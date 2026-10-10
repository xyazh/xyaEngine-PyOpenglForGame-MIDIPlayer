"""体积渲染（体积雾 / 体积光 / god rays，Phase 4）。

## 为什么它现在能"免费"接上

体积散射需要三样东西，前三个阶段已经全部备好：

1. **可采样深度**（Phase 3 的深度预 pass + depth_resolve）→ 确定每条射线走到哪
2. **方向光的阴影贴图**（Phase 2）→ 射线上每个采样点查一次，被挡住的地方散射为 0，
   这就是体积阴影 / god rays
3. **HDR 颜色缓冲**（引擎本来就是 RGBA16F）→ 散射是**加性 HDR 项**，必须在
   tone mapping 之前叠加，所以这个 pass 在"半透明之后、Bloom/tonemap 之前"

## 两趟（半分辨率 + 全分辨率合成）

- 第 1 趟（``u_mode = 0``，半分辨率）：射线步进，输出 ``vec4(散射, 透过率)``
- 第 2 趟（``u_mode = 1``，全分辨率）：``scene * T + scattered``

半分辨率是标准做法：体积雾是低频信号，半分辨率几乎看不出差别，性能翻 4 倍。
逐帧抖动步进起点把条带打散成噪声（``u_frameIndex`` 由 ``RenderGlobal.frame_index`` 提供）。

## 三趟之外：Phase 5 的"雾层模式"（干冰雾）

``fog_layer=True`` + 一张 `TextureVolumeNoise` 时，密度不再是"只跟高度有关"，
而是

    密度 = 高度剖面(顶面可被噪声抬成起伏的云顶) × 3D 噪声(三层错动+域扭曲 + 阈值化)

五个关键点（都在 ``volumetric.frag`` 里）：

1. **平板步进**：``fogSlab()`` 求射线与 [fog_bottom, fog_top] 的交，只步进那一段
   —— 40 步铺在 2 米高的雾里，比铺在整条射线上细一个数量级，顶面边缘才"分明"。
2. **阈值化**：``edge`` / ``edge_softness`` / ``sharpen`` 把噪声压成有边界的团块，
   不做阈值就是一坨均匀灰雾（这就是"干冰"和"雾霾"的差别）。
3. **三层错动 + 域扭曲（去周期）**：贴图是可平铺的，**固有周期 = 1/noise_scale**
   （0.2 → 5 米）。只采一层时整片雾每 5 米原样复制一次，一眼就能认出"同一团雾又
   出现了"。所以叠三个**互不可通约**的尺度（``noise_tiling``）各绕 y 轴转一个角度，
   再用宽层做低频域扭曲（``noise_warp``，单位米）：合成周期 = 各层公倍数 ≫ 雾池
   本身。``wind`` / ``wind2`` + 第三个流向让三层不是一起平移（否则像整体漂移）。
4. **颗粒感**：贴图侧先砍掉超过奈奎斯特的倍频层（``computeVolumeNoise.comp`` 的
   ``nzOctaveBudget``）—— 那才是"雾面一层粗噪点"的来源（加步数无效）；着色器侧
   再靠 ``noise_grain``（细颗粒权重，0.1 以下就很平滑）和 ``sharpen`` 收尾。
5. **雾顶是有厚度的"壳"**：顶面过渡带（``top_softness``）不能比顶面起伏
   （``fog_top_variation``）小一个数量级，否则雾顶就是一张薄壳，相机俯视不到
   15° 时看到的是它的**侧棱** —— 屏幕上被压成一条条沿视线的拉丝（"雾本身
   一条一条"的主因：加步数、关噪声、关阴影都无效，因为那是壳的几何）。
   着色器里的兜底是 ``max(u_topSoftness, u_fogRange.z * 0.4)``；起伏为 0 时
   不受影响（仍是原来的锐利顶面）。

``emitters`` 是"喷口"（干冰容器口那种），给附近加浓并且带轻微脉动。

## 谁在用它（Phase 4/5 vs Phase 6）

- Phase 4/5：雾是**相机的属性**（``camera.useVolumetric(...)`` + ``volumetric_*``），
  整张地图只有一份，也不能挪动。
- Phase 6：:class:`~pyglgame.gameobject.FogVolume.FogVolume` 把同一套 pass 包成
  **世界里的对象**（可放置 / 可多个 / 可移动 / 可删）—— 它自己持有本类实例并驱动
  :meth:`render`，相机只提供颜色与深度缓冲。``volume_enabled = True`` 时射线只步进
  盒子内那一段，密度再按"到侧面的距离"淡出（见 ``volumetric.frag``）。
  相机的 ``useVolumetric`` 退化成**兼容入口**：内部维护一个"不设盒子"的全局雾。

## 接口（未来扩展）

参数都集中在 :class:`Volumetric` 的属性上（密度 / 高度衰减 / 步数 / 散射强度 /
最远距离 / 雾层那一组 / 世界体积盒那一组），要接云层、体积光柱或参与介质扩展，
只改 ``volumetric.frag`` 的 :func:`march` / :func:`fogDensityAt` 即可，
:class:`FogVolume` 与相机两侧的接口都不用变。
"""

import glm
from OpenGL.GL import *

from .RenderBuffer import RenderBuffer
from . import RenderState
from ..RenderGlobal import RenderGlobal
from ..shader.ShaderManager import ShaderManager
from ..tools.glDebug import currentContext, isSameContext

# 体积渲染程序自己的采样器单元（与主着色器 / SSAO / 阴影互不干扰）
DEPTH_UNIT = 0
COLOR_UNIT = 1
SCATTER_UNIT = 2
SHADOW_UNIT = 3
# Phase 5：3D 噪声（雾层模式用）
NOISE_UNIT = 4
# 喷口上限（必须与 volumetric.frag 的 u_emitter[4] 一致）
MAX_EMITTERS = 4


def _createDummyNoiseTexture() -> int:
    """1×1×1 的占位 3D 纹理（``u_fogLayer == 0`` 时也要绑一个"完整"纹理）。

    绑定纹理 0（不完整纹理）虽然采样不到（那条分支根本不执行），但有些驱动会
    在采样器指向不完整纹理时报 GL_INVALID_OPERATION；给个真纹理就完全没这个风险。
    只在 :meth:`Volumetric.__init__` 里创建 —— 那里一定有 GL 上下文。
    """
    texture = int(glGenTextures(1))
    glBindTexture(GL_TEXTURE_3D, texture)
    glTexImage3D(GL_TEXTURE_3D, 0, GL_RGBA32F, 1, 1, 1, 0, GL_RGBA, GL_FLOAT,
                 [1.0, 1.0, 1.0, 1.0])
    glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE)
    glBindTexture(GL_TEXTURE_3D, 0)
    return texture


class Volumetric:
    """体积散射两趟 pass（可选"雾层模式"见类文档）。

    **雾层参数在这里给一份类属性默认值**：``__init__`` 会把它们覆盖成实例属性；
    而用 ``object.__new__`` 拼出来的实例（测试里的假对象、特殊用法）也能安全走到
    —— 默认值就是"不启用雾层"，正好退化成原来的纯高度雾分支。
    """

    # === 雾层开关与几何 ===
    fog_layer = False
    noise_texture = None
    fog_bottom = -0.5
    fog_top = 2.2
    fog_top_variation = 0.5
    top_softness = 0.35
    # === 3D 噪声 ===
    noise_scale = 0.06
    noise_strength = 0.9
    noise_detail = 0.45
    noise_grain = 0.15
    # 三个附加层的尺度（相对 noise_scale）：(次层, 三层, 宽层)。
    # 必须与 1.0 **不可通约**，否则三层同时对齐 → 合成图案又回到基础周期（见着色器）
    noise_tiling = glm.vec3(0.61, 0.29, 0.13)
    # 低频域扭曲的最大位移（米）：打散贴图固有周期，0 = 关掉（退回"三层错动"）。
    # 量级要和团块尺寸可比（团块 ≈ 1/(noise_scale × 贴图频率)，默认约 8 米）：
    # 太小等于没推（实测只推 0.5 米时自相关只从 0.68 降到 0.50），
    # 1 米量级才真正把周期打散，而且因为位移来自宽层（周期最长的那层），
    # 观感是"整团雾缓缓地拧一下"，不会变糊。
    noise_warp = 1.0
    noise_time = 0.0
    noise_speed = 1.0
    edge = 0.30
    edge_softness = 0.15
    sharpen = 1.6
    # === 流动 / 喷口 ===
    wind = glm.vec2(0.35, 0.12)
    wind2 = glm.vec2(-0.22, 0.30)
    emitters = ()
    emitter_power = 1.5
    # === 雾池范围（xz 平面）：(起始半径, 结束半径)，结束 <= 起始 = 不限范围 ===
    fog_center = glm.vec2(0.0, 0.0)
    fog_spread = glm.vec2(0.0, 0.0)
    # === 世界体积盒（Phase 6：把雾做成可放置的 GameObject，见 FogVolume）===
    # ``volume_enabled = False`` 时下面这组全部不参与 → 与老路径（雾是相机属性）
    # 逐像素一致。True 时射线只步进盒子内那一段，密度再按"到侧面的距离"淡出。
    volume_enabled = False
    volume_min = glm.vec3(0.0)
    volume_max = glm.vec3(0.0)
    # 四个侧面的软边宽度（米）：0 = 硬边（盒子的棱会直接出现在雾上）
    volume_softness = 0.0
    # 噪声锚定：True = 锚定在体积上（体积移动/旋转时图案跟着走）；
    # False = 锚定世界（风把图案吹过体积 —— 大范围静止的雾池用手感更自然）
    volume_anchor = False
    # 噪声采样绕体积中心的旋转（弧度）：几个同参数的体积各转一个角度，
    # 就不会看起来是"同一个贴图复印了几份"
    volume_yaw = 0.0
    paused = False
    # 占位 3D 纹理（`__init__` 里创建；拼出来的实例默认 0，那时也不会采样）
    fallback_noise_texture = 0

    def __init__(self, width: int, height: int, scale: float = 0.5,
                 steps: int = 32, max_distance: float = 120.0,
                 density: float = 0.02, height_falloff: float = 0.02,
                 extinction: float = 1.0, scattering: float = 1.0,
                 ambient_scatter: float = 0.006,
                 # === Phase 5：雾层（干冰雾）===
                 fog_layer: bool = False, noise_texture=None,
                 noise_scale: float = 0.06, noise_strength: float = 0.9,
                 noise_detail: float = 0.45, noise_grain: float = 0.15,
                 noise_tiling=(0.61, 0.29, 0.13), noise_warp: float = 1.0,
                 noise_speed: float = 1.0,
                 wind=(0.35, 0.12), wind2=(-0.22, 0.30),
                 fog_bottom: float = -0.5, fog_top: float = 2.2,
                 fog_top_variation: float = 0.5, top_softness: float = 0.35,
                 edge: float = 0.30, edge_softness: float = 0.15,
                 sharpen: float = 1.6,
                 emitter_power: float = 1.5, emitters=(),
                 fog_center=(0.0, 0.0), fog_spread=(0.0, 0.0),
                 # === Phase 6：世界体积盒（可放置的雾，见 gameobject/FogVolume.py）===
                 volume_enabled: bool = False,
                 volume_min=(0.0, 0.0, 0.0), volume_max=(0.0, 0.0, 0.0),
                 volume_softness: float = 0.0, volume_anchor: bool = False,
                 volume_yaw: float = 0.0,
                 internal_format: int = GL_RGBA16F) -> None:
        self.width = int(width)
        self.height = int(height)
        self.scale = float(scale)
        self.steps = int(steps)
        self.max_distance = float(max_distance)
        self.density = float(density)
        self.height_falloff = float(height_falloff)
        self.extinction = float(extinction)
        self.scattering = float(scattering)
        self.ambient_scatter = float(ambient_scatter)
        # 占位 3D 纹理：u_fogLayer == 0 时绑它（__init__ 里一定有 GL 上下文）
        self.fallback_noise_texture = _createDummyNoiseTexture()
        # === 雾层（Phase 5：3D 噪声驱动的"干冰雾"）===
        self.fog_layer = bool(fog_layer)
        self.noise_texture = noise_texture
        self.noise_scale = float(noise_scale)
        self.noise_strength = float(noise_strength)
        self.noise_detail = float(noise_detail)
        self.noise_grain = float(noise_grain)
        self.noise_tiling = glm.vec3(*noise_tiling)
        self.noise_warp = float(noise_warp)
        self.noise_speed = float(noise_speed)
        self.wind = glm.vec2(*wind)
        self.wind2 = glm.vec2(*wind2)
        self.fog_bottom = float(fog_bottom)
        self.fog_top = float(fog_top)
        self.fog_top_variation = float(fog_top_variation)
        self.top_softness = float(top_softness)
        self.edge = float(edge)
        self.edge_softness = float(edge_softness)
        self.sharpen = float(sharpen)
        self.emitter_power = float(emitter_power)
        self.emitters = self.packEmitters(emitters)
        self.fog_center = glm.vec2(*fog_center)
        self.fog_spread = glm.vec2(*fog_spread)
        # === 世界体积盒（Phase 6）===
        self.volume_enabled = bool(volume_enabled)
        self.volume_min = glm.vec3(*volume_min)
        self.volume_max = glm.vec3(*volume_max)
        self.volume_softness = float(volume_softness)
        self.volume_anchor = bool(volume_anchor)
        self.volume_yaw = float(volume_yaw)
        # 流动相位（由相机每帧 advance(dt) 推进；暂停时冻结）
        self.noise_time = 0.0
        self.paused = False
        self.internal_format = internal_format
        self.program = None
        self.render_buffer = RenderBuffer.getWindowRenderBuffer()
        self._context = currentContext()
        self._released = False
        self.scatter_texture = 0
        self.out_texture = 0
        self.march_fbo = 0
        self.composite_fbo = 0
        self._allocate()

    # === 喷口 / 时间 ===

    @staticmethod
    def packEmitters(emitters) -> list:
        """把喷口列表规整成 ``MAX_EMITTERS`` 个 ``(x, y, z, radius)``（纯函数，可单测）。

        - 元素可以是 ``(x, y, z, r)`` / ``(x, y, z)``（半径默认 1）/ 带 ``x/y/z`` 的对象
        - 超出上限的截断（着色器的 uniform 数组是定长的），不足的补零向量
        """
        packed = []
        for emitter in tuple(emitters or ()):
            if hasattr(emitter, "x") and hasattr(emitter, "y") and hasattr(emitter, "z"):
                values = (float(emitter.x), float(emitter.y), float(emitter.z),
                          float(getattr(emitter, "radius", 1.0)))
            else:
                values = tuple(float(v) for v in emitter)
                if len(values) == 3:
                    values = values + (1.0,)
            packed.append(values)
            if len(packed) >= MAX_EMITTERS:
                break
        while len(packed) < MAX_EMITTERS:
            packed.append((0.0, 0.0, 0.0, 0.0))
        return packed

    def advance(self, dt: float) -> None:
        """推进流动相位（由相机的 ``renderVolumetric`` 每帧调用一次）。

        暂停时不动 —— 这样"雾还在流动"与"整帧静止"可以对比（也方便抓图）。
        """
        if self.paused:
            return
        self.noise_time += float(dt) * self.noise_speed

    def setEmitter(self, index: int, position, radius: float = 1.0) -> None:
        """原地改一个喷口（demo 里拖动容器时用）。"""
        index = int(index) % MAX_EMITTERS
        x, y, z = (float(v) for v in position)
        self.emitters[index] = (x, y, z, float(radius))

    def clearEmitters(self) -> None:
        self.emitters = self.packEmitters(())

    # === 构建 ===

    def _allocate(self) -> None:
        self.half_width = max(1, int(self.width * self.scale))
        self.half_height = max(1, int(self.height * self.scale))
        # 散射缓冲：RGB = 散射颜色，A = 透过率
        self.scatter_texture = self._createTexture(self.half_width,
                                                  self.half_height)
        self.out_texture = self._createTexture(self.width, self.height)
        self.march_fbo = self._createFramebuffer(self.scatter_texture)
        self.composite_fbo = self._createFramebuffer(self.out_texture)

    def _createTexture(self, width: int, height: int) -> int:
        texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, texture)
        glTexStorage2D(GL_TEXTURE_2D, 1, self.internal_format, width, height)
        # 半分辨率的散射要线性放大到全分辨率，所以是 LINEAR
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glBindTexture(GL_TEXTURE_2D, 0)
        return texture

    @staticmethod
    def _createFramebuffer(texture: int) -> int:
        framebuffer = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, framebuffer)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, texture, 0)
        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"体积渲染帧缓冲不完整 (status=0x{status:x})")
        return framebuffer

    def getProgram(self):
        if self.program is None:
            self.program = ShaderManager.loadShader("volumetric")
            # 通用绘制路径会设置主着色器的 uniform，本程序里没有 —— 预期行为
            self.program.silenceUniform(
                "formatType", "fuc", "tex0", "win_wh", "wh", "translate",
                "rotate", "scale", "view", "u_cameraPos", "u_lightCount")
        return self.program

    # === pass ===

    def render(self, camera, color_texture: int, depth_texture: int,
               light=None, shadow_map=None) -> int:
        """跑一遍体积散射并合成，返回可直接采样的最终颜色纹理。

        - ``camera``：取投影/视矩阵（重建世界坐标）与相机位置
        - ``color_texture``：场景颜色（HDR，不透明 + 半透明都画完）
        - ``depth_texture``：可采样深度（决定每条射线走到哪）
        - ``light``：方向光（散射颜色 + 体积阴影方向），None 时只有环境散射
        - ``shadow_map``：Phase 2 的 ShadowMap；None 或该光源不投影则不查阴影
        """
        if not color_texture or not depth_texture:
            return 0
        program = self.getProgram()
        program.use()
        # 全屏后处理：不要深度测试/混合，也不要写深度
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_BLEND)
        glDepthMask(GL_FALSE)

        projection, view = self._cameraMatrices(camera)
        inv_view_proj = glm.inverse(projection * view)

        # === 第 1 趟：半分辨率射线步进（输出 vec4(散射, 透过率)）===
        glBindFramebuffer(GL_FRAMEBUFFER, self.march_fbo)
        glViewport(0, 0, self.half_width, self.half_height)
        glActiveTexture(GL_TEXTURE0 + DEPTH_UNIT)
        glBindTexture(GL_TEXTURE_2D, depth_texture)
        program.uniform1i("u_depth", DEPTH_UNIT)
        self._uploadRayMarch(program, inv_view_proj, camera)
        self._uploadFogLayer(program)
        self._uploadVolumeBox(program)
        self._uploadLight(program, light, shadow_map)
        self.render_buffer.draw(False)

        # === 第 2 趟：全分辨率合成 scene * T + scattered ===
        glBindFramebuffer(GL_FRAMEBUFFER, self.composite_fbo)
        glViewport(0, 0, self.width, self.height)
        glActiveTexture(GL_TEXTURE0 + COLOR_UNIT)
        glBindTexture(GL_TEXTURE_2D, color_texture)
        program.uniform1i("u_color", COLOR_UNIT)
        glActiveTexture(GL_TEXTURE0 + SCATTER_UNIT)
        glBindTexture(GL_TEXTURE_2D, self.scatter_texture)
        program.uniform1i("u_scatter", SCATTER_UNIT)
        program.uniform1i("u_mode", 1)
        program.uniform1f("u_scattering", self.scattering)
        self.render_buffer.draw(False)

        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glActiveTexture(GL_TEXTURE0)
        # 直接改过 GL 状态：作废缓存 + 真正把 pass 默认状态下发一遍
        # （同 SSAO：本 pass 关掉了深度测试/混合/深度写入，不复位会污染后面的绘制，
        #   例如同帧内的第二个相机、或下一帧的 shadow/AO 预 pass）
        RenderState.invalidate()
        RenderState.setDefaults()
        # 还原主着色器（同 WBOIT resolve / SSAO：否则后续 uniform 会落到本程序上）
        render_global = RenderGlobal.instance
        if render_global is not None and render_global.dis_shader is not None:
            render_global.dis_shader.use()
        return self.out_texture

    def _uploadRayMarch(self, program, inv_view_proj, camera) -> None:
        """下发射线步进的公共参数（雾层/光照各自另有方法）。

        ``u_ambientScatter`` 在这里：体积阴影里只剩环境散射，所以它同时是
        "阴影里的雾有多黑"的唯一旋钮 —— 太阳低斜、影子被拉成长条时，抬高它
        才能把"黑条"变成"灰条"。
        """
        program.uniform1i("u_mode", 0)
        program.uniformMatrix4fv("u_invViewProj", inv_view_proj)
        program.uniform3f("u_cameraPos", camera.x, camera.y, camera.z)
        program.uniform2f("u_win", float(self.width), float(self.height))
        program.uniform1f("u_density", self.density)
        program.uniform1f("u_heightFalloff", self.height_falloff)
        program.uniform1f("u_extinction", self.extinction)
        program.uniform1f("u_scattering", self.scattering)
        program.uniform1f("u_ambientScatter", self.ambient_scatter)
        program.uniform1f("u_maxDistance", self.max_distance)
        program.uniform1i("u_steps", self.steps)
        # 帧号取模：抖动周期不要无限增长（避免浮点精度掉下去）
        program.uniform1f("u_frameIndex",
                          float(RenderGlobal.instance.frame_index % 64))

    def _uploadFogLayer(self, program) -> None:
        """下发雾层参数 + 绑定 3D 噪声（每种状态都下发，GL uniform 是持久状态）。

        - ``fog_layer=False`` 或没有噪声纹理 → ``u_fogLayer = 0``：着色器走原来的
          纯高度雾分支（老场景逐像素不变）。此时仍然绑一张 1×1×1 占位 3D 纹理，
          避免采样器指向不完整纹理。
        - 噪声纹理是 lazy 的（``TextureVolumeNoise`` 构造时不碰 GL），第一次真正
          用到时在这里生成 —— 这里一定有当前 GL 上下文。
        """
        texture = self.noise_texture
        enabled = bool(self.fog_layer and texture is not None)
        if enabled:
            generate = getattr(texture, "generate", None)
            if generate is not None and not getattr(texture, "generated", True):
                generate()
        program.uniform1i("u_fogLayer", 1 if enabled else 0)
        program.uniform1f("u_noiseScale", self.noise_scale)
        program.uniform1f("u_noiseStrength", self.noise_strength)
        program.uniform1f("u_noiseDetail", self.noise_detail)
        program.uniform1f("u_noiseGrain", self.noise_grain)
        program.uniform3f("u_noiseTiling", self.noise_tiling.x,
                          self.noise_tiling.y, self.noise_tiling.z)
        program.uniform1f("u_noiseWarp", self.noise_warp)
        program.uniform2f("u_wind", self.wind.x, self.wind.y)
        program.uniform2f("u_wind2", self.wind2.x, self.wind2.y)
        program.uniform3f("u_fogRange", self.fog_bottom, self.fog_top,
                          self.fog_top_variation)
        program.uniform1f("u_topSoftness", max(self.top_softness, 1e-3))
        program.uniform1f("u_edge", self.edge)
        program.uniform1f("u_edgeSoftness", self.edge_softness)
        program.uniform1f("u_fogSharpen", self.sharpen)
        program.uniform1f("u_noiseTime", self.noise_time)
        program.uniform2f("u_fogCenter", self.fog_center.x, self.fog_center.y)
        program.uniform2f("u_fogSpread", self.fog_spread.x, self.fog_spread.y)
        program.uniform1f("u_emitterPower", self.emitter_power)
        # 空槽位（半径 0）跳过，前面有空洞时后面的喷口要往前挤 —— 着色器只
        # 循环到 u_emitterCount，中间夹一个 (0,0,0,0) 会在原点造出一个假喷口
        count = 0
        for emitter in self.emitters:
            if count >= MAX_EMITTERS:
                break
            x, y, z, radius = emitter
            if radius <= 0.0:
                continue
            program.uniform4f(f"u_emitter[{count}]", x, y, z, radius)
            count += 1
        program.uniform1i("u_emitterCount", count)
        # 绑定 3D 噪声（启用时用真纹理，否则占位纹理）
        program.uniform1i("u_noiseTex", NOISE_UNIT)
        glActiveTexture(GL_TEXTURE0 + NOISE_UNIT)
        if enabled and getattr(texture, "id", 0):
            glBindTexture(GL_TEXTURE_3D, int(texture.id))
        else:
            glBindTexture(GL_TEXTURE_3D, int(self.fallback_noise_texture))
        glActiveTexture(GL_TEXTURE0)

    def _uploadVolumeBox(self, program) -> None:
        """下发"世界体积盒"（Phase 6）：把雾当成可放置对象时的那组 uniform。

        每种状态都下发（GL uniform 是持久状态）：``volume_enabled = False`` 时
        下发 ``u_volumeEnabled = 0``，着色器走老路径（雾是相机属性）→ 逐像素不变。
        """
        program.uniform1i("u_volumeEnabled", 1 if self.volume_enabled else 0)
        program.uniform3f("u_volumeMin", self.volume_min.x, self.volume_min.y,
                          self.volume_min.z)
        program.uniform3f("u_volumeMax", self.volume_max.x, self.volume_max.y,
                          self.volume_max.z)
        program.uniform1f("u_volumeSoftness", self.volume_softness)
        program.uniform1i("u_volumeAnchor", 1 if self.volume_anchor else 0)
        program.uniform1f("u_volumeYaw", self.volume_yaw)

    @staticmethod
    def _cameraMatrices(camera):
        """取相机的投影/视矩阵（缺省用单位矩阵，保证不崩）。"""
        model = getattr(camera, "m", None)
        projection = getattr(model, "projection", None)
        view = getattr(model, "view", None)
        if projection is None:
            projection = glm.mat4(1.0)
        if view is None:
            view = glm.mat4(1.0)
        return projection, view

    def _uploadLight(self, program, light, shadow_map) -> None:
        direction = glm.vec3(0.0, -1.0, 0.0)
        color = glm.vec3(0.0)
        if light is not None:
            raw = glm.vec3(light.direction)
            length = glm.length(raw)
            if length > 1e-6:
                direction = raw / length
            color = glm.vec3(light.radiance)
        program.uniform3f("u_lightDir", direction.x, direction.y, direction.z)
        program.uniform3f("u_lightColor", color.x, color.y, color.z)
        shadow_enabled = (1 if (light is not None and shadow_map is not None
                                and getattr(light, "cast_shadow", False)) else 0)
        program.uniform1i("u_shadowEnabled", shadow_enabled)
        if not shadow_enabled:
            return
        program.uniformMatrix4fv("u_lightSpaceMatrix",
                                 shadow_map.light_space_matrix)
        program.uniform1f("u_shadowTexel", shadow_map.texel)
        program.uniform1f("u_shadowBias", shadow_map.bias)
        glActiveTexture(GL_TEXTURE0 + SHADOW_UNIT)
        glBindTexture(GL_TEXTURE_2D, shadow_map.texture)
        program.uniform1i("u_shadowMap", SHADOW_UNIT)

    # === 生命周期 ===

    def resize(self, width: int, height: int) -> bool:
        """窗口尺寸变化时重建散射/合成缓冲。返回是否真的重建了。"""
        if (int(width), int(height)) == (self.width, self.height):
            return False
        self._deleteTargets()
        self.width = int(width)
        self.height = int(height)
        self._allocate()
        return True

    def _deleteTargets(self) -> None:
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        for framebuffer in (self.march_fbo, self.composite_fbo):
            if framebuffer:
                glDeleteFramebuffers(1, [framebuffer])
        self.march_fbo = self.composite_fbo = 0
        for texture in (self.scatter_texture, self.out_texture):
            if texture:
                glDeleteTextures(1, [texture])
        self.scatter_texture = self.out_texture = 0

    def release(self) -> None:
        """释放 GPU 资源（需要在 GL 上下文里调用）。"""
        if self._released:
            return
        self._released = True
        if not isSameContext(self._context):
            self.march_fbo = self.composite_fbo = 0
            self.scatter_texture = self.out_texture = 0
            self.fallback_noise_texture = 0
            return
        self._deleteTargets()
        if self.fallback_noise_texture:
            glDeleteTextures(1, [int(self.fallback_noise_texture)])
            self.fallback_noise_texture = 0
        # 程序由 ShaderManager 管理，这里只解除引用
        self.program = None
