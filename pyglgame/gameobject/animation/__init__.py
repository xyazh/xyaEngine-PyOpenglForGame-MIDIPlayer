"""2D 动画系统（``pyglgame.gameobject.animation``）：**接口类 + 组合式编程**。

三个接口都以 ``I`` 开头（Python 允许实方法，所以它们是"带默认实现的接口"），
**都不继承 ``GameObject``**，因此可以两种用法随便选：

.. code-block:: python

    # ① 多继承：当世界里的对象（接口写在**左边**！）
    class Player(IAnimationFrame, Sprite2D):
        def __init__(self, sheet, x=0.0, y=0.0):
            super().__init__(rows=2, columns=4, frame_count=8,
                             resourceLocation=sheet, time=0.8,
                             x=x, y=y)          # x / y 会透传给 Sprite2D

    # ② 持有：当对象的零件
    class Torch(GameObject):
        def __init__(self):
            super().__init__()
            self.anim = IAnimationFrame(1, 6, 6, "res/img/1-n.png")

        def update(self, dt, tps):
            self.anim.update(dt, tps)

        def render(self, dt, fps):
            self.anim.render(dt, fps)

接口写在左边是**硬约定**：``GameObject.update`` / ``Sprite2D.render`` 这类宿主方法不调用
``super()``，接口排在前面才能把宿主当成链的下游来调（写反了不报错，只是动画不动）。

清单
----

===========================  ==========================================================
:class:`IAnimationBase`      **动画基类**：时间轴、播放控制、协作式 ``update`` / ``render``
:class:`IAnimationFrame`     **帧动画**：精灵表切帧（旧 ``AnimationFrame`` 的改造版）
:class:`ISkeletonAnimation`  **骨骼动画**：骨架 + 剪辑 + 挂点，每帧解算并分批绘制
:class:`Skeleton` / `Bone`   骨骼数据层：父子层级、局部 / 世界变换、``fromDict``
:class:`AnimationClip`       剪辑数据层：``Keyframe`` → ``BoneTrack`` → 剪辑（+ 事件）
:class:`BoneAttachment`      挂点：一根骨 + 一张贴图 = 一个会跟着骨动的四边形
===========================  ==========================================================

数据层（``Skeleton`` / ``AnimationClip`` / ``BoneAttachment``）不叫 ``I...``：它们不是
"给别的类组合用的接口"，而是**可序列化的纯数据**（``fromDict`` / ``toDict`` 直接吃
AIGC / 编辑器导出的 JSON）。

常见动作
-------

::

    anim.play() / pause() / stop() / restart() / togglePlay()
    anim.seek(0.4) / setSpeed(2.0) / setLoop(False)
    anim.progress / anim.animation_time / anim.duration / anim.finished
    anim.on_finish = lambda a: print("播完了")     # on_loop / on_frame 同理

帧动画：``anim.frameIndex`` / ``anim.frameUV()`` / ``anim.driveSprite(sprite)``；
骨骼动画：``anim.playClip("run")`` / ``anim.setBone("arm", rotation=-30)`` /
``anim.addAttachment(...)`` / ``anim.setAttachmentVisible("sword", False)``。

文档：``docs/2D动画系统.md``。
"""

from .AnimationClip import (CHANNELS, EASINGS, AnimationClip, BoneTrack,
                            Keyframe, normalizeEasingName, resolveEasing)
from .BoneAttachment import BoneAttachment
# 旧名字（``AnimationBase`` / ``AnimationFrame``）也给一份"包顶层别名"，方便老代码
# ``from pyglgame.gameobject.animation import AnimationFrame`` 直接迁移。
# ⚠ 但子模块和旧类同名：一旦有人 ``import pyglgame.gameobject.animation.AnimationFrame``
# （或跑了 tests/test_imports.py 那种"扫包导入"），Python 会把**包属性**覆盖成模块对象
# —— 于是这个别名"是不是类"取决于导入顺序。**要稳就用旧模块路径**（那个永远给类）：
#
#     from pyglgame.gameobject.animation.AnimationFrame import AnimationFrame
#
from .FrameSequence import FrameSequence
from .IAnimationBase import (AnimationBase, IAnimationBase, chainCall, wrapTime)
from .IAnimationFrame import AnimationFrame, IAnimationFrame
from .IAtlasAnimation import IAtlasAnimation
from .ISkeletonAnimation import ISkeletonAnimation
from .Skeleton import Bone, Skeleton

__all__ = [
    # 接口（可组合）
    "IAnimationBase", "IAnimationFrame", "ISkeletonAnimation",
    # 图集帧动画（LibreSprite / Aseprite 导出的图集直接能播）
    "IAtlasAnimation", "FrameSequence",
    # 骨骼 / 剪辑 / 挂点
    "Skeleton", "Bone", "AnimationClip", "BoneTrack", "Keyframe",
    "BoneAttachment", "EASINGS", "CHANNELS", "resolveEasing",
    "normalizeEasingName",
    # 协作式接驳用的工具（自己写接口时用得上）
    "chainCall", "wrapTime",
]


