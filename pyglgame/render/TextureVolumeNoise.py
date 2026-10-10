"""3D 体积噪声贴图（计算着色器**一次性**生成，供体积雾滚动采样）。

## 为什么体积雾需要它

体积雾（Phase 4 的 ``volumetric.frag``）在射线上采样"雾的密度"。没有噪声时密度
只跟高度有关 —— 那就是一层均匀的灰雾。要做成**干冰那种分明、翻涌的雾**，密度
必须是 3D 的、随时间流动的，而这正是这张贴图的作用：

    密度 = 高度剖面 × 采样(3D 噪声, 世界坐标 × 尺度 + 风速 × 时间)

## 为什么"生成一次"

噪声内容是静态的，流动靠**采样坐标平移**（两层不同风速错动）就能做出来，
比每帧重算一张 64³ 贴图便宜两个数量级。所以它是 lazy 的：

    noise = TextureVolumeNoise(64)   # 此刻不碰 GL（模块顶层构造也安全）
    noise.generate()                 # 第一次真正用到时在 GL 上下文里生成

``generate()`` 幂等，重复调用不会重复算。

## 四个通道（一张贴图四种结构，着色器里自由组合）

| 通道 | 默认种类 | 用途 |
|---|---|---|
| R | ``perlin`` | 主结构：低频大团块 / 云顶起伏 |
| G | ``ridged`` | 中频翻涌：干冰的卷边与丝状 |
| B | ``worley`` | 细颗粒：气泡 / 颗粒感 |
| A | ``value`` | 高频细节：侵蚀 / 抖动 |

## 必须可平铺

采样坐标是世界坐标 × 尺度 + 风速×时间，会**无限增长**；贴图如果不是无缝的，
每跨过一个整数边界就会出现一道可见的硬接缝（干冰雾会变成"刷了格子的墙"）。
所以 ``frequency`` 必须是整数（构造时会取整），shader 侧对格点哈希取周期模。

## 一个必须记住的坑：3D 纹理按 image 绑时要 ``layered=GL_TRUE``

``generate()`` 里 ``glBindImageTexture(..., layered, ...)`` 的 ``layered`` 对 3D 纹理
**必须**是 ``GL_TRUE``。传 ``GL_FALSE`` 只会绑住**一层**，着色器里 ``gid.z > 0`` 的
``imageStore`` 全部越界 → 体数据大半是未定义内容。它的表现不是"花屏"，而是
**整张 64³ 体沿纹理 x 轴完全重复**（实测沿 x 的梯度能量 0.000000、自相关 1.0000，
四个通道都一样）；体积雾恰好用 ``coord.x`` 采样它，于是**雾的密度场和"云顶起伏"
完全不随世界 x 变化** —— 等于一堆沿 x 无限延伸的沟槽，相机朝 ±x 看就是"被梳子
梳过"的条状拉丝。修好后三个轴的梯度能量是 1.09 : 1.03 : 1.00。
2D 纹理（Bloom / FFT / 2D 噪声）用 ``GL_FALSE`` 是对的，别一起改。

## 另一个坑：可平铺 = 固有周期，采样侧必须打散

"可平铺"意味着贴图在采样坐标跨过一个整数时**逐像素重复**：世界尺度上的固有周期
= ``1 / noise_scale``（体积雾里 0.16 → 6.25 米）。**这一半要在着色器侧解决**：
``volumetric.frag`` 用三层不可通约的尺度 + 三个旋转角 + 低频域扭曲
（``u_noiseTiling`` / ``u_noiseWarp``）把合成周期推到各层公倍数；只采一层时雾面上
会清楚地印着"同一团雾每 6.25 米又出现一次"（自相关实测 0.875 → 修好后 0.052）。

另外 **每个通道的倍频层数由贴图分辨率兜底**（``computeVolumeNoise.comp`` 的
``nzOctaveBudget``）：64³ + ``frequency=2`` + ``channel_gain=2`` 时 R/G/B 是 4/3/2 层，
超过奈奎斯特（每格 < 2 纹素）的那一层是随机纹素，采样出来就是雾面上一层固定的粗颗粒。
"""

import math
import ctypes

from OpenGL.GL import *

from .TextureBase import TextureBase
from .TextureFractalNoise import (NOISE_BILLOW, NOISE_CURL, NOISE_PERLIN,
                                  NOISE_RIDGED, NOISE_VALUE, NOISE_WARP,
                                  NOISE_WORLEY, NOISE_WORLEY_EDGE, kindId,
                                  kindName)
from ..RenderGlobal import RenderGlobal
from ..tools.glDebug import currentContext

# glBindImageTexture 的单元：GL_MAX_IMAGE_UNITS 通常是 8（合法编号 0..7），
# 而 0..6 被 Bloom/FFT 占了。7 是本引擎的"噪声单元"（2D 噪声贴图也用 7）：
# 两处都会在 dispatch 之前重新绑定，不会真的互相踩。
IMAGE_UNIT = 7
# 必须与 res/shader/computeVolumeNoise.comp 的 local_size 一致
LOCAL_SIZE = 4
# 通道默认种类（R, G, B, A）
DEFAULT_KINDS = (NOISE_PERLIN, NOISE_RIDGED, NOISE_WORLEY, NOISE_VALUE)
# 通道说明（HUD 用）
CHANNEL_LABELS = ("R 主结构", "G 中频翻涌", "B 细颗粒", "A 高频细节")

# 体积噪声预设：Game6 里一键轮换，直观看到"种类"对雾形态的影响
KIND_PRESETS = (
    ("干冰卷边 perlin/ridged/worley", (NOISE_PERLIN, NOISE_RIDGED, NOISE_WORLEY, NOISE_VALUE)),
    ("气泡颗粒 worley/billow/edge", (NOISE_WORLEY, NOISE_BILLOW, NOISE_WORLEY_EDGE, NOISE_BILLOW)),
    ("丝状烟雾 warp/billow/worley", (NOISE_WARP, NOISE_BILLOW, NOISE_WORLEY, NOISE_VALUE)),
    ("翻卷山脊 ridged/warp/billow", (NOISE_RIDGED, NOISE_WARP, NOISE_BILLOW, NOISE_WORLEY_EDGE)),
    ("旋度湍流 curl/warp/ridged", (NOISE_CURL, NOISE_WARP, NOISE_RIDGED, NOISE_VALUE)),
)


class TextureVolumeNoise(TextureBase):
    """3D 噪声体积纹理（RGBA32F，image unit 8）。

    构造不会碰 GL（模块顶层也能安全 new）；第一次 ``generate()`` 时才在上下文里
    建纹理 + dispatch 计算着色器。参数都是公开属性，改完调 ``invalidate()``
    （或 ``setKinds``）再 ``generate()`` 即可重新生成。
    """

    def __init__(self, size: int = 64, frequency: float = 2.0,
                 octaves: int = 3, lacunarity: float = 2.0, gain: float = 0.5,
                 kinds=DEFAULT_KINDS, channel_gain: float = 2.0,
                 jitter: float = 0.75, warp: float = 0.7,
                 threshold: float = 0.0, softness: float = 0.0,
                 contrast: float = 1.0, amplitude: float = 1.0,
                 value: float = 0.0, invert: bool = False,
                 internal_format: int = GL_RGBA32F) -> None:
        super().__init__(0)                      # 纹理对象延迟到 generate()
        self.size = max(int(size), LOCAL_SIZE)
        # 无缝的前提是"整数格点周期"：这里直接取整，免得手滑改成小数后接缝
        self.frequency = float(max(1.0, round(float(frequency))))
        self.octaves = int(octaves)
        self.lacunarity = float(lacunarity)
        self.gain = float(gain)
        self.kinds = tuple(kindId(kind) for kind in kinds)[:4]
        if len(self.kinds) < 4:
            self.kinds = self.kinds + DEFAULT_KINDS[len(self.kinds):]
        self.channel_gain = float(channel_gain)
        self.jitter = float(jitter)
        self.warp = float(warp)
        self.threshold = float(threshold)
        self.softness = float(softness)
        self.contrast = float(contrast)
        self.amplitude = float(amplitude)
        self.value = float(value)
        self.invert = bool(invert)
        self.internal_format = internal_format
        self.generated = False

    # === 参数 ===

    @property
    def group_count(self) -> int:
        """每个轴的 dispatch 组数（local_size = 4³）。"""
        return max(1, math.ceil(self.size / LOCAL_SIZE))

    def invalidate(self) -> "TextureVolumeNoise":
        """标记内容过期：下次 ``generate()`` 重新算（比如改了种类/频率）。"""
        self.generated = False
        return self

    def setKinds(self, *kinds) -> "TextureVolumeNoise":
        """设置四个通道的种类（少于四个时其余保持默认）。"""
        names = list(kinds)
        if len(names) == 1 and isinstance(names[0], (tuple, list)):
            names = list(names[0])
        self.kinds = tuple(kindId(kind) for kind in names)[:4]
        return self.invalidate()

    def setPreset(self, name: str) -> "TextureVolumeNoise":
        """按预设名切换四个通道的种类（Game6 里按键轮换用）。"""
        for preset_name, kinds in KIND_PRESETS:
            if preset_name == name:
                return self.setKinds(*kinds)
        raise ValueError(f"未知预设 '{name}'，可选: "
                         + " / ".join(item[0] for item in KIND_PRESETS))

    def kindSummary(self) -> str:
        """四个通道的种类（HUD 打印用）。"""
        return " ".join(f"{label}={kindName(kind)}"
                        for label, kind in zip(CHANNEL_LABELS, self.kinds))

    # === 生成 ===

    def generate(self) -> bool:
        """生成/重新生成贴图（幂等）。需要当前有 GL 上下文，否则直接报错。"""
        if self.generated and self.id:
            return False
        render_global = RenderGlobal.instance
        if render_global is None:
            raise RuntimeError("TextureVolumeNoise.generate() 需要引擎已初始化")
        if not self.id:
            self.id = int(glGenTextures(1))
            self._context = currentContext()
            self._released = False
            glBindTexture(GL_TEXTURE_3D, self.id)
            glTexStorage3D(GL_TEXTURE_3D, 1, self.internal_format,
                           self.size, self.size, self.size)
            # 必须 REPEAT：采样坐标是无限增长的世界坐标，平铺才能无缝
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_REPEAT)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_REPEAT)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_REPEAT)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glBindTexture(GL_TEXTURE_3D, 0)

        shader = render_global.volume_noise_shader
        previous = render_global.using_shader
        # 第 4 个参数 layered **必须** GL_TRUE：3D 纹理绑成 image3D 时，GL_FALSE 只绑
        # 一层（z=0 那一片），着色器里 gid.z > 0 的 imageStore 全部越界 → 体数据只有
        # 一小部分被写、其余是**未定义内容**。表现不是"花屏"而是极难认的那种：
        # 整张 64³ 体沿纹理 x 轴**完全重复**（实测梯度能量 gx = 0.000000、lag1 相关
        # = 1.0000，四个通道都一样），而体积雾恰好用 coord.x 采样它 —— 于是雾的密度
        # 场和"云顶起伏"都**只随 y/z 变、完全不随世界 x 变**，等于一堆沿 x 无限延伸的
        # 沟槽；相机朝 ±x 看时这些沟槽正对视线，屏幕上就是"被梳子梳过"的条状拉丝。
        # 2D 纹理（Bloom/FFT/2D 噪声）用 GL_FALSE 是对的，只有 3D 要 GL_TRUE。
        glBindImageTexture(IMAGE_UNIT, self.id, 0, GL_TRUE, 0,
                           GL_WRITE_ONLY, self.internal_format)
        shader.use()
        shader.uniform1f("u_frequency", self.frequency)
        shader.uniform1i("u_kindR", self.kinds[0])
        shader.uniform1i("u_kindG", self.kinds[1])
        shader.uniform1i("u_kindB", self.kinds[2])
        shader.uniform1i("u_kindA", self.kinds[3])
        shader.uniform1i("u_octaves", max(1, min(self.octaves, 8)))
        shader.uniform1f("u_lacunarity", self.lacunarity)
        shader.uniform1f("u_gain", self.gain)
        shader.uniform1f("u_channelGain", self.channel_gain)
        shader.uniform1f("u_jitter", self.jitter)
        shader.uniform1f("u_warp", self.warp)
        shader.uniform1f("u_threshold", self.threshold)
        shader.uniform1f("u_softness", self.softness)
        shader.uniform1f("u_contrast", self.contrast)
        shader.uniform1f("u_amplitude", self.amplitude)
        shader.uniform1f("u_value", self.value)
        shader.uniform1i("u_invert", 1 if self.invert else 0)
        groups = self.group_count
        shader.dispatch(groups, groups, groups)
        shader.memoryBarrier()
        if previous is not None:
            previous.use()
        self.generated = True
        return True

    # === 绑定 / 释放 ===

    def bindVolume(self, unit=GL_TEXTURE0) -> None:
        """绑成普通 ``sampler3D``（着色器里的采样器 uniform 由调用方设置）。"""
        glActiveTexture(unit)
        glBindTexture(GL_TEXTURE_3D, self.id)

    def getData(self, level=0, fmt=GL_RGBA, dtype=GL_FLOAT):
        """读回全部体素（``size³ × 4`` 的 ctypes 数组，调试/验证用）。

        与 2D 的 :meth:`TextureBase.getData` 同构，只是体积是三维的。
        """
        c_type = ctypes.c_float if dtype == GL_FLOAT else ctypes.c_ubyte
        count = self.size * self.size * self.size * 4
        buffer = (c_type * count)()
        glBindTexture(GL_TEXTURE_3D, self.id)
        glGetTextureImage(self.id, level, fmt, dtype, ctypes.sizeof(buffer),
                          buffer)
        glBindTexture(GL_TEXTURE_3D, 0)
        return buffer

    def channelStats(self) -> list:
        """四个通道的 (均值, 标准差)，用来确认噪声真的"有内容"（不是常数/爆炸）。"""
        data = self.getData()
        stats = []
        for channel in range(4):
            values = data[channel::4]
            count = len(values)
            mean = sum(values) / count
            variance = sum((value - mean) ** 2 for value in values) / count
            stats.append((mean, variance ** 0.5))
        return stats

    def release(self) -> None:
        if self._released:
            return
        super().release()
        self.generated = False
