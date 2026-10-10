"""方向光阴影贴图（Phase 2）。

## 数据流

1. :meth:`ShadowMap.begin` —— 绑定 depth-only FBO、清深度、关闭混合（颜色附件是
   ``GL_NONE``，所以不用管 glColorMask）
2. 用主着色器 ``fuc = SHADOW(8)`` 把**不透明物体**重画一遍：顶点走
   ``u_lightSpaceMatrix * 模型矩阵``，片元只驱动深度写入
3. :meth:`ShadowMap.end` —— 恢复 framebuffer / viewport / 渲染状态
4. :meth:`ShadowMap.upload` —— 把 ``u_shadowMap0`` / ``u_lightSpaceMatrix`` /
   采样参数交给 dis.frag

采样端在 ``dis.frag`` 的 ``shadowFactor()``：3×3 PCF + normal offset（沿法线把
采样点推离表面，斜率越大推得越多）。这比单纯加大深度偏置更不容易出现自阴影
痤疮，代价是薄物体会有轻微漏光。

## 为什么是"相机前方一块正交区域"而不是 CSM

最小可用方案，所有参数集中在 :meth:`computeLightSpaceMatrix`（纯函数，可以脱离
GL 单测）。要升级成 CSM，只需按视锥分段调用它多次、结果塞进 ``sampler2DArray``：
采样端的 uniform 名与 PCF 代码都不用变。
"""

import glm
from OpenGL.GL import *

from . import RenderState
from ..RenderGlobal import RenderGlobal


class ShadowMap:
    # 阴影贴图固定使用纹理单元 6：
    #   0..5 被 dis.frag 的 tex0..tex5（颜色 / 贴图 / 光照图）占用，
    #   WBOIT 的 accum/reveal/合成用 1..3（但那是在它自己的 program 里）。
    TEXTURE_UNIT = 6

    def __init__(self, size: int = 2048, ortho_radius: float = 45.0,
                 distance: float = 140.0, depth_padding: float = 80.0,
                 bias: float = 0.0022) -> None:
        self.size = int(size)
        self.ortho_radius = float(ortho_radius)
        self.distance = float(distance)
        self.depth_padding = float(depth_padding)
        self.bias = float(bias)
        self.texel = 1.0 / float(self.size)
        # 一个纹素在世界空间有多大（法线偏移用世界单位才可控）
        self.texel_world = 2.0 * self.ortho_radius / float(self.size)
        # 法线偏移 = 纹素世界尺寸 × 这个倍数（越大越不容易自阴影，但薄物体漏光更明显）
        self.normal_offset_scale = 2.5
        self.light_space_matrix = glm.mat4(1.0)
        self._saved_framebuffer = 0
        self._saved_viewport = None
        self._released = False

        self.texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.texture)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_DEPTH_COMPONENT24, self.size,
                     self.size, 0, GL_DEPTH_COMPONENT, GL_FLOAT, None)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        # 贴图之外一律返回深度 1.0（= 最远）→ 采样端判为"被照亮"，
        # 不会在阴影范围边缘出现一圈假的暗边
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_BORDER)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_BORDER)
        glTexParameterfv(GL_TEXTURE_2D, GL_TEXTURE_BORDER_COLOR,
                         (1.0, 1.0, 1.0, 1.0))

        self.framebuffer = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                               GL_TEXTURE_2D, self.texture, 0)
        # 只写深度：没有颜色附件（比 glColorMask(0,0,0,0) 更彻底）
        glDrawBuffer(GL_NONE)
        glReadBuffer(GL_NONE)
        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"阴影贴图帧缓冲不完整 (status=0x{status:x})")
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glBindTexture(GL_TEXTURE_2D, 0)

    # === 光空间矩阵（纯数学部分可脱离 GL 单测）===

    @staticmethod
    def cameraBasis(view: glm.mat4) -> glm.vec3:
        """从 view 矩阵取出世界空间前向（glm 是列主序：``mat[col][row]``）。

        视图矩阵的行是相机基：row0=right、row1=up、row2=-forward。
        """
        forward = glm.vec3(-view[0][2], -view[1][2], -view[2][2])
        length = glm.length(forward)
        if length < 1e-6:
            return glm.vec3(0.0, 0.0, -1.0)
        return forward / length

    @staticmethod
    def computeLightSpaceMatrix(eye, forward, light_direction,
                                ortho_radius: float, distance: float,
                                depth_padding: float) -> glm.mat4:
        """正交光空间矩阵 = 光投影 × 光视图。

        - 观察区域以"相机前方 ortho_radius/2 处"为中心（兼顾近处与中景）
        - 光源眼点沿光线反方向退 ``distance``，正交范围 ``[-r, r]²``
        - 近平面 0：光源眼点与中心之间的几何体也会被写进贴图
        """
        center = glm.vec3(eye) + glm.vec3(forward) * (float(ortho_radius) * 0.5)
        direction = glm.vec3(light_direction)
        if glm.length(direction) < 1e-6:
            direction = glm.vec3(0.0, -1.0, 0.0)
        direction = glm.normalize(direction)
        up = glm.vec3(0.0, 1.0, 0.0)
        if abs(direction.y) > 0.99:
            # 太阳几乎垂直时 lookAt 的 up 不能与视线共线
            up = glm.vec3(0.0, 0.0, 1.0)
        light_eye = center - direction * float(distance)
        light_view = glm.lookAt(light_eye, center, up)
        light_projection = glm.ortho(-float(ortho_radius), float(ortho_radius),
                                     -float(ortho_radius), float(ortho_radius),
                                     0.0, float(distance) + float(depth_padding))
        return light_projection * light_view

    def updateLightSpaceMatrix(self, camera, light) -> glm.mat4:
        """按相机位置/朝向 + 光源方向刷新光空间矩阵。"""
        view = None
        model = getattr(camera, "m", None)
        if model is not None:
            view = getattr(model, "view", None)
        forward = self.cameraBasis(view if view is not None else glm.mat4(1.0))
        eye = glm.vec3(camera.x, camera.y, camera.z)
        self.light_space_matrix = self.computeLightSpaceMatrix(
            eye, forward, light.direction, self.ortho_radius, self.distance,
            self.depth_padding)
        return self.light_space_matrix

    # === pass ===

    def begin(self) -> None:
        """进入阴影 pass：保存当前 framebuffer/viewport，只启用深度写入。"""
        self._saved_framebuffer = glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING)
        viewport = glGetIntegerv(GL_VIEWPORT)
        self._saved_viewport = tuple(int(value) for value in viewport)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        glViewport(0, 0, self.size, self.size)
        # 深度清除受 glDepthMask 影响，所以先让 RenderState 落到"写深度 + 不混合"
        RenderState.setDefaults(blend_mode=RenderState.BLEND_NONE,
                                depth_write=True, depth_test=True)
        glClear(GL_DEPTH_BUFFER_BIT)

    def end(self) -> None:
        """退出阴影 pass：恢复 framebuffer/viewport 与不透明 pass 的基准状态。"""
        glBindFramebuffer(GL_FRAMEBUFFER, self._saved_framebuffer or 0)
        if self._saved_viewport:
            glViewport(*self._saved_viewport)
        # 本 pass 直接改过混合/深度写入，缓存必须作废后重新下发，
        # 否则后面的不透明绘制会带着"关闭混合"的状态（同 WBOIT 的坑）
        RenderState.invalidate()
        RenderState.setDefaults()

    def uploadLightSpace(self, shader) -> None:
        """只上传光空间变换相关 uniform —— 阴影 pass **绘制之前**必须先调用它。"""
        shader.uniformMatrix4fv("u_lightSpaceMatrix", self.light_space_matrix)
        shader.uniform1f("u_shadowTexel", self.texel)
        shader.uniform1f("u_shadowBias", self.bias)
        shader.uniform1f("u_shadowNormalOffset",
                         self.texel_world * self.normal_offset_scale)

    def upload(self, shader, light_index: int) -> None:
        """把阴影贴图与采样参数交给主着色器（颜色 pass 用）。"""
        # 采样器 uniform 属于 program 状态，调用方需要先 use() 主程序
        self.uploadLightSpace(shader)
        shader.uniform1i("u_shadowLight", int(light_index))
        glActiveTexture(GL_TEXTURE0 + self.TEXTURE_UNIT)
        glBindTexture(GL_TEXTURE_2D, self.texture)
        shader.uniform1i("u_shadowMap0", self.TEXTURE_UNIT)
        glActiveTexture(GL_TEXTURE0)

    def release(self) -> None:
        """释放 GPU 资源（需要在 GL 上下文里调用）。"""
        if self._released:
            return
        self._released = True
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        if self.framebuffer:
            glDeleteFramebuffers(1, [self.framebuffer])
            self.framebuffer = 0
        if self.texture:
            glDeleteTextures(1, [self.texture])
            self.texture = 0
