"""场景文档与运行时（``pyglgame.scene``）：**外部工具的产出 → 引擎场景**。

==============================  =========================================================
:mod:`~pyglgame.scene.registry`      类名注册表：``type`` → 构造器（+ 属性校验）
:mod:`~pyglgame.scene.SceneDocument` 场景文档：校验、索引、路径解析（**纯数据**）
:mod:`~pyglgame.scene.SceneRuntime`  运行时：把文档建成本引擎的对象（相机 + 图层 + 对象）
==============================  =========================================================

一条链走完是四行（都不碰 GL，第一次绘制才上传）::

    from pyglgame.scene import SceneRuntime

    runtime = SceneRuntime.fromTiled("res/scene/level1.json")   # 或 .fromFile("*.scene.json")
    runtime.load()                 # 建对象（App 之前 / 之后都行）
    runtime.objectNamed("lamp")    # 按名字取对象
    runtime.unload()               # 全部删除（贴图走 TextureCache，不在这里释放）

设计约定（详见 ``docs/2D帧动画与场景导入.md`` 与 ``docs/资源导入管线.md``）：

- 导入器只产出**引擎自有文档**，运行时只读这一种格式；
- 内置构造器都满足"**构造不碰 GL**"，所以文档 → 对象可以离线跑、也能单测；
- 未知 ``type`` 会报错（可逐条降级跳过），未知属性只警告并丢弃，类型不对**不吞**。
"""

from . import registry
from .SceneDocument import SceneDocument
from .SceneRuntime import SceneRuntime
from .registry import (REGISTRY, UnknownTypeError, register, registerBuiltins,
                       resolve, unregister)

__all__ = [
    "registry", "SceneDocument", "SceneRuntime",
    "REGISTRY", "UnknownTypeError", "register", "registerBuiltins", "resolve",
    "unregister",
]
