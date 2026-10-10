"""粒子效果参数表：一份"粒子长什么样、怎么动"的配置（可复用、可 copy 派生）。

.. code-block:: python

    from pyglgame.particle import ParticleEffect, ParticleSystem

    fire = ParticleEffect.fire()          # 内置预设（每次调用都是新的一份）
    fire.setSize(0.12, variation=0.35)    # 链式改参数（都返回 self）
    ParticleSystem(fire, x=0.0, y=-1.0)

    # 从零写一份（参数名写错了会直接报错，不是静默忽略）
    spark = ParticleEffect(
        lifetime=0.6, lifetime_variation=0.4,
        rate=400.0, speed=6.0, speed_variation=0.5,
        direction=(0.0, 1.0), gravity=(0.0, -9.8, 0.0), drag=1.2,
        size=0.06, color=(1.0, 0.85, 0.4, 1.0), end_color=(1.0, 0.2, 0.0, 0.0),
        blend_mode="add", shape=ConeEmitter(angle=35.0))

参数分组
--------

- **生命周期**：``lifetime`` / ``lifetime_variation``
- **发射**：``rate``（每秒几个）、``burst``（``play()`` 时一次性来几个）、
  ``duration``（发多久之后停发，0 = 一直发）、``max_particles``、``prewarm``
- **运动**：``speed`` / ``speed_variation``、``direction``、``gravity``、
  ``drag``（线性阻尼）、``turbulence``（随机扰动）、``expansion``（径向扩散）
- **外观**：``size`` / ``size_variation``、``color`` / ``end_color``、
  ``blend_mode``、``texture``
- **曲线**（:mod:`pyglgame.math.Curve`，入参是**归一化年龄** ``age01 ∈ [0, 1]``）：
  ``size_curve``（尺寸倍率）、``alpha_curve``（透明度倍率）、
  ``color_curve``（起点色 → 终点色的插值位置）。不传就是线性。
- **形状**：``shape``（:class:`~pyglgame.particle.EmitterShape.EmitterShape`）

``lifetime`` / ``speed`` / ``size`` 的 ``*_variation`` 是**比例**：
``size=0.1, size_variation=0.3`` → 每个粒子在 ``0.07~0.13`` 之间抖一下。
"""

from ..math.Curve import BezierCurve, ParametricCurve, PowerCurve
from ..render.RenderState import BLEND_ADD, BLEND_ALPHA, BLEND_FUNCS
from .EmitterShape import (BoxEmitter, ConeEmitter, EmitterShape, PointEmitter,
                           SphereEmitter, normalize3)

#: 允许出现在 ``ParticleEffect(**)`` / ``copy(**)`` 里的参数名（写错会报错）
PARAMETERS = (
    "lifetime", "lifetime_variation",
    "rate", "burst", "duration", "max_particles", "prewarm",
    "speed", "speed_variation", "direction", "gravity", "drag", "turbulence",
    "expansion",
    "size", "size_variation", "size_curve", "alpha_curve", "color_curve",
    "color", "end_color", "blend_mode", "texture",
    "shape",
)


class ParticleEffect:
    """一份粒子效果的参数表（见模块文档）。

    只有数据 + 采样函数，**不持有粒子**：同一个效果表可以被多个
    :class:`~pyglgame.particle.ParticleSystem.ParticleSystem` 共用。
    """

    PARAMETERS = PARAMETERS

    def __init__(self, **overrides) -> None:
        # === 生命周期 ===
        self.lifetime: float = 1.0
        #: ±比例（0.3 = ±30%）
        self.lifetime_variation: float = 0.0
        # === 发射 ===
        self.rate: float = 60.0
        self.burst: int = 0
        #: 发射持续时长（0 = 一直发；粒子会各自走完寿命）
        self.duration: float = 0.0
        self.max_particles: int = 1000
        #: play() 时先"空转"这么久（一上来就是一团成熟的火，而不是从零长出来）
        self.prewarm: float = 0.0
        # === 运动 ===
        self.speed: float = 1.0
        self.speed_variation: float = 0.0
        self.direction = (0.0, 1.0, 0.0)
        self.gravity = (0.0, 0.0, 0.0)
        #: 线性阻尼：v *= clamp(1 - drag·dt, 0, 1)
        self.drag: float = 0.0
        #: 随机扰动加速度的幅度
        self.turbulence: float = 0.0
        #: 径向扩散速度（世界单位/秒，从发射原点向外；烟雾膨胀用）
        self.expansion: float = 0.0
        # === 外观 ===
        self.size: float = 0.1
        self.size_variation: float = 0.0
        self.size_curve: ParametricCurve = None
        self.alpha_curve: ParametricCurve = None
        self.color_curve: ParametricCurve = None
        self.color = (1.0, 1.0, 1.0, 1.0)
        #: 终点色（None = 不变色）
        self.end_color = None
        self.blend_mode: str = BLEND_ALPHA
        self.texture = None
        # === 形状 ===
        self.shape: EmitterShape = None
        self.apply(overrides)

    # === 构造 / 复制 / 校验 ===

    def apply(self, overrides: dict = None) -> "ParticleEffect":
        """批量写入参数。

        - 未知参数名直接报错 —— 拼错参数名是最难查的一类 bug；
        - **校验失败会回滚**：不会把对象留在"一半新一半旧"的坏状态
          （``ParticleEffect(size=-1.0)`` 抛异常之后，原来那份仍然可用）。
        """
        pending = {}
        for key, value in (overrides or {}).items():
            if key not in PARAMETERS:
                raise TypeError("未知的粒子参数 %r（可用: %s）"
                                % (key, ", ".join(PARAMETERS)))
            pending[key] = value
        if not pending:
            return self.validate()
        backup = {key: getattr(self, key) for key in pending}
        for key, value in pending.items():
            setattr(self, key, value)
        try:
            return self.validate()
        except BaseException:
            for key, value in backup.items():
                setattr(self, key, value)
            raise

    def copy(self, **overrides) -> "ParticleEffect":
        """复制一份（改参数不会影响原件）。

        形状与曲线是**共享引用**（它们本身不可变；要独立就自己换一份新对象）。
        """
        clone = ParticleEffect()
        for key in PARAMETERS:
            setattr(clone, key, getattr(self, key))
        return clone.apply(overrides)

    def validate(self) -> "ParticleEffect":
        """检查参数范围（构造和 :meth:`apply` 都会走一遍 → 错误当场暴露）。"""
        for name in ("lifetime", "rate", "drag", "turbulence", "expansion", "size"):
            if float(getattr(self, name)) < 0.0:
                raise ValueError("%s 不能是负数: %r"
                                 % (name, getattr(self, name)))
        if float(self.lifetime) <= 0.0:
            raise ValueError("lifetime 必须 > 0（粒子至少要活一帧）")
        for name in ("lifetime_variation", "speed_variation", "size_variation"):
            if float(getattr(self, name)) < 0.0:
                raise ValueError("%s 是比例，不能是负数" % name)
        if int(self.max_particles) < 1:
            raise ValueError("max_particles 必须 >= 1")
        if int(self.burst) < 0:
            raise ValueError("burst 不能是负数")
        if float(self.duration) < 0.0:
            raise ValueError("duration 不能是负数（0 = 一直发）")
        if self.blend_mode not in BLEND_FUNCS:
            raise ValueError("未知混合模式 %r（可用: %s）"
                             % (self.blend_mode, ", ".join(sorted(BLEND_FUNCS))))
        if self.shape is None:
            self.shape = PointEmitter()
        elif not hasattr(self.shape, "sample"):
            raise ValueError("shape 必须是 EmitterShape（要有 sample(rng)）")
        self.direction = normalize3(self.direction, (0.0, 1.0, 0.0))
        self.gravity = _vec3(self.gravity)
        self.color = _rgba(self.color)
        if self.end_color is not None:
            self.end_color = _rgba(self.end_color)
        for name in ("size_curve", "alpha_curve", "color_curve"):
            curve = getattr(self, name)
            if curve is not None and not callable(curve):
                raise ValueError("%s 必须是可调用的曲线（例如 BezierCurve）" % name)
        return self

    # === 出生时的采样（随机只发生在这几处，都由系统的 RNG 驱动 → 可复现）===

    @staticmethod
    def _sampleAround(rng, base: float, variation: float, minimum: float = 0.0):
        """``base ± base·variation`` 上的均匀抖动。"""
        base = float(base)
        if variation <= 0.0:
            return max(base, minimum)
        return max(rng.uniform(base * (1.0 - variation), base * (1.0 + variation)),
                   minimum)

    def sampleLifetime(self, rng) -> float:
        """抽一个寿命（秒）。"""
        return max(self._sampleAround(rng, self.lifetime, self.lifetime_variation),
                   1e-4)

    def sampleSpeed(self, rng) -> float:
        """抽一个速率（世界单位/秒）。"""
        return self._sampleAround(rng, self.speed, self.speed_variation)

    def sampleSize(self, rng) -> float:
        """抽一个初始尺寸。"""
        return self._sampleAround(rng, self.size, self.size_variation)

    # === 随时间变化（入参都是归一化年龄 0~1）===

    @staticmethod
    def _curveAt(curve, age01: float, default: float = 1.0) -> float:
        """曲线取值（``None`` → ``default``；入参夹在 0~1，曲线跑飞也不出怪值）。"""
        if curve is None:
            return default
        return float(curve(min(max(float(age01), 0.0), 1.0)))

    def sizeAt(self, age01: float) -> float:
        """归一化年龄处的尺寸（``size`` × ``size_curve``）。"""
        return max(self.size * self._curveAt(self.size_curve, age01), 0.0)

    def alphaAt(self, age01: float) -> float:
        """归一化年龄处的透明度倍率（``alpha_curve``；不传 = 1）。"""
        return self._curveAt(self.alpha_curve, age01, 1.0)

    def colorAt(self, age01: float) -> tuple:
        """归一化年龄处的颜色 ``(r, g, b, a)``。

        起点色 → 终点色（``end_color``）按 ``color_curve`` 插值，最后乘上
        ``alpha_curve``：``color=(亮黄, a=1)`` + ``end_color=(暗红, a=0)`` + 线性，
        就是最常见的"亮黄烧到暗红再熄灭"。
        """
        age01 = min(max(float(age01), 0.0), 1.0)
        mix = self._curveAt(self.color_curve, age01, age01)
        start = self.color
        if self.end_color is None:
            red, green, blue, alpha = start
        else:
            end = self.end_color
            red = start[0] + (end[0] - start[0]) * mix
            green = start[1] + (end[1] - start[1]) * mix
            blue = start[2] + (end[2] - start[2]) * mix
            alpha = start[3] + (end[3] - start[3]) * mix
        return (red, green, blue, alpha * self.alphaAt(age01))

    # === 链式改参数（都返回 self，方便一行写完一份效果）===

    def setLifetime(self, lifetime: float,
                    variation: float = None) -> "ParticleEffect":
        self.lifetime = float(lifetime)
        if variation is not None:
            self.lifetime_variation = float(variation)
        return self.validate()

    def setSpeed(self, speed: float, variation: float = None) -> "ParticleEffect":
        self.speed = float(speed)
        if variation is not None:
            self.speed_variation = float(variation)
        return self.validate()

    def setSize(self, size: float, variation: float = None,
                curve=None) -> "ParticleEffect":
        """设初始尺寸（``variation`` 是比例；``curve`` 是尺寸随年龄的倍率曲线）。"""
        self.size = float(size)
        if variation is not None:
            self.size_variation = float(variation)
        if curve is not None:
            self.size_curve = curve
        return self.validate()

    def setColor(self, color, end_color=None, curve=None) -> "ParticleEffect":
        """设起点色 / 终点色 / 变色曲线。"""
        self.color = _rgba(color)
        if end_color is not None:
            self.end_color = _rgba(end_color)
        if curve is not None:
            self.color_curve = curve
        return self.validate()

    def setAlpha(self, curve) -> "ParticleEffect":
        """设透明度曲线（fade in / fade out）。"""
        self.alpha_curve = curve
        return self.validate()

    def setGravity(self, gravity) -> "ParticleEffect":
        self.gravity = _vec3(gravity)
        return self.validate()

    def setDirection(self, direction) -> "ParticleEffect":
        self.direction = normalize3(direction, (0.0, 1.0, 0.0))
        return self

    def setShape(self, shape) -> "ParticleEffect":
        self.shape = shape
        return self.validate()

    def setBlend(self, blend_mode: str) -> "ParticleEffect":
        self.blend_mode = blend_mode
        return self.validate()

    def setTexture(self, texture) -> "ParticleEffect":
        self.texture = texture
        return self

    def setEmission(self, rate: float = None, burst: int = None,
                    duration: float = None) -> "ParticleEffect":
        """调发射：``rate`` 每秒几个 / ``burst`` 一次性几个 / ``duration`` 发多久。"""
        if rate is not None:
            self.rate = float(rate)
        if burst is not None:
            self.burst = int(burst)
        if duration is not None:
            self.duration = float(duration)
        return self.validate()

    # === 内置预设（每次都返回**新的一份**，可随便改；多余参数用 **overrides 覆盖）===

    @classmethod
    def fire(cls, **overrides) -> "ParticleEffect":
        """火焰：圆锥向上收束、加性混合、亮黄烧到暗红、先鼓一下再缩没。"""
        effect = cls(
            lifetime=1.1, lifetime_variation=0.35,
            rate=260.0, burst=60, max_particles=800,
            speed=1.6, speed_variation=0.5, direction=(0.0, 1.0, 0.0),
            gravity=(0.0, 1.4, 0.0),            # 热气往上顶
            drag=1.0, turbulence=0.9,
            size=0.45, size_variation=0.35,   # 世界单位：约 0.3~0.6
            # 尺寸曲线：先鼓一点再缩成 0（入参 = 归一化年龄）
            size_curve=BezierCurve(1.0, 1.2, 0.55, 0.0),
            # 透明度曲线：快速亮起（t=0 时是 0）再慢慢熄灭
            alpha_curve=BezierCurve(0.0, 0.6, 0.25, 0.0),
            color=(2.4, 1.2, 0.35, 1.0), end_color=(0.30, 0.04, 0.01, 1.0),
            color_curve=PowerCurve(0.55),       # 前半段保持亮黄，末尾迅速转暗
            blend_mode=BLEND_ADD,
            shape=ConeEmitter(angle=24.0, radius=0.12))
        return effect.apply(overrides)

    @classmethod
    def smoke(cls, **overrides) -> "ParticleEffect":
        """烟雾：慢慢升起 + 径向扩散，越飘越大越淡（常规 alpha 混合）。"""
        effect = cls(
            lifetime=2.6, lifetime_variation=0.4,
            rate=45.0, max_particles=400,
            speed=0.55, speed_variation=0.6,
            gravity=(0.0, 0.35, 0.0), drag=1.6, turbulence=1.1, expansion=0.25,
            size=0.55, size_variation=0.4,    # 世界单位（曲线会把它带到 ~1.9）
            size_curve=BezierCurve(1.0, 1.8, 2.6, 3.4),   # 越来越大的烟团
            # 透明度：出生就接近不透明，末端散掉。
            # ⚠ 别用"低 alpha 的灰烟"（0.2~0.4）：引擎的 alpha 混合对低 alpha 内容
            # 实测会偏暗（叠起来像黑斑），要么 alpha 给高（≥0.7），要么改用加性混合。
            alpha_curve=BezierCurve(0.85, 1.0, 0.95, 0.0),
            color=(0.88, 0.88, 0.92, 1.0), end_color=(0.80, 0.80, 0.86, 1.0),
            blend_mode=BLEND_ALPHA,
            shape=SphereEmitter(radius=0.12, radial=False,
                                direction=(0.0, 1.0, 0.0)))
        return effect.apply(overrides)

    @classmethod
    def spark(cls, **overrides) -> "ParticleEffect":
        """火花 / 溅射：又小又快、被重力往下拽、加性混合（配 Bloom 很亮）。"""
        effect = cls(
            lifetime=0.7, lifetime_variation=0.45,
            rate=320.0, burst=40, max_particles=900,
            speed=5.5, speed_variation=0.6,
            gravity=(0.0, -7.0, 0.0), drag=0.7, turbulence=0.8,
            size=0.14, size_variation=0.4,
            size_curve=PowerCurve(0.7),
            alpha_curve=PowerCurve(0.35),       # 亮很久，最后迅速熄灭
            color=(3.2, 2.2, 0.9, 1.0), end_color=(1.6, 0.25, 0.05, 1.0),
            color_curve=PowerCurve(0.5),
            blend_mode=BLEND_ADD,
            shape=ConeEmitter(angle=40.0, radius=0.03))
        return effect.apply(overrides)

    @classmethod
    def explosion(cls, count: int = 500, **overrides) -> "ParticleEffect":
        """爆炸：一次性球面爆发（``rate=0`` + ``burst``），短寿命 + 冲击波感。"""
        count = max(int(count), 1)
        effect = cls(
            lifetime=0.9, lifetime_variation=0.5,
            rate=0.0, burst=count, duration=0.02,
            max_particles=max(count, 1),
            speed=7.0, speed_variation=0.7,
            drag=2.4, turbulence=1.5,
            size=0.35, size_variation=0.5,
            size_curve=PowerCurve(0.5),
            alpha_curve=BezierCurve(1.0, 0.9, 0.4, 0.0),
            color=(3.0, 2.0, 0.8, 1.0), end_color=(0.5, 0.08, 0.02, 1.0),
            color_curve=PowerCurve(0.5),
            blend_mode=BLEND_ADD,
            shape=SphereEmitter(radius=0.25, surface=True, radial=True))
        return effect.apply(overrides)

    @classmethod
    def snow(cls, size=(12.0, 0.5, 12.0), **overrides) -> "ParticleEffect":
        """雪花 / 飘絮：在一个盒子里不断生成、慢慢下落、随风左右飘。"""
        effect = cls(
            lifetime=9.0, lifetime_variation=0.3,
            rate=60.0, max_particles=1200,
            speed=0.7, speed_variation=0.5, direction=(0.0, -1.0, 0.0),
            gravity=(0.0, -0.35, 0.0), drag=0.4, turbulence=0.5,
            size=0.16, size_variation=0.5,
            alpha_curve=BezierCurve(0.6, 1.0, 1.0, 0.0),
            color=(0.92, 0.96, 1.0, 0.9),
            blend_mode=BLEND_ALPHA,
            shape=BoxEmitter(size=size))
        return effect.apply(overrides)

    @classmethod
    def fountain(cls, **overrides) -> "ParticleEffect":
        """喷泉：向上喷出后自然回落（抛物线），演示重力的标准效果。"""
        effect = cls(
            lifetime=2.0, lifetime_variation=0.25,
            rate=180.0, max_particles=900,
            speed=6.0, speed_variation=0.25, direction=(0.0, 1.0, 0.0),
            gravity=(0.0, -9.8, 0.0), drag=0.15,
            size=0.18, size_variation=0.3,
            alpha_curve=BezierCurve(0.4, 1.0, 1.0, 0.0),
            color=(0.55, 0.85, 1.0, 0.9), end_color=(0.20, 0.45, 0.90, 1.0),
            blend_mode=BLEND_ALPHA,
            shape=ConeEmitter(angle=18.0, radius=0.06))
        return effect.apply(overrides)

    @classmethod
    def pixelFire(cls, width: float = 1.0, pixels: float = 4.0,
                  pixels_per_unit: float = 32.0, rate_per_unit: float = 130.0,
                  **overrides) -> "ParticleEffect":
        """方块上的**像素火焰**：沿一条 ``width`` 宽的横线往上冒的**方点**火苗。

        为 2D 像素风调的一套参数：

        - **方点**（不贴纹理、加性混合）+ 短寿命 + **大湍流**（火苗左右乱跳，像素火的关键）；
        - 颜色走"亮黄 → 橙 → 暗红"一路烧完（``color`` / ``end_color`` / ``color_curve``），
          燃尽时还带一点向上飘的热浮力（``gravity`` 是正的）；
        - 摆在**方块**（图块 / ``Sprite2D``）的上沿就是"这块在烧"：``width`` = 发射口宽度
          （**世界单位**，默认 1.0 = 一个图块宽），粒子在这一段上均匀生成。

        :param width: 发射口宽度（世界单位）
        :param pixels: 单个火苗"像素"多大（默认 4 像素）
        :param pixels_per_unit: 换算用的 PPU（默认 32 → ``size = 4/32 = 0.125`` 世界单位）。
            ``size`` 是**世界单位** ⇒ 相机放大时火苗跟着图块一起变大（像素风里这样才一致）；
            想"永远是 4 屏幕像素"就给系统写 ``size_mode="pixels"``。
        :param rate_per_unit: 每个世界单位宽度每秒发几个（默认 130 ⇒ 一个方块 130/秒）。
            ``rate`` 是**按宽度算的**（3 格宽的火墙自动 270/秒，密度不变）——
            这也是"别让加性粒子叠成一片白"的关键（叠太多通道全到 1，画面就白了）。
        :param overrides: 其余效果参数照常覆盖（``rate`` / ``lifetime`` / ``color`` …）

        关于"为什么没有 ``alpha_curve``"：像素火是**实心方块**，亮 → 暗靠
        ``color`` → ``end_color`` 的颜色斜坡（亮黄 → 橙 → 暗红 → 近黑）走完，
        不靠淡入淡出 —— 加性混合下低 alpha 的方块会变成一片"暗暗的脏点"
        （实测：带 alpha 曲线时单个火苗只有 5%~30% 亮度，看着像噪点而不是火）。
        """
        size = max(float(pixels), 0.5) / max(float(pixels_per_unit), 1.0)
        width = max(float(width), 0.0)
        rate = max(float(rate_per_unit), 0.0) * max(width, 0.5)
        effect = cls(
            lifetime=0.5, lifetime_variation=0.45,
            rate=rate, burst=int(max(width, 1.0) * 8.0),
            max_particles=max(int(rate * 3.0), 120),
            speed=1.4, speed_variation=0.5, direction=(0.0, 1.0, 0.0),
            gravity=(0.0, 1.0, 0.0),            # 热浮力：火苗自己往上蹿
            drag=1.2, turbulence=2.4,           # 湍流大 = 像素火"乱跳"
            size=size, size_variation=0.3,
            size_curve=BezierCurve(1.0, 1.1, 0.55, 0.0),   # 先胀一下再缩小
            # 颜色斜坡：亮黄 → 橙 → 暗红（``PowerCurve(1.5)`` = 前 2/3 寿命都很亮，
            # 最后一小段才转暗红 —— 火苗"根部亮、梢部红"就是这么来的）
            color=(1.15, 0.62, 0.12, 1.0), end_color=(0.20, 0.02, 0.01, 1.0),
            color_curve=PowerCurve(1.5),
            blend_mode=BLEND_ADD,
            shape=BoxEmitter(size=(width, 0.04, 0.0)))
        return effect.apply(overrides)

    #: 预设名（``ParticleSystem.preset("fire")`` 用）
    PRESETS = ("fire", "smoke", "spark", "explosion", "snow", "fountain",
               "pixel_fire")

    #: 预设名 → 类方法名（名字里带下划线的走这里；其余按名字直接取）
    PRESET_METHODS = {"pixel_fire": "pixelFire"}

    @classmethod
    def presetFactory(cls, name: str):
        """预设名 → 造效果的类方法（未知名字抛 ``ValueError``，消息里列出全部预设）。

        名字大小写不敏感，``-`` 当 ``_`` 使；``pixel_fire`` 这类**带下划线的名字**
        走 :attr:`PRESET_METHODS` 映射到驼峰方法名（Tiled 属性里就写 ``pixel_fire``）。
        """
        key = str(name or "").strip().lower().replace("-", "_")
        method = cls.PRESET_METHODS.get(key, key)
        factory = getattr(cls, method, None)
        if key not in cls.PRESETS or factory is None:
            raise ValueError("未知的粒子预设 %r（可选: %s）"
                             % (name, ", ".join(cls.PRESETS)))
        return factory

    def __repr__(self) -> str:
        return ("<ParticleEffect %.3g/秒 寿命%.2gs 尺寸%.3g %s %s>"
                % (self.rate, self.lifetime, self.size, self.blend_mode,
                   type(self.shape).__name__))


def _vec3(value) -> tuple:
    """``(x, y, z)`` / ``glm.vec3`` → 三元组（``None`` → 零向量）。"""
    if value is None:
        return (0.0, 0.0, 0.0)
    if isinstance(value, (int, float)):
        return (float(value), 0.0, 0.0)
    return (float(value[0]), float(value[1]), float(value[2]))


def _rgba(value) -> tuple:
    """``(r, g, b)`` / ``(r, g, b, a)`` → 四元组（省略 ``a`` 就是 1）。"""
    if value is None:
        return (1.0, 1.0, 1.0, 1.0)
    channels = [float(channel) for channel in value]
    if len(channels) == 3:
        channels.append(1.0)
    if len(channels) != 4:
        raise ValueError("颜色必须是 (r, g, b) 或 (r, g, b, a)，收到 %r" % (value,))
    return tuple(channels)

