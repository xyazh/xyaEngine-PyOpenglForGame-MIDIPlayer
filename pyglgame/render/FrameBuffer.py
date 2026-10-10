from typing import Any, Callable

from OpenGL.GL import *
from ..RenderGlobal import RenderGlobal, backgroundColor
from ..tools.glDebug import currentContext, isSameContext
from .RenderBuffer import *

class FrameBuffer:
    def __init__(self, width: int, height: int, use_depth: bool = False, param: int = GL_NEAREST, data: list = None) -> None:
        self.width = width
        self.height = height
        self.size = RenderGlobal.instance.window.size
        self.use_depth = use_depth
        # 创建帧缓冲对象
        self.framebuffer = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)

        # 创建颜色纹理附件
        self.texture_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA32F, width, height,
                     0, GL_RGBA, GL_FLOAT, data)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, param)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, param)
        glFramebufferTexture2D(
            GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, self.texture_id, 0)

        if use_depth:
            glEnable(GL_DEPTH_TEST)  # 启用深度测试
            glEnable(GL_STENCIL_TEST)  # 启用模板测试
            # 创建深度纹理附件
            self.depth_texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.depth_texture_id)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_DEPTH_COMPONENT24,
                         width, height, 0, GL_DEPTH_COMPONENT, GL_FLOAT, None)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glFramebufferTexture2D(
                GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_TEXTURE_2D, self.depth_texture_id, 0)

        # 检查帧缓冲是否完整
        if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("Framebuffer is not complete")

        # 解绑帧缓冲
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        self.is_dead = False
        self._context = currentContext()
        self.render_buffer = RenderBuffer(GL_STATIC_DRAW)
        self.buf_builder = self.render_buffer.createBuffer(
            GL_TRIANGLES, POS | TEX)
        self.buf_builder.pos(-1, -1, 0).tex(0, 0).end()  # bottom left
        self.buf_builder.pos(+1, -1, 0).tex(1, 0).end()   # bottom right
        self.buf_builder.pos(-1, +1, 0).tex(0, 1).end()   # top left
        self.buf_builder.pos(+1, -1, 0).tex(1, 0).end()   # bottom right
        self.buf_builder.pos(+1, +1, 0).tex(1, 1).end()    # top right
        self.buf_builder.pos(-1, +1, 0).tex(0, 1).end()   # top left

    def __del__(self) -> None:
        try:
            self.cleanup()
        except Exception:
            pass

    def bindTexture(self) -> None:
        """绑定纹理对象"""
        glBindTexture(GL_TEXTURE_2D, self.texture_id)

    def bindDepthTexture(self) -> None:
        """绑定深度纹理对象"""
        glBindTexture(GL_TEXTURE_2D, self.depth_texture_id)

    def bind(self) -> None:
        """绑定帧缓冲对象"""
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)

    def unbind(self) -> None:
        """解绑帧缓冲对象"""
        glBindFramebuffer(GL_FRAMEBUFFER, 0)

    def drawStart(self) -> None:
        """将当前帧缓冲对象绑定为渲染目标，并绘制到纹理"""
        self.bind()
        glViewport(0, 0, self.width, self.height)
        # 天空色 = RenderGlobal.bg（alpha 仍为 0，见 RenderGlobal.backgroundColor）
        glClearColor(*backgroundColor())
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT |
                GL_STENCIL_BUFFER_BIT)
        if self.use_depth:
            glEnable(GL_DEPTH_TEST)  # 启用深度测试
            glEnable(GL_STENCIL_TEST)  # 启用模板测试

    def draw(self, fuc: Callable, *args: Any, **kw: Any) -> None:
        """绑定本 FBO 为渲染目标 → 跑一遍 ``fuc(*args, **kw)`` → 解绑。"""
        self.drawStart()
        fuc(*args, **kw)
        self.drawEnd()

    def drawEnd(self) -> None:
        """将当前帧缓冲对象解绑"""
        self.unbind()
        glViewport(0, 0, self.size.w, self.size.h)

    def cleanup(self) -> None:
        """清理帧缓冲对象及其资源"""
        if self.is_dead:
            return
        self.is_dead = True
        if not isSameContext(self._context):
            self.framebuffer = self.texture_id = 0
            self.depth_texture_id = 0
            return
        glDeleteFramebuffers(1, [self.framebuffer])
        self.framebuffer = 0
        glDeleteTextures(1, [self.texture_id])
        self.texture_id = 0
        if self.use_depth:
            glDeleteTextures(1, [self.depth_texture_id])
            self.depth_texture_id = 0

    def drawToWindow(self) -> None:
        """将当前帧缓冲对象绘制到窗口"""
        self.bindTexture()
        RenderGlobal.instance.using_shader.uniform1i("fuc", 1)
        self.render_buffer.draw()
