"""二维程序化噪声贴图（计算着色器，GPU 生成、逐帧可更新）。

## 它是什么

``TextureFractalNoise`` 是一张 RGBA32F 的 image unit 贴图（单元 7），内容由
``res/shader/computeFractalNoise.comp`` 在 GPU 上算出。用法三步：

    texture = TextureFractalNoise(512, 512, kind=NOISE_PERLIN, frequency=4.0,
                                  tileable=True, channel_mode=PACK_RGB_OCTAVE)
    texture.render(dt, fps)     # 每帧调用（dispatch 一次计算着色器；animate=False 可冻结）
    texture.bind(GL_TEXTURE0)   # 之后当普通 sampler2D 采样

## 种类（``kind``）

| 名称 | 值 | 观感 / 用途 |
|---|---|---|
| ``value`` | 0 | 值噪声：最便宜，方团块 |
| ``perlin`` | 1 | 梯度噪声：结构自然，云雾底图 |
| ``ridged`` | 2 | 山脊 ``1-abs(n)``：翻卷的"山脊线"，干冰的翻涌感 |
| ``billow`` | 3 | 团絮 ``abs(n)``：棉花团 |
| ``worley`` | 4 | 细胞 F1：颗粒 / 气泡 |
| ``worley_edge`` | 5 | 细胞 F2-F1：裂纹 / 蜂窝边界 |
| ``warp`` | 6 | 域扭曲 fBm：烟雾的丝状结构 |
| ``curl`` | 7 | 旋度噪声：无散度向量场（标量取模长） |

编号在 ``res/shader/noise.glsl`` 里也有一份；``NOISE_KINDS`` 是唯一真相，
``tests/test_engine.py::NoiseShaderContractTest`` 读着色器源码比对，改单边会失败。

## 功能（除了"换种类"）

- **可平铺**：``tileable=True`` 把格点周期锁成整数（``round(frequency)``），
  贴图 wrap 成 REPEAT 时首尾逐像素连续（哈希对格点取模实现，见 noise.glsl）。
- **多倍频**：``octaves`` / ``lacunarity`` / ``gain`` 控制细节层次；输出归一化到 0..1。
- **通道打包**：``channel_mode`` 决定 RGB 放什么 —— 三种种类 / 三个频率层 /
  三个时间切片 / 低频×高频+A 遮罩（``PACK_*``）。
- **时间轴**：噪声是 3D 的，第三轴就是时间切片；``time_loop=n`` 让时间轴也周期化
  → ``n`` 秒一循环且接缝不可见（``PACK_RGB_TIME`` 下最明显）。
- **形状映射**：``threshold`` / ``softness`` / ``contrast`` / ``invert``
  （``softness=0`` 时是恒等映射，保持"原始噪声"观感）。

## 与旧版的差异（没有 demo 依赖旧行为，所以直接改语义）

旧 uniform 名字全部保留，但含义变了两处：``frequency`` 现在是"每个轴向的基础
格点数"（旧版是初始频率倍率），fBm 输出做了归一化（旧版是振幅累加，同参数下
新版略亮）。
"""

import glm
from OpenGL.GL import *

from .TextureStorage2D import TextureStorage2D
from ..RenderGlobal import RenderGlobal

# === 噪声种类（必须与 res/shader/noise.glsl 的 const int NOISE_* 一致）===
NOISE_VALUE = 0
NOISE_PERLIN = 1
NOISE_RIDGED = 2
NOISE_BILLOW = 3
NOISE_WORLEY = 4
NOISE_WORLEY_EDGE = 5
NOISE_WARP = 6
NOISE_CURL = 7
NOISE_COUNT = 8

NOISE_KINDS = {
    "value": NOISE_VALUE,
    "perlin": NOISE_PERLIN,
    "ridged": NOISE_RIDGED,
    "billow": NOISE_BILLOW,
    "worley": NOISE_WORLEY,
    "worley_edge": NOISE_WORLEY_EDGE,
    "warp": NOISE_WARP,
    "curl": NOISE_CURL,
}
NOISE_NAMES = {value: name for name, value in NOISE_KINDS.items()}

# === 通道打包模式（必须与 noise.glsl 的 PACK_* 一致）===
PACK_GRAY_MUL = 0
PACK_RGB_KINDS = 1
PACK_RGB_OCTAVE = 2
PACK_RGB_TIME = 3
PACK_RGBA_LAYER = 4
PACK_COUNT = 5

PACK_MODES = {
    "gray_mul": PACK_GRAY_MUL,
    "rgb_kinds": PACK_RGB_KINDS,
    "rgb_octave": PACK_RGB_OCTAVE,
    "rgb_time": PACK_RGB_TIME,
    "rgba_layer": PACK_RGBA_LAYER,
}
PACK_NAMES = {value: name for name, value in PACK_MODES.items()}

MAX_OCTAVES = 8


def kindId(kind) -> int:
    """把 ``"perlin"`` / ``NOISE_PERLIN`` 统一成 int；非法值直接报错（fail-fast）。"""
    if isinstance(kind, str):
        key = kind.strip().lower()
        if key not in NOISE_KINDS:
            raise ValueError(
                f"未知噪声种类 '{kind}'，可选: {', '.join(NOISE_KINDS)}")
        return NOISE_KINDS[key]
    kind = int(kind)
    if not 0 <= kind < NOISE_COUNT:
        raise ValueError(f"噪声种类编号越界: {kind}（0..{NOISE_COUNT - 1}）")
    return kind


def kindName(kind: int) -> str:
    """int → 名字（HUD / 日志用）。"""
    return NOISE_NAMES.get(int(kind), f"kind{int(kind)}")


def packId(mode) -> int:
    """把 ``"rgb_kinds"`` / ``PACK_RGB_KINDS`` 统一成 int。"""
    if isinstance(mode, str):
        key = mode.strip().lower()
        if key not in PACK_MODES:
            raise ValueError(
                f"未知通道打包模式 '{mode}'，可选: {', '.join(PACK_MODES)}")
        return PACK_MODES[key]
    mode = int(mode)
    if not 0 <= mode < PACK_COUNT:
        raise ValueError(f"通道打包模式编号越界: {mode}（0..{PACK_COUNT - 1}）")
    return mode


def packName(mode: int) -> str:
    return PACK_NAMES.get(int(mode), f"pack{int(mode)}")


class TextureFractalNoise(TextureStorage2D):
    """GPU 计算的分形噪声贴图（RGBA32F，image unit 7）。

    参数都是公开属性，改了下一帧就生效：

    - ``frequency``：每个轴向的基础格点数（``tileable=True`` 时取整）
    - ``kind`` / ``octaves`` / ``lacunarity`` / ``gain``：噪声种类与倍频
    - ``period``：``vec3`` 格点周期（0 = 该轴不平铺）；一般用 ``tileable`` /
      ``time_loop`` 两个开关就够了，不用手动给
    - ``threshold`` / ``softness`` / ``contrast`` / ``invert``：形状映射
    - ``channel_mode`` / ``channel_gain``：通道打包
    - ``time``：``vec3`` 时间切片（``render()`` 按 ``time_scale`` 推进）
    - ``animate`` / ``time_scale``：是否自动推进时间，以及推进速度
    """

    def __init__(self, w: int, h: int, value: float = 0.0,
                 amplitude: float = 1.0, frequency: float = 4.0,
                 kind=NOISE_VALUE, octaves: int = 5, lacunarity: float = 2.0,
                 gain: float = 0.5, offset=(0.0, 0.0), period=None,
                 tileable: bool = False, time_loop: float = 0.0,
                 jitter: float = 0.7, warp: float = 0.6,
                 threshold: float = 0.0, softness: float = 0.0,
                 contrast: float = 1.0, invert: bool = False,
                 channel_mode=PACK_GRAY_MUL, channel_gain: float = 2.0,
                 animate: bool = True, time_scale: float = 0.1,
                 unit: int = 7, internal_format: int = GL_RGBA32F) -> None:
        super().__init__(int(w), int(h), unit=unit,
                         internal_format=internal_format)
        self.time = glm.vec3(0.0)
        # 旧接口（名字保留）
        self.value = float(value)
        self.amplitude = float(amplitude)
        self.frequency = float(frequency)
        self.offset = glm.vec2(*offset)
        # 种类 / 倍频
        self.kind = kindId(kind)
        self.octaves = int(octaves)
        self.lacunarity = float(lacunarity)
        self.gain = float(gain)
        # 平铺 / 时间
        self.tileable = bool(tileable)
        self.time_loop = float(time_loop)
        self.period = None if period is None else glm.vec3(*period)
        # Worley / 域扭曲
        self.jitter = float(jitter)
        self.warp = float(warp)
        # 形状映射
        self.threshold = float(threshold)
        self.softness = float(softness)
        self.contrast = float(contrast)
        self.invert = bool(invert)
        # 通道打包
        self.channel_mode = packId(channel_mode)
        self.channel_gain = float(channel_gain)
        # 动画
        self.animate = bool(animate)
        self.time_scale = float(time_scale)
        self.compute_shader = RenderGlobal.instance.fractal_noise_shader

    # === 参数 ===

    def setKind(self, kind) -> "TextureFractalNoise":
        """切换噪声种类（``"perlin"`` 或 ``NOISE_PERLIN`` 都行）。"""
        self.kind = kindId(kind)
        return self

    def nextKind(self, step: int = 1) -> int:
        """循环到下一个种类（demo 里一个键轮换用），返回新的种类编号。"""
        self.kind = (self.kind + int(step)) % NOISE_COUNT
        return self.kind

    def setChannelMode(self, mode) -> "TextureFractalNoise":
        """切换通道打包模式（``"rgb_octave"`` / ``PACK_RGB_OCTAVE``）。"""
        self.channel_mode = packId(mode)
        return self

    def nextChannelMode(self, step: int = 1) -> int:
        self.channel_mode = (self.channel_mode + int(step)) % PACK_COUNT
        return self.channel_mode

    def setTime(self, t) -> "TextureFractalNoise":
        """直接设时间切片（``vec3`` / 三个数 / 一个数都可以）。"""
        if isinstance(t, (int, float)):
            self.time = glm.vec3(float(t))
        else:
            values = list(t)
            if len(values) == 1:
                self.time = glm.vec3(float(values[0]))
            else:
                self.time = glm.vec3(float(values[0]), float(values[1]),
                                     float(values[2]))
        return self

    def setShape(self, threshold=None, softness=None, contrast=None,
                 invert=None) -> "TextureFractalNoise":
        """形状映射的批量设置（``None`` = 不改那一项）。"""
        if threshold is not None:
            self.threshold = float(threshold)
        if softness is not None:
            self.softness = float(softness)
        if contrast is not None:
            self.contrast = float(contrast)
        if invert is not None:
            self.invert = bool(invert)
        return self

    def periodVector(self) -> glm.vec3:
        """实际下发的格点周期向量。

        - 显式给了 ``period`` 就用它
        - 否则 ``tileable`` → 空间两轴取整（整数格点周期才是可平铺的）
        - ``time_loop > 0`` → 第三轴（时间轴）取该秒数
        """
        if self.period is not None:
            return glm.vec3(self.period)
        space = float(round(self.frequency)) if self.tileable else 0.0
        return glm.vec3(space, space, max(self.time_loop, 0.0))

    def describe(self) -> str:
        """一行摘要（HUD 打印用）。"""
        return (f"{kindName(self.kind)}×{int(self.octaves)} "
                f"freq={self.frequency:g} 通道={packName(self.channel_mode)}"
                f"{' 可平铺' if self.tileable else ''}")

    # === 渲染 ===

    def uploadUniforms(self) -> None:
        """把当前参数下发到计算着色器（拆出来便于复用/排查）。"""
        shader = self.compute_shader
        shader.use()
        period = self.periodVector()
        shader.uniform3f("u_time", *self.time)
        shader.uniform1f("u_value", self.value)
        shader.uniform1f("u_amplitude", self.amplitude)
        shader.uniform1f("u_frequency", self.frequency)
        shader.uniform2f("u_offset", *self.offset)
        shader.uniform1i("u_kind", self.kind)
        shader.uniform1i("u_octaves",
                         max(1, min(int(self.octaves), MAX_OCTAVES)))
        shader.uniform1f("u_lacunarity", self.lacunarity)
        shader.uniform1f("u_gain", self.gain)
        shader.uniform3f("u_period", *period)
        shader.uniform1f("u_jitter", self.jitter)
        shader.uniform1f("u_warp", self.warp)
        shader.uniform1f("u_threshold", self.threshold)
        shader.uniform1f("u_softness", self.softness)
        shader.uniform1f("u_contrast", self.contrast)
        shader.uniform1i("u_invert", 1 if self.invert else 0)
        shader.uniform1i("u_channelMode", self.channel_mode)
        shader.uniform1f("u_channelGain", self.channel_gain)

    def render(self, dt, fps) -> None:
        self.bindUnit(self.unit)
        # 计算着色器要用自己的程序：先记住当前程序，dispatch 完还原
        # （否则后续 uniform 会落到本程序上 —— 与 SSAO / 体积 pass 同一套约定）
        self.dis_shader = RenderGlobal.instance.using_shader
        self.uploadUniforms()
        self.compute_shader.dispatch(self.group_x, self.group_y, 1)
        self.compute_shader.memoryBarrier()
        if self.dis_shader is not None:
            self.dis_shader.use()
        if self.animate:
            self.time += glm.vec3(float(dt) * self.time_scale)
