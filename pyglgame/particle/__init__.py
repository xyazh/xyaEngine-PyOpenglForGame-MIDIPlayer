"""粒子系统（``pyglgame.particle``）：发射 + 模拟 + 一次 draw call 画完。

五行走起来::

    from pyglgame.particle import ParticleEffect, ParticleSystem

    # 预设：火焰（建好就在发射）
    ParticleSystem(ParticleEffect.fire(), x=0.0, y=-1.0)

    # 或者按预设名 + 覆盖参数
    ParticleSystem.preset("spark", x=2.0, y=0.0, effect_rate=200.0)

    # 从零写一份 2D 光环
    from pyglgame.math.Curve import BezierCurve
    from pyglgame.particle import CircleEmitter, ParticleEffect as Effect

    ring = Effect(lifetime=0.8, rate=200.0, speed=2.5, drag=2.0,
                  size=0.02, color=(0.4, 0.8, 1.0, 1.0),
                  end_color=(0.1, 0.2, 1.0, 0.0), blend_mode="add",
                  shape=CircleEmitter.ring(0.3, thickness=0.02))
    ParticleSystem(ring, z=0.05)

组成
----

=======================  =========================================================
:class:`ParticleSystem`  世界里的对象：发射、模拟、回收、渲染（``update/render``）
:class:`ParticleEffect`  参数表 + 预设（fire / smoke / spark / explosion / snow / fountain）
:class:`EmitterShape`    出生位置 / 方向的分布（点 / 盒 / 球 / 圆 / 圆锥）
:class:`Particle`        一个粒子（池化复用，自己只会"动"）
:class:`ParticlePool`    定长池：只借不 new，爆发式发射也不抖
:class:`ParticleRenderer` 绘制层：``points``（GL_POINTS + SIZ）/ ``quads``（广告牌四边形）
=======================  =========================================================

约定速查
--------

- 顶点是**世界坐标**：2D 里让 ``z`` 大一点压在精灵上，3D 里可以直接用透视
  （点会天然远小近大）；
- 粒子默认走**半透明 pass**（``transparent = True``），混合模式由效果表决定
  （``"add"`` / ``"alpha"`` / ...，见 :mod:`pyglgame.render.RenderState`）；
- ``ParticleEffect`` 里的 ``size_curve`` / ``alpha_curve`` / ``color_curve`` 都是
  :mod:`pyglgame.math.Curve` 的曲线，入参是**归一化年龄** ``0~1``；
- ``seed`` 固定 → 每次运行完全一致（回归测试、录视频用）。

文档：``docs/粒子系统.md``。参考实现：``Game4.py`` 里那两套手写粒子
（音符光环 / 星尘）现在可以换成 :class:`ParticleSystem`。
"""

from .EmitterShape import (BoxEmitter, CircleEmitter, ConeEmitter, EmitterShape,
                           PointEmitter, RotatedEmitter, SphereEmitter, basisOf,
                           normalize3)
from .Particle import Particle, ParticlePool
from .ParticleEffect import PARAMETERS, ParticleEffect
from .ParticleEmitter import DEFAULT_PRESET, ParticleEmitter
from .ParticleRenderer import (SIZE_PIXELS, SIZE_RAW, SIZE_WORLD, PointRenderer,
                               QuadRenderer, ParticleRenderer, makeRenderer)
from .ParticleSystem import ParticleSystem

__all__ = [
    # 对象（放进场景就能用）
    "ParticleSystem", "ParticleEmitter",
    # 参数表 / 预设
    "ParticleEffect", "PARAMETERS", "DEFAULT_PRESET",
    # 形状
    "EmitterShape", "PointEmitter", "BoxEmitter", "SphereEmitter",
    "CircleEmitter", "ConeEmitter", "RotatedEmitter", "basisOf", "normalize3",
    # 粒子 / 池
    "Particle", "ParticlePool",
    # 绘制
    "ParticleRenderer", "PointRenderer", "QuadRenderer", "makeRenderer",
    "SIZE_WORLD", "SIZE_PIXELS", "SIZE_RAW",
]
