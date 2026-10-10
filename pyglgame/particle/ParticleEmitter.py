"""场景里直接摆的**粒子生成器**（Tiled 里 ``type = ParticleEmitter``）。

Tiled 里怎么用
--------------

在对象层放一个对象，**类型写 ``ParticleEmitter``**，自定义属性就是构造参数（与场景文档
里其它类同一套约定）：

.. code-block:: json

    {"type": "ParticleEmitter", "name": "fire", "x": 96, "y": 128,
     "properties": [
       {"name": "preset", "type": "string", "value": "pixel_fire"},   // 预设（默认就是它）
       {"name": "width",  "type": "float",  "value": 1.0},            // 发射口宽（世界单位）
       {"name": "rate",   "type": "float",  "value": 180},            // 覆盖预设的每秒粒数
       {"name": "size_mode", "type": "string", "value": "pixels"}]}   // 想固定像素大小才写

``x`` / ``y`` 由导入器从 Tiled 的像素坐标换算成世界坐标（对象中心）；``r``（角度）
**会**把出生位置与喷射方向一起绕 Z 转（竖着的火把转成横的也照样对）。

参数怎么分流
------------

==================================  ==========================================================
``preset`` / ``effect``             预设名（``pixel_fire`` / ``fire`` / ``smoke`` / ``spark`` /
                                    ``explosion`` / ``snow`` / ``fountain``）；``effect`` 还能直接
                                    给一份 ``dict`` 参数表或现成的 ``ParticleEffect``
其余**效果参数**                      原样覆盖预设里的同名项（``rate`` / ``lifetime`` / ``size`` /
                                    ``color`` / ``end_color`` / ``speed`` / ``gravity`` /
                                    ``turbulence`` / ``width`` …，名字写错会报错并列出可选项）；
                                    也认 ``effect_`` 前缀（``effect_rate=…``）
``shape`` + ``shape_*``               形状快捷方式：``point`` / ``box`` / ``circle`` /
                                    ``sphere`` / ``cone``，配合 ``shape_size`` /
                                    ``shape_radius`` / ``shape_angle`` / ``shape_inner_radius`` /
                                    ``shape_surface`` / ``shape_radial``（不写就用预设的形状）
``capacity`` / ``render_mode`` /     系统参数，原样转给 :class:`ParticleSystem`
``size_mode`` / ``texture`` / ``seed`` /
``auto_play`` / ``name`` / ``x`` / ``y`` / ``z``
==================================  ==========================================================

⚠ **Tiled 的对象形状不冲突**：导入器写在条目里的 ``shape``（``rect`` / ``point`` /
``tile`` / ``ellipse`` / ``polygon`` / ``polyline``）是**对象的几何形状**，会被忽略 ——
发射形状仍然由预设或显式的 ``shape=box`` 这类值决定。``source_id`` / ``template`` /
``uv`` / ``points`` / ``height`` 这些**书签与几何字段**同样会被静默忽略
（见 :func:`effectParams`）。

::

    from pyglgame.particle import ParticleEmitter

    ParticleEmitter(preset="pixel_fire", x=0.0, y=-2.0, z=0.2, width=1.0)

⚠ 2D 里的 ``z``：默认 ``0.05``（压在方块/精灵上面）。3D 里想要精确位置就显式写 ``z``。
⚠ ``pixel_fire`` 的 ``size`` 是**世界单位**（默认 4px @ 32px/单位 = 0.125）：相机放大时
它跟图块一起变大。要"永远 4 屏幕像素"就写 ``size_mode="pixels"``。
"""

from .EmitterShape import (BoxEmitter, CircleEmitter, ConeEmitter, PointEmitter,
                           RotatedEmitter, SphereEmitter)
from .ParticleEffect import ParticleEffect
from .ParticleSystem import ParticleSystem

#: 不写 ``preset`` 时用哪个（2D 像素风的"方块在烧"）
DEFAULT_PRESET = "pixel_fire"

#: ``shape=`` 快捷方式认的名字
SHAPE_NAMES = ("point", "box", "circle", "sphere", "cone")

#: Tiled 写在对象 ``shape`` 里的**对象形状**（导入器加的几何字段）—— 与"发射形状"同名，
#: 遇到它们就当作者没写（发射形状还是用预设自带的）。想强制某个形状就在代码里传
#: ``EmitterShape`` 对象，或写一个不在这个名单里的名字（``box`` / ``cone`` …）。
TILED_SHAPES = ("tile", "rect", "rectangle", "ellipse", "polygon", "polyline",
                "point")

#: 本类自己消费掉的场景字段（其余 ``RESERVED_KEYS`` 都不进效果参数）
CONSUMED_KEYS = frozenset({"x", "y", "z", "r", "name", "shape", "width", "texture",
                           "capacity", "render_mode", "size_mode", "seed",
                           "auto_play"})

#: ``RESERVED_KEYS`` 之外还要丢掉的那些（图块对象的几何：它们是给 ``Sprite2D`` 用的）
EXTRA_IGNORED_KEYS = frozenset({"uv", "gid", "flip_x", "flip_y", "pivot",
                                "world_rect"})


def resolveEffect(effect=None, preset=None, overrides=None) -> ParticleEffect:
    """``effect`` / ``preset`` + 参数覆盖 → 一份 :class:`ParticleEffect`。

    ``effect`` 可以是：预设名（与 ``preset`` 等价）、``dict``（一份完整参数表）、
    现成的 ``ParticleEffect``（会 ``copy()`` 一份，不动调用方那份）；
    ``overrides`` 里 ``effect_`` 前缀会被去掉（与 ``ParticleSystem.preset`` 一致）。
    """
    overrides = dict(overrides or {})
    for key in [key for key in overrides if key.startswith("effect_")]:
        overrides[key[len("effect_"):]] = overrides.pop(key)
    if isinstance(effect, ParticleEffect):
        return effect.copy(**overrides) if overrides else effect
    if isinstance(effect, dict):
        return ParticleEffect(**effect).apply(overrides)
    name = effect if effect is not None else (preset or DEFAULT_PRESET)
    return ParticleEffect.presetFactory(name)(**overrides)


def effectParams(params: dict) -> dict:
    """把场景条目里的 ``**params`` 过滤成**效果参数**。

    场景文档 / Tiled 导入器会给条目塞一批**几何与书签**字段（``source_id`` /
    ``template`` / ``uv`` / ``points`` / ``height`` / ``opacity`` …），它们不是粒子
    参数，落到 :meth:`ParticleEffect.apply` 会变成"未知参数"报错。这里按
    :data:`pyglgame.scene.registry.RESERVED_KEYS` 把它们滤掉（本类自己消费的那几个
    已在 ``__init__`` 的签名里，进不到这里），**其余照旧原样透传** —— 名字真写错了
    还是会当场报错并列出可选项。
    """
    from ..scene.registry import RESERVED_KEYS       # 延迟导入：粒子包不依赖场景包

    ignored = (RESERVED_KEYS - CONSUMED_KEYS) | EXTRA_IGNORED_KEYS
    return {key: value for key, value in params.items() if key not in ignored}


def resolveShape(shape=None, size=None, radius=None, angle=None,
                 inner_radius=None, surface: bool = False,
                 radial: bool = False):
    """``shape="box"`` 这类快捷方式 → :class:`~pyglgame.particle.EmitterShape`。

    - ``None`` → ``None``（保持预设自带的形状）；
    - 已经是一个形状对象 → 原样返回（想精确控制就自己传对象）；
    - 名字不认识 → ``ValueError``（早失败，别默默用错形状）。
    """
    if shape is None:
        return None
    if not isinstance(shape, str):
        if hasattr(shape, "sample"):
            return shape
        raise ValueError("shape 要么是形状对象，要么是名字 %s，收到 %r"
                         % (", ".join(SHAPE_NAMES), shape))
    key = shape.strip().lower()
    if key in TILED_SHAPES:
        # 这是 Tiled 的**对象形状**（导入器写的几何字段），不是"发射形状" → 当没写
        return None
    if key not in SHAPE_NAMES:
        raise ValueError("未知的发射形状 %r（可选: %s；Tiled 的对象形状 %s 会被忽略）"
                         % (shape, ", ".join(SHAPE_NAMES),
                            " / ".join(TILED_SHAPES)))
    if key == "point":
        return PointEmitter(jitter=float(radius or 0.0))
    if key == "box":
        return BoxEmitter(size=size or (1.0, 1.0, 0.0))
    if key == "circle":
        return CircleEmitter(radius=float(radius if radius is not None else 1.0),
                             inner_radius=float(inner_radius or 0.0))
    if key == "sphere":
        return SphereEmitter(radius=float(radius if radius is not None else 1.0),
                             surface=bool(surface), radial=bool(radial))
    return ConeEmitter(angle=float(angle if angle is not None else 25.0),
                       radius=float(radius or 0.02))


class ParticleEmitter(ParticleSystem):
    """Tiled 里可直接摆放的粒子生成器（见模块文档）。"""

    def __new__(cls, *args, **kwargs):
        # ⚠ GameObject.__new__ 的第二个位置参数是 is_camera：构造参数必须在这里吃掉
        return super().__new__(cls)

    def __init__(self, effect=None, preset: str = None, x: float = 0.0,
                 y: float = 0.0, z: float = 0.05, r: float = 0.0,
                 capacity: int = None, render_mode="points", size_mode=None,
                 texture=None, seed=None, auto_play: bool = True,
                 name: str = None, shape=None, shape_size=None, shape_radius=None,
                 shape_angle=None, shape_inner_radius=None,
                 shape_surface: bool = False, shape_radial: bool = False, **params) -> None:
        """
        :param effect: 预设名 / 参数表 ``dict`` / 现成的 ``ParticleEffect``
        :param preset: 预设名（``effect`` 没给时用它；都没给 = ``pixel_fire``）
        :param z: 2D 里默认 ``0.05``，压在方块 / 精灵上面
        :param r: 绕 Z 的角度（度）：出生位置与喷射方向一起转（Tiled 里转对象就是它）
        :param shape: 形状快捷方式（``point`` / ``box`` / ``circle`` / ``sphere`` / ``cone``）
        :param params: 其余**效果参数**（``rate`` / ``size`` / ``color`` …，也可带 ``effect_`` 前缀）；
            场景条目里那些**几何 / 书签**字段（``source_id`` / ``uv`` / ``points`` / ``height`` …）
            会被静默忽略（见 :func:`effectParams`）
        """
        resolved = resolveEffect(effect, preset, effectParams(params))
        shape_object = resolveShape(shape, size=shape_size, radius=shape_radius,
                                    angle=shape_angle,
                                    inner_radius=shape_inner_radius,
                                    surface=shape_surface, radial=shape_radial)
        if shape_object is not None:
            resolved = resolved.copy(shape=shape_object)
        if float(r) % 360.0:                     # 对象转了角度 → 形状整体跟着转
            resolved = resolved.copy(
                shape=RotatedEmitter(resolved.shape, float(r)))
        super().__init__(resolved, x=x, y=y, z=z, capacity=capacity,
                         render_mode=render_mode, size_mode=size_mode,
                         texture=texture, seed=seed, auto_play=auto_play,
                         name=name)
        #: 这个生成器的角度（度）—— 与 Tiled 对象的 ``r`` 一致
        self.r = float(r)

    @classmethod
    def onBlock(cls, block, preset: str = DEFAULT_PRESET, offset=None,
                gap: float = 0.02, **params) -> "ParticleEmitter":
        """在**方块的上沿**装一个像素火焰（方块动，火跟着动）—— "这块在烧"只要一行。

        :param block: 任意有 ``x`` / ``y`` 的对象（``Sprite2D`` / ``BodySprite`` …；
            ``width`` / ``height`` 有就用，没有按 1.0）
        :param preset: 预设名（默认 ``pixel_fire``；``width`` 只有它会自动带上）
        :param offset: 整个偏移（默认 ``(0, height/2 + gap)`` = 上沿）
        :param gap: 往上抬一点（默认 0.02，免得火苗被方块自己的边切掉）
        :param params: 其余效果参数（``rate`` / ``pixels`` / ``lifetime`` …）

        ::

            block = Sprite2D(texture, width=1.0, height=1.0, x=0.0, y=-2.0)
            ParticleEmitter.onBlock(block)

        内部用的是 :meth:`ParticleSystem.attachTo`：方块移动 / 被物理推着走时，火跟着走。
        """
        import inspect

        width = float(getattr(block, "width", 1.0) or 1.0)
        height = float(getattr(block, "height", 1.0) or 1.0)
        if "width" not in params:
            factory = ParticleEffect.presetFactory(preset)
            if "width" in inspect.signature(factory).parameters:
                params["width"] = width
        if offset is None:
            offset = (0.0, height * 0.5 + float(gap), 0.0)
        emitter = cls(preset=preset, x=float(getattr(block, "x", 0.0)),
                      y=float(getattr(block, "y", 0.0)),
                      z=float(getattr(block, "z", 0.0) or 0.0) + 0.1, **params)
        emitter.attachTo(block, offset=offset)
        return emitter

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return ("<ParticleEmitter%s %.3g/秒 寿命%.2gs 尺寸%.3g %s 形状%s at "
                "(%.2f, %.2f, %.2f) r=%g>"
                % (label, self.effect.rate, self.effect.lifetime, self.effect.size,
                   self.effect.blend_mode, type(self.effect.shape).__name__,
                   self.x, self.y, self.z, self.r))
