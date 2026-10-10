"""兼容旧模块名：动画基类搬到了 :mod:`~pyglgame.gameobject.animation.IAnimationBase`。

``from ...animation.AnimationBase import AnimationBase`` 照旧可用（同一个类对象），
新代码请用 ``IAnimationBase``（与 ``IAnimationFrame`` / ``ISkeletonAnimation`` 一致）。
"""

from .IAnimationBase import AnimationBase, IAnimationBase, chainCall, wrapTime

__all__ = ["AnimationBase", "IAnimationBase", "chainCall", "wrapTime"]

