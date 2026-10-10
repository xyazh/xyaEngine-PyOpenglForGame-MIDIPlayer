"""兼容旧模块名：帧动画搬到了 :mod:`~pyglgame.gameobject.animation.IAnimationFrame`。

``from ...animation.AnimationFrame import AnimationFrame`` 照旧可用（同一个类对象）。
改名与"与 ``GameObject`` 组合时别抢宿主字段"的说明见
:mod:`~pyglgame.gameobject.animation.IAnimationFrame` 的模块文档。
"""

from .IAnimationFrame import AnimationFrame, IAnimationFrame

__all__ = ["AnimationFrame", "IAnimationFrame"]
