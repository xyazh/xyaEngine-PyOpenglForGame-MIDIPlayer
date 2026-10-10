"""场景运行时（``SceneRuntime``）：把场景文档**变成世界里的对象**。

职责边界
--------

========================================  ==============================================
:class:`~pyglgame.scene.SceneDocument`     "文档是什么"：校验、索引、路径解析（纯数据）
``SceneRuntime``（本模块）                  "文档变成什么"：建相机 / 建对象 / 卸载重载
:class:`~pyglgame.scene.registry`          "某个 type 用哪个构造器"
========================================  ==============================================

两条**硬约定**（都来自引擎本身的设计）：

1. **构造不碰 GL**：所有内置构造器（``Sprite2D`` / ``TileMapLayer`` / ``light2D`` …
   直到动画的 ``IAtlasAnimation``）都只在绘制时才上传纹理与 VBO，所以 ``load()``
   可以在没有窗口的时候调用（离线校验、单测都能跑）。
2. **新增对象由渲染层在帧边界统一 start()**：``RenderLayer`` 已经实现了"运行时新增的
   对象下一帧再 start()"，所以这里只管建对象，不用管"什么时候开始画"。

用法
----

.. code-block:: python

    from pyglgame.scene.SceneDocument import SceneDocument
    from pyglgame.scene.SceneRuntime import SceneRuntime

    # ① 直接读引擎自有格式
    runtime = SceneRuntime.fromFile("res/scene/level1.scene.json")
    # ② 或者从 Tiled 的 JSON 一步到位
    runtime = SceneRuntime.fromTiled("res/scene/level1.json")

    runtime.load()          # 建对象（可以在 App 之前 / 之后）
    ...
    runtime.unload()        # 全部删除（贴图走 TextureCache，不在这里释放）
    runtime.reload()        # 卸载再加载（编辑器的"热重载"就是它）
"""

from . import registry
from .SceneDocument import SceneDocument


class SceneRuntime:
    """场景文档 → 世界里的对象（见模块文档）。"""

    def __init__(self, document=None, app=None, strict: bool = False,
                 withCamera: bool = True, autoLoad: bool = False) -> None:
        """
        :param document: :class:`SceneDocument` 或它的 dict（``None`` = 之后再用 ``fromFile`` 装）
        :param app: 可选的 :class:`~pyglgame.App.App`（只是留个把手，运行时用不到）
        :param strict: ``True`` = 任何一个条目建不出来就整体失败（默认"跳过并警告"）
        :param withCamera: 是否按文档的 ``camera`` 建相机
        :param autoLoad: 构造时立刻 :meth:`load`
        """
        self._document = None
        self.document = document
        self.app = app
        self.strict = bool(strict)
        self.withCamera = bool(withCamera)
        #: 建出来的相机（``None`` = 文档没写 / 关掉了）
        self.camera = None
        #: 建出来的**全部**对象（含相机）
        self.items = []
        #: 建出来的非相机对象
        self.objects = []
        #: 名字 → 对象（文档里写了 ``name`` 的才有）
        self.named = {}
        #: 条目的图块集与图集缓存（名字 → 对象）
        self.tilesets = {}
        self.atlases = {}
        #: 被跳过的条目（文档里不支持 / 注册表里没有）
        self.skipped = []
        #: ``(条目, 异常)``：建失败但被跳过的
        self.errors = []
        #: 物理世界（``Physics2D``）；文档没写 ``physics`` 段 / 后端不可用时是 ``None``
        self.world = None
        if autoLoad:
            self.load()

    # === 文档 ===

    @property
    def document(self):
        """当前场景文档（:class:`SceneDocument`）。"""
        return self._document

    @document.setter
    def document(self, value) -> None:
        if value is None or isinstance(value, SceneDocument):
            self._document = value
        elif isinstance(value, dict):
            self._document = SceneDocument.fromDict(value)
        else:
            raise TypeError("document 必须是 SceneDocument 或它的 dict，收到 %r"
                            % (type(value).__name__,))

    @classmethod
    def fromFile(cls, path: str, **kwargs) -> "SceneRuntime":
        """读引擎自有格式的 ``*.scene.json`` 并建运行时（不自动 load）。"""
        return cls(SceneDocument.fromFile(path), **kwargs)

    @classmethod
    def fromTiled(cls, source, **kwargs) -> "SceneRuntime":
        """从 Tiled 的 JSON（dict 或路径）一步到位。"""
        options = {key: kwargs.pop(key) for key in list(kwargs)
                   if key in ("pixels_per_unit", "default_object_type", "include_hidden",
                              "with_camera", "name", "base_dir")}
        with_camera = options.pop("with_camera", True)
        document = SceneDocument.fromTiled(source, **options)
        kwargs.setdefault("withCamera", with_camera)
        return cls(document, **kwargs)

    # === 加载 / 卸载 ===

    def load(self) -> list:
        """建相机 + 建全部条目；返回建出来的对象列表（**不碰 GL**）。

        单个条目失败时：``strict=False``（默认）会警告并跳过（记在 :attr:`errors` 里），
        ``strict=True`` 会把已经建好的对象全部回滚再抛错。
        """
        document = self.document
        if document is None:
            raise ValueError("SceneRuntime 还没有场景文档（用 document=… 或 fromFile/fromTiled）")
        self.unload()
        self.skipped = []
        self.errors = []
        textures = document.resolvedTextures()
        self.tilesets = document.tilesetsByName()
        self.atlases = document.buildAtlases()
        #: 物理世界要在**建对象之前**准备好 —— ``Body2D`` 的工厂靠 context 拿到它
        self.world = self.buildPhysics(document.physics)

        if self.withCamera:
            self.camera = self.buildCamera(document.camera)
            if self.camera is not None:
                self.items.append(self.camera)

        for kind, entry in document.entries():
            try:
                obj = self.buildEntry(kind, entry, textures, document)
            except Exception as error:                     # noqa: BLE001 - 逐条降级
                if self.strict:
                    self.unload()
                    raise
                self.errors.append((entry, error))
                from ..xyazhServer.ConsoleMessage import ConsoleMessage
                ConsoleMessage.printWarning(
                    "场景条目 %r（%s）建不出来，已跳过: %s: %s"
                    % (entry.get("name") or entry.get("type"), kind,
                       type(error).__name__, error))
                continue
            if obj is None:
                self.skipped.append(entry)
                continue
            self.items.append(obj)
            if kind != "camera":
                self.objects.append(obj)
            name = entry.get("name")
            if name:
                self.named[str(name)] = obj
        return self.items

    def unload(self) -> int:
        """删掉自己建出来的**全部**对象（相机也删），返回删掉的个数。"""
        from ..xyazhServer.ConsoleMessage import ConsoleMessage

        removed = 0
        for obj in self.items:
            try:
                obj.delete()
                removed += 1
            except Exception as error:                     # noqa: BLE001 - 卸载要尽力而为
                ConsoleMessage.printWarning("卸载对象 %r 失败: %s" % (obj, error))
        self.items = []
        self.objects = []
        self.named = {}
        self.camera = None
        #: 物理世界**最后**删：它的 clear 会连带销毁刚体 —— 先删对象、再删世界，顺序不能反
        if self.world is not None:
            try:
                self.world.delete()
            except Exception as error:                     # noqa: BLE001
                ConsoleMessage.printWarning("卸载物理世界失败: %s" % error)
            self.world = None
        return removed

    def reload(self) -> list:
        """卸载再加载（编辑器改完文档后的"热重载"）。"""
        return self.load()

    @property
    def running(self) -> bool:
        """当前有没有加载过（建出来的对象非空）。"""
        return bool(self.items)

    def objectNamed(self, name: str):
        """按名字取对象（文档里没写名字的话返回 ``None``）。"""
        return self.named.get(str(name))

    # === 建对象 ===

    def buildCamera(self, spec=None):
        """按文档的 ``camera`` 建相机：``MSimple2D``（默认）/ ``MPerspective``。

        支持的字段（都可以不写）::

            {"projection": "MSimple2D", "size": [20.0, 15.0],   // → setBySize
             "position": [0.0, 0.0, 30.0],                      // 相机在世界里的位置
             "zoom": 1.0, "rotation": 0.0,                      // → setZoom / setRotation
             "use_lighting": true, "use_shadow2d": false, "use_bloom": false,
             "msaa": 4, "switch": true}                         // 最后一项 = 加到窗口相机列表

        ⚠ 2D 相机的 ``position.z`` 要比场景里的东西大（默认 30）：引擎的 2D 深度是
        "``z`` 越大越靠近相机"，所以相机得站在 Z 轴正方向"往下看"。
        """
        from ..gameobject.camera.Camera import Camera

        spec = dict(spec or {})
        projection = str(spec.get("projection") or "MSimple2D")
        camera = Camera(msaa_val=int(spec.get("msaa", 4) or 4))
        position = list(spec.get("position") or (0.0, 0.0, 30.0))
        while len(position) < 3:
            position.append(30.0 if len(position) == 2 else 0.0)
        camera.x, camera.y, camera.z = (float(position[0]), float(position[1]),
                                       float(position[2]))
        if spec.get("rotation"):
            camera.r = float(spec["rotation"])
        if projection == "MPerspective":
            from ..gameobject.camera.m.MPerspective import MPerspective
            model = MPerspective()
            aspect = spec.get("aspect")
            if aspect:
                model.updateAspectFromSize(float(aspect[0]), float(aspect[1]))
            camera.setProjection(model)
        else:
            from ..gameobject.camera.m.MSimple2D import MSimple2D
            model = MSimple2D()
            size = spec.get("size")
            if size:
                model.setBySize(float(size[0]), float(size[1]))
            if spec.get("zoom"):
                model.setZoom(float(spec["zoom"]))
            if spec.get("rotation"):
                model.setRotation(float(spec["rotation"]))
            if spec.get("use_lighting"):
                model.useLighting(True)
            camera.setProjection(model)
        if spec.get("use_shadow2d"):
            camera.useShadow2D(True)
        if spec.get("use_bloom"):
            camera.useBloom(True)
        if spec.get("switch", True):
            camera.switch()
        return camera

    def buildPhysics(self, spec=None):
        """按文档的 ``physics`` 段建 2D 物理世界（``None`` = 不建）。

        ::

            "physics": {"gravity": [0.0, -22.0],     // 横板 ACT：重力往下
                        "fixed_step": 0.0166667,     // 固定步长（默认 1/60）
                        "max_steps": 5,              // 单帧最多补几步（防死亡螺旋）
                        "do_sleep": true,            // 允许刚体休眠（省 CPU）
                        "name": "act1"}

        后端（Box2D）**只有 Python 3.10 能加载**：不可用时这里给一次警告并返回 ``None``，
        于是"带物理的场景"在别的解释器上仍然打得开 —— 只是 ``Body2D`` 会被跳过
        （记进 :attr:`errors`，不会让整个场景失败）。
        """
        if spec is None:
            return None
        from ..physics import Physics2D, available

        if not available():
            from ..xyazhServer.ConsoleMessage import ConsoleMessage
            ConsoleMessage.printWarning(
                "物理后端不可用（Box2D 只有 Python 3.10 能加载：pyglgame/libs/Box2D）→ "
                "场景里的 physics 段与 Body2D 都会被跳过")
            return None
        gravity = spec.get("gravity") or (0.0, -9.8)
        return Physics2D(gravity=(float(gravity[0]), float(gravity[1])),
                         fixed_step=float(spec.get("fixed_step", 1.0 / 60.0)),
                         max_steps=int(spec.get("max_steps", 5)),
                         do_sleep=bool(spec.get("do_sleep", True)),
                         name=spec.get("name"))

    def context(self) -> dict:
        """交给工厂的**共享上下文**（目前只有物理世界 ``world``）。

        ``registry.build`` 只把它注入到"签名里明确有该参数"的工厂
        （``Body2D`` 就靠它拿到世界），其余工厂不受影响。
        """
        return {"world": self.world}

    def buildEntry(self, kind: str, entry: dict, textures: dict = None,
                   document=None, context: dict = None):
        """把一个条目交给注册表造出来（顺带把"名字引用"换成真正的对象）。

        这一步做三件事（都不碰 GL）：

        1. ``tileset`` 的字符串 → 文档里的 :class:`~pyglgame.render.TileSet.TileSet`；
        2. ``atlas`` 的字符串 → 文档 ``atlases`` 表里的
           :class:`~pyglgame.render.TextureAtlas.TextureAtlas`；
        3. 瓦片层补一个 ``pixels_per_unit``（``tile_size`` 缺省时要靠它算每格大小）；
        4. ``Body2D`` 这类需要"外部上下文"的工厂，从 :meth:`context` 拿到物理世界。
        """
        document = document or self.document
        entry = dict(entry or {})
        tileset_key = entry.get("tileset")
        if tileset_key is not None and not hasattr(tileset_key, "uvFor"):
            found = self.tilesets.get(str(tileset_key))
            if found is None:
                raise KeyError("图块集 %r 不在场景文档的 tilesets 表里" % (tileset_key,))
            entry["tileset"] = found
        atlas_key = entry.get("atlas")
        if isinstance(atlas_key, str):
            found = self.atlases.get(atlas_key)
            if found is None:
                raise KeyError("图集 %r 不在场景文档的 atlases 表里" % (atlas_key,))
            entry["atlas"] = found
        if kind == "layer" and document is not None:
            entry.setdefault("pixels_per_unit", document.pixels_per_unit)
        if textures is None and document is not None:
            textures = document.resolvedTextures()
        return registry.createFrom(entry, textures=textures or {},
                                   document=document, strict=self.strict,
                                   context=context if context is not None else self.context())

    # === 便捷 ===

    def __enter__(self) -> "SceneRuntime":
        self.load()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.unload()
        return False

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __repr__(self) -> str:
        name = self.document.name if self.document is not None else "未命名"
        return ("<SceneRuntime %r %d 个对象%s%s>"
                % (name, len(self.items),
                   "" if self.camera is None else " 含相机",
                   "" if not self.skipped else " 跳过 %d 条" % (len(self.skipped),)))
