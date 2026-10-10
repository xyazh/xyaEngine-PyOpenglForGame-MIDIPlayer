"""WBOIT（加权混合顺序无关透明 / Weighted Blended OIT）。

参考：Morgan McGuire & Louis Bavoil, "Weighted Blended Order-Independent
Transparency" (JCGT 2013)。

为什么需要它：引擎的半透明内容大量是**同一批顶点里互相重叠的四边形**
（音符拖尾、粒子、网格线），对象级排序（A 方案）无法解决批内顺序问题。
WBOIT 把每个半透明片元按"权重"累加，最后一次性合成，**与绘制顺序完全无关**。

数据流：

    accum (RGBA16F)  += vec4(color.rgb * a * w, a * w)   blend: ONE / ONE
    reveal(R16F)     *= (1 - a)                          blend: ZERO / ONE_MINUS_SRC_COLOR
    composite        color = accum.rgb / max(accum.a, 1e-5), alpha = 1 - reveal

MSAA（重要）：accum/reveal 也是**逐采样**渲染的 —— ``samples > 1`` 时它们用
多重采样纹理，解析成单采样纹理后再合成；否则半透明内容（在很多场景里就是
全部内容）会完全没有抗锯齿。代价是这两张缓冲的显存随采样数线性增长，而且
**必须与帧缓冲同采样数**才能直接共用它的深度附件，所以
:data:`WBOIT.MAX_SAMPLES` 会给帧缓冲的 MSAA 设一个上限。

实现要点：
- 两个颜色附件用 ``glBlendFunci`` 分别设置混合（GL 4.0+）
- 清屏必须用 ``glClearBufferfv`` 逐附件清（accum 清 0，reveal 清 1）
- 深度：直接复用相机帧缓冲的深度附件（多重采样时是深度 renderbuffer，
  单采样时是深度纹理）做**深度测试**，不写深度：半透明被不透明几何正确
  遮挡，但半透明之间不再互相遮挡。复用深度附件还省掉了每帧一次深度解析。
"""

import ctypes

from OpenGL.GL import *

from .RenderBuffer import RenderBuffer
from . import RenderState
from ..RenderGlobal import RenderGlobal
from ..shader.ShaderManager import ShaderManager


class WBOIT:
    ACCUM_FORMAT = GL_RGBA16F
    REVEAL_FORMAT = GL_R16F
    # accum/reveal 的采样数上限。帧缓冲必须与它们同采样数（要共用深度附件），
    # 所以开启 WBOIT 时会把帧缓冲的 MSAA 也压到这个值以内。
    # 1711x1048 下 4x 约 129MB（accum 57 + reveal 14 + 帧缓冲颜色 57），
    # 8x 约 259MB；显存宽裕可以调大。
    MAX_SAMPLES = 32

    def __init__(self, width: int, height: int, samples: int = 1,
                 composite_format: int = None) -> None:
        self.width = int(width)
        self.height = int(height)
        self.samples = max(1, int(samples))
        self.composite_format = (GL_RGBA16F if composite_format is None
                                 else composite_format)
        self.depth_texture = 0
        self.depth_is_renderbuffer = False
        self.composited = False
        self.resolve_shader = None
        self.alpha_floor = 0.0
        self.depth_scale = 1.0 / 200.0

        # 单采样累加纹理：合成时采样它们；MSAA 时它们同时是解析目标
        self.accum_texture = self._createTexture(self.ACCUM_FORMAT)
        self.reveal_texture = self._createTexture(self.REVEAL_FORMAT)
        self.resolve_fbo = 0
        self.ms_accum_texture = 0
        self.ms_reveal_texture = 0

        # 半透明累加缓冲：两个颜色附件
        self.framebuffer = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        if self.samples > 1:
            # 逐采样渲染：半透明同样有 MSAA 边缘
            self.ms_accum_texture = self._createMultisampleTexture(
                self.ACCUM_FORMAT)
            self.ms_reveal_texture = self._createMultisampleTexture(
                self.REVEAL_FORMAT)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D_MULTISAMPLE,
                                   self.ms_accum_texture, 0)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT1,
                                   GL_TEXTURE_2D_MULTISAMPLE,
                                   self.ms_reveal_texture, 0)

            # 解析目标（单采样）
            self.resolve_fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, self.resolve_fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, self.accum_texture, 0)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT1,
                                   GL_TEXTURE_2D, self.reveal_texture, 0)
            glDrawBuffers(2, [GL_COLOR_ATTACHMENT0, GL_COLOR_ATTACHMENT1])
            self._checkComplete("WBOIT accum resolve")

            glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        else:
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, self.accum_texture, 0)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT1,
                                   GL_TEXTURE_2D, self.reveal_texture, 0)
        glDrawBuffers(2, [GL_COLOR_ATTACHMENT0, GL_COLOR_ATTACHMENT1])
        self._checkComplete("WBOIT accum/reveal")

        # 合成目标（1 个颜色附件，无深度）
        self.color_texture = self._createTexture(self.composite_format)
        self.composite_fbo = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.composite_fbo)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, self.color_texture, 0)
        glDrawBuffers(1, [GL_COLOR_ATTACHMENT0])
        self._checkComplete("WBOIT composite")

        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        self.render_buffer = RenderBuffer.getWindowRenderBuffer()

    # === 内部工具 ===

    def _createTexture(self, internal_format: int) -> int:
        texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, texture)
        glTexStorage2D(GL_TEXTURE_2D, 1, internal_format, self.width, self.height)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glBindTexture(GL_TEXTURE_2D, 0)
        return texture

    def _createMultisampleTexture(self, internal_format: int) -> int:
        texture = glGenTextures(1)
        # 多重采样纹理不能设置 FILTER/WRAP，且必须绑定到 *_MULTISAMPLE 目标
        glBindTexture(GL_TEXTURE_2D_MULTISAMPLE, texture)
        glTexImage2DMultisample(GL_TEXTURE_2D_MULTISAMPLE, self.samples,
                                internal_format, self.width, self.height,
                                GL_TRUE)
        glBindTexture(GL_TEXTURE_2D_MULTISAMPLE, 0)
        return texture

    @staticmethod
    def _checkComplete(tag: str) -> None:
        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"{tag} 帧缓冲不完整 (status=0x{status:x})")

    def getResolveShader(self):
        if self.resolve_shader is None:
            self.resolve_shader = ShaderManager.loadShader("oit_resolve")
            # 合成程序不使用场景 uniform（formatType/fuc/tex0/矩阵等），
            # 通用绘制路径会尝试设置它们 —— 预期行为，不刷警告
            self.resolve_shader.silenceUniform(
                "formatType", "fuc", "tex0", "win_wh", "wh",
                "translate", "rotate", "scale", "projection", "view")
        return self.resolve_shader

    # === 半透明 pass ===

    def attachDepth(self, attachment: int, is_renderbuffer: bool = False) -> None:
        """挂上相机帧缓冲的深度附件（半透明 pass 只读它做深度测试）。

        - ``samples > 1``：必须是**多重采样深度 renderbuffer**（采样数要和
          accum/reveal 一致），直接共用相机的那个，省掉每帧一次深度解析
        - ``samples <= 1``：用相机的深度纹理
        """
        if (attachment, is_renderbuffer) == (self.depth_texture,
                                             self.depth_is_renderbuffer):
            return
        self.depth_texture = attachment
        self.depth_is_renderbuffer = is_renderbuffer
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        # 深度 renderbuffer 通常是 DEPTH24_STENCIL8，按 DEPTH_STENCIL 挂
        point = (GL_DEPTH_STENCIL_ATTACHMENT if is_renderbuffer
                 else GL_DEPTH_ATTACHMENT)
        if not attachment:
            glFramebufferTexture2D(GL_FRAMEBUFFER, point, GL_TEXTURE_2D, 0, 0)
            if is_renderbuffer:
                glFramebufferRenderbuffer(GL_FRAMEBUFFER, point,
                                          GL_RENDERBUFFER, 0)
        elif is_renderbuffer:
            glFramebufferRenderbuffer(GL_FRAMEBUFFER, point,
                                      GL_RENDERBUFFER, attachment)
        else:
            glFramebufferTexture2D(GL_FRAMEBUFFER, point, GL_TEXTURE_2D,
                                   attachment, 0)
        self._checkComplete("WBOIT depth attach")

    def bind(self) -> None:
        """开始半透明 pass：绑定累加缓冲、逐附件清屏、设置 per-buffer 混合。"""
        RenderState.invalidate()
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        glViewport(0, 0, self.width, self.height)
        # accum 清 0、reveal 清 1（必须逐附件清；多重采样时清掉所有采样点）
        glClearBufferfv(GL_COLOR, 0, (ctypes.c_float * 4)(0.0, 0.0, 0.0, 0.0))
        glClearBufferfv(GL_COLOR, 1, (ctypes.c_float * 4)(1.0, 0.0, 0.0, 0.0))
        glEnable(GL_DEPTH_TEST)
        glDepthMask(GL_FALSE)          # 只读深度：半透明之间不再互相遮挡
        glEnable(GL_BLEND)
        glBlendFunci(0, GL_ONE, GL_ONE)                               # accum +=
        glBlendFunci(1, GL_ZERO, GL_ONE_MINUS_SRC_COLOR)              # reveal *=
        self.composited = False

    def _resolveAccum(self) -> None:
        """把多重采样 accum/reveal 解析成单采样（合成要采样它们）。"""
        glBindFramebuffer(GL_READ_FRAMEBUFFER, self.framebuffer)
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self.resolve_fbo)
        for attachment in (GL_COLOR_ATTACHMENT0, GL_COLOR_ATTACHMENT1):
            glReadBuffer(attachment)
            glDrawBuffer(attachment)
            glBlitFramebuffer(
                0, 0, self.width, self.height,
                0, 0, self.width, self.height,
                GL_COLOR_BUFFER_BIT, GL_NEAREST)
        glReadBuffer(GL_COLOR_ATTACHMENT0)
        glDrawBuffer(GL_COLOR_ATTACHMENT0)

    def resolve(self, color_texture: int) -> int:
        """合成：先解析多重采样累加缓冲，再混到不透明画面上，返回最终纹理。"""
        if self.samples > 1 and self.resolve_fbo:
            self._resolveAccum()
        shader = self.getResolveShader()
        shader.use()
        glBindFramebuffer(GL_FRAMEBUFFER, self.composite_fbo)
        glViewport(0, 0, self.width, self.height)
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_BLEND)
        glDepthMask(GL_FALSE)

        glActiveTexture(GL_TEXTURE1)
        glBindTexture(GL_TEXTURE_2D, self.accum_texture)
        shader.uniform1i("u_accum", 1)
        glActiveTexture(GL_TEXTURE2)
        glBindTexture(GL_TEXTURE_2D, self.reveal_texture)
        shader.uniform1i("u_reveal", 2)
        glActiveTexture(GL_TEXTURE3)
        glBindTexture(GL_TEXTURE_2D, color_texture)
        shader.uniform1i("u_color", 3)
        shader.uniform1f("u_alphaFloor", self.alpha_floor)

        self.render_buffer.draw(False)
        glActiveTexture(GL_TEXTURE0)
        self.composited = True
        RenderState.invalidate()
        # 还原主着色器：否则后续的 uniform 设置会落到 resolve 程序上
        # （表现为一堆 "不存在 uniform" 警告，且真正的 dis 程序拿不到 formatType）
        render_global = RenderGlobal.instance
        if render_global.dis_shader is not None:
            render_global.dis_shader.use()
        return self.color_texture

    # === 生命周期 ===

    def release(self) -> None:
        """释放所有资源（需要在 GL 上下文里调用）。"""
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        for fbo in (self.framebuffer, self.resolve_fbo, self.composite_fbo):
            if fbo:
                glDeleteFramebuffers(1, [fbo])
        self.framebuffer = self.resolve_fbo = self.composite_fbo = 0
        for texture in (self.accum_texture, self.reveal_texture,
                        self.ms_accum_texture, self.ms_reveal_texture,
                        self.color_texture):
            if texture:
                glDeleteTextures(1, [texture])
        self.accum_texture = self.reveal_texture = self.color_texture = 0
        self.ms_accum_texture = self.ms_reveal_texture = 0
        if self.resolve_shader is not None:
            # 程序由 ShaderManager 管理，这里只解除引用
            self.resolve_shader = None
