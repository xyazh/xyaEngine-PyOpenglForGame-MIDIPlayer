"""Object ID 注册表：给每个 :class:`~pyglgame.gameobject.GameObject.GameObject`
分配一个唯一整数 ID，并支持按 ID 反查对象（GPU Picking 的最后一环）。

闭环::

    GameObject(唯一 ID)  --ID pass(fuc == OBJECT_ID)-->  ObjectIdBuffer(每像素一个 ID)
        --glReadPixels(1x1)-->  ID  --ObjectIdRegistry.get(ID)-->  GameObject

设计要点：

- ID 从 1 开始、单调递增、**永不复用**；``0`` 保留给"背景 / 这里没有物体"。
- 用 :class:`weakref.WeakValueDictionary` 存对象：对象被 GC 后按 ID 查会得到
  ``None``，注册表不会拖住已经没人引用的对象（不会造成内存泄漏）。
- :data:`MAX_OBJECT_ID` 取 ``2^24``：``ObjectIdBuffer`` 是 ``GL_R32F``，float32
  能**精确**表示的最大连续整数就是 ``2^24``，超过这个范围 ID 会开始丢精度。
"""

import weakref
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..gameobject.GameObject import GameObject

#: ID 上限（2^24）：float32 能精确表示的最大连续整数（见 ObjectIdBuffer）
MAX_OBJECT_ID = 1 << 24


class ObjectIdRegistry:
    """ID 分配 / 反查（全进程一张表，纯 Python，不碰 GL）。"""

    _next_id: int = 1
    #: ID → 对象；弱引用，对象被 GC 后自动消失（不拖住对象）
    _objects: "weakref.WeakValueDictionary[int, GameObject]" = \
        weakref.WeakValueDictionary()

    @classmethod
    def register(cls, obj: "GameObject") -> int:
        """给对象分配一个新 ID 并登记；由 ``GameObject.__new__`` 调用。"""
        object_id = cls._next_id
        cls._next_id += 1
        if cls._next_id >= MAX_OBJECT_ID:
            # 回绕到 1 继续分配（正常游戏跑不到这里；不回绕成 0，0 是背景）
            cls._next_id = 1
        cls._objects[object_id] = obj
        return object_id

    @classmethod
    def unregister(cls, object_id: int) -> None:
        """注销一个 ID（由 ``GameObject.delete`` 调用）。"""
        try:
            cls._objects.pop(int(object_id), None)
        except (TypeError, ValueError):
            pass

    @classmethod
    def get(cls, object_id: int) -> "GameObject":
        """按 ID 取回对象；``0`` / 未登记 / 对象已被回收 → ``None``。"""
        try:
            key = int(object_id)
        except (TypeError, ValueError):
            return None
        if key <= 0:
            return None
        return cls._objects.get(key)

    @classmethod
    def objects(cls) -> list["GameObject"]:
        """当前登记且仍存活的对象，按 ID 升序（``GameObject.getObjests`` 遍历用）。"""
        return [cls._objects[key] for key in sorted(cls._objects)]

    @classmethod
    def reset(cls) -> None:
        """清空注册表并重置计数器（单测用；正常运行不需要）。"""
        cls._next_id = 1
        cls._objects.clear()

    @classmethod
    def count(cls) -> int:
        """当前登记且仍然存活的对象数（调试用）。"""
        return len(cls._objects)
