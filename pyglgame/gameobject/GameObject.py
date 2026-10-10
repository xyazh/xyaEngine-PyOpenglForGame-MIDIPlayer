"""GameObject：渲染层里所有可更新 / 可渲染对象的基类。

对象在 ``__new__`` 里就完成注册（分配唯一 ID + 进渲染层），所以子类即使不调
``super().__init__()`` 也能正常参与引擎循环。ID 分配与反查见
``pyglgame/render/ObjectIdRegistry.py``。
"""

from typing import TYPE_CHECKING

from ..RenderGlobal import RenderGlobal
from ..render.ObjectIdRegistry import ObjectIdRegistry
if TYPE_CHECKING:
    from ..RenderLayer import RenderLayer


class GameObject:
    """一个场景对象：位置 / 可见性 / 生命周期 + 唯一 ID 与名字。"""

    #: 唯一 ID（Object ID / GPU Picking 用；``0`` 保留给背景）
    object_id: int
    #: 名字：**允许重复**，只用于查询（见 :meth:`getObjests`）
    name: str
    #: 所属渲染层（注册时由 RenderGlobal 设置）
    _render_layer: "RenderLayer"
    #: 是否已注销（让 :meth:`delete` 可重复调用）
    _deleted: bool

    def __new__(cls, is_camera: bool = False, *args, **kwargs) -> "GameObject":
        # 其余构造参数只给 __init__ 用，在这里吃掉（否则会被当成 is_camera）
        obj = super().__new__(cls)
        obj._render_layer = None
        obj._deleted = False
        obj.name = None
        # ID 在 __new__ 里分配：子类不调 super().__init__() 也一定拿得到
        obj.object_id = ObjectIdRegistry.register(obj)
        RenderGlobal.instance.addToLayer(obj, is_camera)
        return obj

    def __init__(self, name: str = None) -> None:
        self.name = name
        self.active: bool = True
        self.x: float = 0
        self.y: float = 0
        self.z: float = 0
        self.p: float = 0
        self.t: float = 0
        self.r: float = 0
        self.started: bool = False
        self.render_global: RenderGlobal = RenderGlobal.instance
        # 半透明物体：走半透明 pass（关深度写入 + 按相机深度远→近排序）。
        # ⚠ alpha=1 的内容不要标 —— 不写深度它就不再遮挡后面的物体了：要么把不透明
        # 的部分拆成独立对象，要么给它的 RenderBuffer 设 depth_write=True。
        self.transparent: bool = False
        #: 深度相同时的兜底排序权重（大的先画）
        self.render_order: int = 0

    @property
    def layer(self) -> "RenderLayer":
        """本对象所属的渲染层（由 RenderGlobal 在注册时设置）。"""
        return self._render_layer

    @staticmethod
    def getObjests(object_id: int = None, type: type = None,
                   name: str = None) -> list["GameObject"]:
        """按 ``object_id`` / ``type`` / ``name`` 查对象，条件之间是"与"。

        - 三个都不传 = 当前所有存活对象；查不到 = 空列表。
        - ``type`` 传类，按 ``isinstance`` 判断（传基类可拿到所有子类实例）。
        - ``name`` **允许重复**，所以结果**永远是列表**（按 ``object_id`` 升序）。
        """
        if object_id is None:
            found = ObjectIdRegistry.objects()
        else:
            hit = ObjectIdRegistry.get(object_id)
            found = [] if hit is None else [hit]
        if type is not None:
            found = [obj for obj in found if isinstance(obj, type)]
        if name is not None:
            found = [obj for obj in found if getattr(obj, "name", None) == name]
        return found

    def delete(self) -> None:
        """从渲染层 / 窗口相机列表注销，并停止更新与渲染（可重复调用）。

        ⚠ 不依赖 ``RenderGlobal.using_layer``：对象可能注册在别的层上。
        """
        if self._deleted:
            return
        self._deleted = True
        self.active = False
        layer = self._render_layer
        if layer is not None:
            RenderGlobal.instance.removeFromLayer(self, layer.isCamera(self))
        else:
            RenderGlobal.instance.removeFromLayer(self)
        # 注销 GPU Picking 的反查表
        ObjectIdRegistry.unregister(getattr(self, "object_id", 0))
        self.onRemoved()

    def onRemoved(self) -> None:
        """对象被注销时的回调（子类用它释放自己持有的 GPU 资源）。"""

    def setActive(self, active: bool) -> None:
        self.active = active

    def start(self) -> None:
        self.started = True

    def update(self, dt: float, tps: float) -> None:
        pass

    def render(self, dt: float, fps: float) -> None:
        pass

    def _render(self, dt: float, fps: float) -> None:
        pass

    def onHover(self, x: float, y: float) -> None:
        pass

    def onClick(self, x: float, y: float) -> None:
        pass