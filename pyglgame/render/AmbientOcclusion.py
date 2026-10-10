"""SSAO（屏幕空间环境光遮蔽，Phase 3）。

## 为什么要"深度预 pass"

AO 要在**光照的时候**用（只压低间接光），但 AO 又需要不透明几何的深度 ——
所以顺序是：

1. 相机 `renderOcclusion()` 先用**只写深度**的方式把不透明物体画一遍
   （``glColorMask(false...)``，复用 DIS 路径，不需要新 fuc）
2. 把深度解析成可采样纹理（MSAA 时 ``depth_resolve=True`` + blit）
3. **清掉深度**：不透明 pass 用的是 ``GL_LESS``，深度不清掉的话第二步画出来的
   物体在第三步会被自己剔除（同"深度清除受 glDepthMask 影响"那个坑的逻辑）
4. 半分辨率算 AO + 双边模糊，结果接到 ``dis.frag`` 的 ``u_aoTex``

## 输入 / 输出

- 输入：相机深度纹理 + 投影/逆投影矩阵（视空间重建）
- 输出：``GL_R8`` 的单通道纹理（1 = 不遮蔽），用纹理单元 7（阴影在 6）
- 法线是重建出来的（视空间位置叉积），因此**不需要法线缓冲**；代价是边缘会
  有一点脏，双边模糊 + 距离衰减能压住

## 与体积渲染的关系

第 1、2 步（深度预 pass + 可采样深度）正是体积雾/体积光需要的输入，
所以 Phase 4 直接复用同一条链路，不需要再改一遍相机。
"""

import glm
from OpenGL.GL import *

from .RenderBuffer import RenderBuffer
from . import RenderState
from ..RenderGlobal import RenderGlobal
from ..shader.ShaderManager import ShaderManager
from ..tools.glDebug import currentContext, isSameContext
from ..xyazhServer.ConsoleMessage import ConsoleMessage

# 采样核上限（与 ssao.frag 里的 `uniform vec3 u_kernel[16]` 一致）
MAX_KERNEL = 16


class AmbientOcclusion:
    # AO 纹理固定用纹理单元 7：0..5 是 dis.frag 的 tex0..tex5，6 是阴影贴图
    TEXTURE_UNIT = 7

    def __init__(self, width: int, height: int, scale: float = 0.5,
                 samples: int = MAX_KERNEL, radius: float = 1.5,
                 intensity: float = 1.0, power: float = 1.4,
                 bias: float = 0.08, blur_depth_scale: float = 3.0) -> None:
        self.scale = float(scale)
        self.width = max(1, int(width * self.scale))
        self.height = max(1, int(height * self.scale))
        self.samples = int(min(samples, MAX_KERNEL))
        if samples > MAX_KERNEL:
            ConsoleMessage.printWarning(
                f"SSAO 采样数 {samples} 超过上限 {MAX_KERNEL}，已截断")
        self.radius = float(radius)
        self.intensity = float(intensity)
        self.power = float(power)
        self.bias = float(bias)
        self.blur_depth_scale = float(blur_depth_scale)
        self.program = None
        self.kernel = self._buildKernel(self.samples)
        self.render_buffer = RenderBuffer.getWindowRenderBuffer()
        self._context = currentContext()
        self._released = False

        # 半分辨率：先算 ao_texture，再双边模糊到 blur_texture
        self.ao_texture = 0
        self.blur_texture = 0
        self.compute_fbo = 0
        self.blur_fbo = 0
        self._allocate()

    def _allocate(self) -> None:
        self.ao_texture = self._createTexture()
        self.blur_texture = self._createTexture()
        self.compute_fbo = self._createFramebuffer(self.ao_texture)
        self.blur_fbo = self._createFramebuffer(self.blur_texture)

    # === 构建 ===

    @staticmethod
    def _buildKernel(samples: int) -> list:
        """半球采样核（固定种子：保证同一份代码每次都一样，方便截图对比）。

        长度随机分布在 [0.35, 1]、方向在 z>=0 半球内；再按 ``i²`` 缩放，
        让靠近中心的样本更密集（近处的遮蔽对观感影响最大）。
        """
        import random
        rng = random.Random(20260920)
        kernel = []
        for index in range(samples):
            direction = glm.vec3(rng.uniform(-1.0, 1.0),
                                 rng.uniform(-1.0, 1.0),
                                 rng.uniform(0.0, 1.0))
            if glm.length(direction) < 1e-5:
                direction = glm.vec3(0.0, 0.0, 1.0)
            direction = glm.normalize(direction)
            length = rng.uniform(0.35, 1.0)
            scale = 0.1 + 0.9 * ((index + 1) / float(samples)) ** 2
            kernel.append(direction * (length * scale))
        return kernel

    def _createTexture(self) -> int:
        texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, texture)
        glTexStorage2D(GL_TEXTURE_2D, 1, GL_R8, self.width, self.height)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glBindTexture(GL_TEXTURE_2D, 0)
        return texture

    def _createFramebuffer(self, texture: int) -> int:
        framebuffer = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, framebuffer)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, texture, 0)
        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"SSAO 帧缓冲不完整 (status=0x{status:x})")
        return framebuffer

    def getProgram(self):
        if self.program is None:
            self.program = ShaderManager.loadShader("ssao")
            # 通用绘制路径（RenderBuffer.draw）会设置主着色器的 uniform，
            # 本程序里不存在这些名字 —— 预期行为，不刷警告
            self.program.silenceUniform(
                "formatType", "fuc", "tex0", "win_wh", "wh", "translate",
                "rotate", "scale", "view", "u_cameraPos", "u_lightCount")
        return self.program

    # === pass ===

    def compute(self, camera, depth_texture: int) -> int:
        """算一遍 SSAO（半分辨率 + 双边模糊），返回可直接采样的 AO 纹理。

        本方法会把 framebuffer/viewport 切来切去，**调用方结束后要自己重新绑定
        相机帧缓冲**（相机在 :meth:`Camera.renderOcclusion` 里做这件事）。
        """
        if not depth_texture:
            return 0
        projection, inv_projection = self._projectionMatrices(camera)
        program = self.getProgram()
        program.use()
        # 全屏后处理：不要深度测试/混合，也不要写深度
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_BLEND)
        glDepthMask(GL_FALSE)

        # 1) 采样（半分辨率）
        glBindFramebuffer(GL_FRAMEBUFFER, self.compute_fbo)
        glViewport(0, 0, self.width, self.height)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, depth_texture)
        program.uniform1i("u_depth", 0)
        program.uniform1i("u_mode", 0)
        program.uniformMatrix4fv("u_projection", projection)
        program.uniformMatrix4fv("u_invProjection", inv_projection)
        self._uploadKernel(program)
        self.render_buffer.draw(False)

        # 2) 双边模糊（深度做权重，避免跨物体边缘糊开）
        glBindFramebuffer(GL_FRAMEBUFFER, self.blur_fbo)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self.ao_texture)
        program.uniform1i("u_ao", 0)
        program.uniform1i("u_mode", 1)
        program.uniform2f("u_aoTexel", 1.0 / self.width, 1.0 / self.height)
        program.uniform1f("u_blurDepthScale", self.blur_depth_scale)
        self.render_buffer.draw(False)

        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glActiveTexture(GL_TEXTURE0)
        # 直接改过 GL 状态：作废缓存 + **真正把 pass 默认状态下发一遍**。
        # 只 invalidate 是不够的：本 pass 关掉了深度测试/混合/深度写入，
        # 而 RenderLayer 只在**各 pass 之前**设过一次默认状态 —— 不复位的话
        # 紧随其后的不透明 pass 会在"无深度测试 + 不写深度"下绘制：
        #   近处的物体挡不住远处的物体（画序决定遮挡），深度纹理也全是清屏值。
        RenderState.invalidate()
        RenderState.setDefaults()
        # 还原主着色器：否则调用方后续的 uniform 会落到本程序上
        # （表现为 GL_INVALID_OPERATION：glUniform1i 的类型和该 program 的 location 对不上）
        render_global = RenderGlobal.instance
        if render_global is not None and render_global.dis_shader is not None:
            render_global.dis_shader.use()
        return self.blur_texture

    @staticmethod
    def _projectionMatrices(camera):
        """取相机的投影矩阵与逆矩阵（视空间重建要用）。"""
        model = getattr(camera, "m", None)
        projection = getattr(model, "projection", None)
        if projection is None:
            projection = glm.mat4(1.0)
        return projection, glm.inverse(projection)

    def _uploadKernel(self, program) -> None:
        program.uniform1i("u_samples", len(self.kernel))
        program.uniform1f("u_radius", self.radius)
        program.uniform1f("u_bias", self.bias)
        program.uniform1f("u_intensity", self.intensity)
        program.uniform1f("u_power", self.power)
        for index, value in enumerate(self.kernel):
            program.uniform3f(f"u_kernel[{index}]", value.x, value.y, value.z)

    def upload(self, shader, strength: float = 1.0,
               enabled: bool = True) -> None:
        """把 AO 结果接到主着色器（不透明与半透明 pass 都会用到）。"""
        shader.uniform1i("u_aoEnabled", 1 if enabled else 0)
        shader.uniform1f("u_aoStrength", float(strength))
        if not enabled:
            return
        glActiveTexture(GL_TEXTURE0 + self.TEXTURE_UNIT)
        glBindTexture(GL_TEXTURE_2D, self.blur_texture)
        shader.uniform1i("u_aoTex", self.TEXTURE_UNIT)
        glActiveTexture(GL_TEXTURE0)

    # === 生命周期 ===

    def resize(self, width: int, height: int) -> bool:
        """窗口尺寸变化时重建半分辨率缓冲。返回是否真的重建了。"""
        new_width = max(1, int(width * self.scale))
        new_height = max(1, int(height * self.scale))
        if (new_width, new_height) == (self.width, self.height):
            return False
        self._deleteTargets()
        self.width = new_width
        self.height = new_height
        self._allocate()
        return True

    def _deleteTargets(self) -> None:
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        for framebuffer in (self.compute_fbo, self.blur_fbo):
            if framebuffer:
                glDeleteFramebuffers(1, [framebuffer])
        self.compute_fbo = self.blur_fbo = 0
        for texture in (self.ao_texture, self.blur_texture):
            if texture:
                glDeleteTextures(1, [texture])
        self.ao_texture = self.blur_texture = 0

    def release(self) -> None:
        """释放 GPU 资源（需要在 GL 上下文里调用）。"""
        if self._released:
            return
        self._released = True
        if not isSameContext(self._context):
            self.compute_fbo = self.blur_fbo = 0
            self.ao_texture = self.blur_texture = 0
            return
        self._deleteTargets()
        # 程序由 ShaderManager 管理，这里只解除引用
        self.program = None
