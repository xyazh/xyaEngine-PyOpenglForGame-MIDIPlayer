"""骨骼与骨架：2D 骨骼动画的**数据层**（纯 Python，不碰 GL）。

模型
----

每根骨头只有一组**局部变换**（``x`` / ``y`` / ``rotation`` / ``scale``）与一个父骨：

- 局部变换相对父骨（根骨的父 = 场景原点，也可以由 :meth:`Skeleton.solve` 的
  ``root`` 参数给一个"伪父变换"，用来把整具骨架摆到世界里）；
- :meth:`Skeleton.solve` 按**拓扑序**（父在子前）算出每根骨的**世界变换**：

::

    世界缩放 = 父世界缩放 × 局部缩放
    世界旋转 = 父世界旋转 + 局部旋转          # 度，逆时针
    世界位置 = 父世界位置 + rotate(局部偏移 × 父世界缩放, 父世界旋转)

- 世界变换算完之后，挂点（:class:`~pyglgame.gameobject.animation.BoneAttachment.BoneAttachment`）
  才能把自己的矩形摆到世界里。

坐标系与引擎一致：**度**、逆时针为正、Y 向上。

.. code-block:: python

    skeleton = Skeleton()
    root = skeleton.add("root")
    torso = skeleton.add("torso", parent="root", x=0.0, y=0.6, length=1.0)
    upper = skeleton.add("arm_upper", parent="torso", x=0.35, y=0.45, rotation=-30.0)
    forearm = skeleton.add("arm_lower", parent="upper", x=0.5, rotation=45.0)

    skeleton.setBone("arm_upper", rotation=-55.0).setLocal(x=0.4)   # 摆姿态
    skeleton.solve()                                               # 算世界变换
    print(skeleton.getBone("arm_lower").worldPoint(0.5, 0.0))      # 手在哪

数据来源
-------

:meth:`Skeleton.fromDict` / :meth:`Skeleton.toDict` 让它能直接从 AIGC / 编辑器导出的
JSON（``ResourceLocation.tryLoadJson()`` 的返回值）里读出来，见
docs/2D动画系统.md。
"""

import math

#: ``Bone`` 认识的构造字段（``Skeleton.fromDict`` 过滤外部 JSON 用）
BONE_FIELDS = ("x", "y", "rotation", "scale", "scale_x", "scale_y", "length")


class Bone:
    """一根骨头：局部变换 + 父/子关系 + （:meth:`Skeleton.solve` 后的）世界变换。"""

    def __init__(self, name: str, parent: "Bone" = None,
                 x: float = 0.0, y: float = 0.0, rotation: float = 0.0,
                 scale: float = 1.0, scale_x: float = None, scale_y: float = None,
                 length: float = 0.0) -> None:
        self.name = str(name)
        #: 父骨（``None`` = 根骨）
        self.parent: "Bone" = None
        self.children: list = []
        self.setParent(parent)
        self.length = float(length)
        # 局部变换先给默认值：``setLocal`` 只动"传进来的字段"
        self.x = self.y = self.rotation = 0.0
        self.scale_x = self.scale_y = 1.0
        self.setLocal(x=x, y=y, rotation=rotation, scale=scale,
                      scale_x=scale_x, scale_y=scale_y)
        #: "静止姿态"（``reset()`` 回到这个局部变换）
        self.default = (self.x, self.y, self.rotation,
                        self.scale_x, self.scale_y)
        # === 世界变换（solve() 之后有效）===
        self.world_x: float = 0.0
        self.world_y: float = 0.0
        self.world_rotation: float = 0.0
        self.world_scale_x: float = 1.0
        self.world_scale_y: float = 1.0
        self.solved: bool = False

    # === 层级 ===

    def setParent(self, parent: "Bone") -> "Bone":
        """换父骨（``None`` = 变成根骨）。"""
        if parent is self:
            raise ValueError("骨头不能当自己的父骨：%s" % self.name)
        if self.parent is not None and self in self.parent.children:
            self.parent.children.remove(self)
        self.parent = parent
        if parent is not None and self not in parent.children:
            parent.children.append(self)
        return self

    def hasAncestor(self, bone: "Bone") -> bool:
        """``bone`` 是不是自己的祖先（重定父时防环用）。"""
        ancestor = self.parent
        while ancestor is not None:
            if ancestor is bone:
                return True
            ancestor = ancestor.parent
        return False

    # === 局部变换 ===

    def setLocal(self, x: float = None, y: float = None, rotation: float = None,
                 scale: float = None, scale_x: float = None,
                 scale_y: float = None) -> "Bone":
        """写局部变换（**只写传进来的字段**；``scale`` 是等比缩放，会被 ``scale_x/y`` 覆盖）。"""
        if scale is not None:
            self.scale_x = self.scale_y = float(scale)
        if scale_x is not None:
            self.scale_x = float(scale_x)
        if scale_y is not None:
            self.scale_y = float(scale_y)
        if x is not None:
            self.x = float(x)
        if y is not None:
            self.y = float(y)
        if rotation is not None:
            self.rotation = float(rotation)
        return self

    def move(self, dx: float, dy: float) -> "Bone":
        """在局部坐标系里平移（做步态 / 手动摆动时比 ``setLocal`` 顺手）。"""
        self.x += float(dx)
        self.y += float(dy)
        return self

    def rotate(self, degrees: float) -> "Bone":
        """在局部旋转上叠加（负值 = 顺时针）。"""
        self.rotation += float(degrees)
        return self

    def setDefault(self, x: float = None, y: float = None, rotation: float = None,
                   scale: float = None, scale_x: float = None,
                   scale_y: float = None) -> "Bone":
        """把当前（或指定）局部变换记为"静止姿态"。"""
        self.setLocal(x=x, y=y, rotation=rotation, scale=scale,
                      scale_x=scale_x, scale_y=scale_y)
        self.default = (self.x, self.y, self.rotation,
                        self.scale_x, self.scale_y)
        return self

    def reset(self) -> "Bone":
        """回到静止姿态（世界变换要重新 :meth:`Skeleton.solve`）。"""
        self.x, self.y, self.rotation, self.scale_x, self.scale_y = self.default
        return self

    def localTransform(self) -> tuple:
        """``(x, y, rotation, scale_x, scale_y)``。"""
        return (self.x, self.y, self.rotation, self.scale_x, self.scale_y)

    def worldTransform(self) -> tuple:
        """``(world_x, world_y, world_rotation, world_scale_x, world_scale_y)``。"""
        return (self.world_x, self.world_y, self.world_rotation,
                self.world_scale_x, self.world_scale_y)

    # === 世界变换（由 Skeleton.solve() 调用）===

    def solve(self, parent_x: float = 0.0, parent_y: float = 0.0,
              parent_rotation: float = 0.0, parent_scale_x: float = 1.0,
              parent_scale_y: float = 1.0) -> "Bone":
        """按父变换算自己的世界变换（一般由 :meth:`Skeleton.solve` 统一驱动）。"""
        # 局部偏移先按父缩放拉伸，再按父旋转转到父的世界朝向
        offset_x = self.x * parent_scale_x
        offset_y = self.y * parent_scale_y
        rad = math.radians(parent_rotation)
        cosine, sine = math.cos(rad), math.sin(rad)
        self.world_x = parent_x + offset_x * cosine - offset_y * sine
        self.world_y = parent_y + offset_x * sine + offset_y * cosine
        self.world_rotation = parent_rotation + self.rotation
        self.world_scale_x = parent_scale_x * self.scale_x
        self.world_scale_y = parent_scale_y * self.scale_y
        self.solved = True
        return self

    # === 坐标换算 ===

    def worldPoint(self, local_x: float, local_y: float, rotation: float = 0.0,
                   scale_x: float = 1.0, scale_y: float = 1.0) -> tuple:
        """骨空间的点 → 世界坐标（挂点四角就是这么算出来的）。

        ``rotation`` / ``scale_x/y`` 是**挂点自己**的变换，先作用在骨空间，
        再套骨骼的世界变换（缩放 → 旋转 → 平移）。骨骼非均匀缩放时，
        "挂点自身的旋转"会有轻微偏差 —— 这是 2D SRT 骨骼的固有取舍
        （不引入矩阵就兼顾不了，和主流 2D 骨骼工具一致）。
        """
        rad = math.radians(rotation)
        cosine, sine = math.cos(rad), math.sin(rad)
        local_x = local_x * scale_x
        local_y = local_y * scale_y
        point_x = local_x * cosine - local_y * sine
        point_y = local_x * sine + local_y * cosine
        point_x *= self.world_scale_x
        point_y *= self.world_scale_y
        rad = math.radians(self.world_rotation)
        cosine, sine = math.cos(rad), math.sin(rad)
        return (self.world_x + point_x * cosine - point_y * sine,
                self.world_y + point_x * sine + point_y * cosine)

    def localPoint(self, world_x: float, world_y: float) -> tuple:
        """世界坐标 → 骨空间（鼠标拾取 / 碰撞检查用）。"""
        rad = math.radians(-self.world_rotation)
        cosine, sine = math.cos(rad), math.sin(rad)
        dx = world_x - self.world_x
        dy = world_y - self.world_y
        point_x = dx * cosine - dy * sine
        point_y = dx * sine + dy * cosine
        scale_x = self.world_scale_x if abs(self.world_scale_x) > 1e-9 else 1e-9
        scale_y = self.world_scale_y if abs(self.world_scale_y) > 1e-9 else 1e-9
        return (point_x / scale_x, point_y / scale_y)

    def worldTip(self) -> tuple:
        """骨"尖端"的世界坐标（沿局部 +X 走 ``length``；画骨骼线用）。"""
        return self.worldPoint(self.length, 0.0)

    def toDict(self) -> dict:
        """导出成可写进 JSON 的 dict（父骨用名字表示）。"""
        return {"name": self.name,
                "parent": None if self.parent is None else self.parent.name,
                "x": self.x, "y": self.y, "rotation": self.rotation,
                "scale_x": self.scale_x, "scale_y": self.scale_y,
                "length": self.length}

    def __repr__(self) -> str:
        parent = "-" if self.parent is None else self.parent.name
        return ("<Bone %r 父=%s 局部(%.3f, %.3f, %g°) 世界(%.3f, %.3f, %g°)>"
                % (self.name, parent, self.x, self.y, self.rotation,
                   self.world_x, self.world_y, self.world_rotation))


class Skeleton:
    """一具骨架：骨头表 + 拓扑序 + 世界变换解算（见模块文档）。"""

    def __init__(self, name: str = None, bones=None) -> None:
        self.name = name
        #: 骨头表（名字 → :class:`Bone`），插入顺序 = 作者书写顺序
        self.bones: dict = {}
        self.roots: list = []
        self._order: list = []
        if bones is not None:
            if isinstance(bones, dict):
                for bone_name, data in bones.items():
                    self.add(bone_name, **(data or {}))
            else:
                for bone in bones:
                    self.addBone(bone)

    # === 增删查 ===

    def add(self, name: str, parent=None, **kwargs) -> Bone:
        """加一根骨头（``parent`` 可以是骨头对象或名字），返回它。"""
        if name in self.bones:
            raise ValueError("骨头名字重复：%r" % (name,))
        return self.addBone(Bone(name, **kwargs), parent)

    def addBone(self, bone: Bone, parent=None) -> Bone:
        """把一根现成的 :class:`Bone` 注册进骨架（可顺手指定父骨）。"""
        if bone.name in self.bones and self.bones[bone.name] is not bone:
            raise ValueError("骨头名字重复：%r" % (bone.name,))
        if parent is not None:
            resolved = self.getBone(parent) if isinstance(parent, str) else parent
            if resolved is bone or resolved.hasAncestor(bone):
                # 新父骨是自己的后代 → 会成环
                raise ValueError("会绕成环：%r → %r" % (resolved.name, bone.name))
            bone.setParent(resolved)
        self.bones[bone.name] = bone
        self._rebuildOrder()
        return bone

    def remove(self, name: str, keep_children: bool = False) -> Bone:
        """移除一根骨头（默认连子树一起删；``keep_children`` 把**直接**子骨提到根）。"""
        bone = self.getBone(name)
        subtree = [bone]
        queue = [bone]
        while queue:
            current = queue.pop()
            for child in list(current.children):
                if child not in subtree:
                    subtree.append(child)
                    queue.append(child)
        if keep_children:
            # 只把直接子骨提到根；更深的后代依然跟着自己的父骨
            for child in list(bone.children):
                child.setParent(None)
        else:
            for item in subtree:
                self.bones.pop(item.name, None)
        if bone.parent is not None and bone in bone.parent.children:
            bone.parent.children.remove(bone)
        self.bones.pop(bone.name, None)
        self._rebuildOrder()
        return bone

    def getBone(self, name) -> Bone:
        """按名字取骨头（名字不对就报错并列出可用的名字）。"""
        if isinstance(name, Bone):
            return name
        bone = self.bones.get(name)
        if bone is None:
            raise KeyError("骨架里没有骨头 %r（可用：%s）"
                           % (name, ", ".join(self.bones) or "空"))
        return bone

    def findBone(self, name) -> Bone:
        """按名字取骨头，取不到返回 ``None``。"""
        if isinstance(name, Bone):
            return name
        return self.bones.get(name)

    def hasBone(self, name) -> bool:
        return name in self.bones

    def _rebuildOrder(self) -> list:
        """重算拓扑序（父在子前）与根骨表。"""
        order = []
        roots = []
        for bone in self.bones.values():
            if bone.parent is None or bone.parent.name not in self.bones:
                roots.append(bone)
        queue = list(roots)
        while queue:
            current = queue.pop(0)
            if current in order:
                continue
            order.append(current)
            queue.extend(current.children)
        for bone in self.bones.values():
            if bone not in order:          # 环 / 落单（理论上不会，保底）
                order.append(bone)
        self.roots = roots
        self._order = order
        return order

    @property
    def boneOrder(self) -> list:
        """拓扑序的骨头列表（父在子前 → ``solve()`` 直接顺着走）。"""
        return list(self._order)

    # === 解算 ===

    def solve(self, root=None) -> "Skeleton":
        """算出所有骨头的世界变换。

        :param root: 可选的"伪父变换" ``(x, y, rotation, scale_x, scale_y)`` ——
                     用来把整具骨架摆到世界里（``ISkeletonAnimation`` 拿宿主的
                     ``x`` / ``y`` / ``r`` 当它使）。``None`` = 单位变换。

        按**拓扑序**一次遍历即可：每根骨只依赖父骨（父已经在前面算好了）。
        """
        if root is None:
            root_x = root_y = root_rotation = 0.0
            root_scale_x = root_scale_y = 1.0
        else:
            values = tuple(float(v) for v in root)
            root_x, root_y, root_rotation = values[0], values[1], values[2]
            root_scale_x = values[3] if len(values) > 3 else 1.0
            root_scale_y = values[4] if len(values) > 4 else root_scale_x
        for bone in self._order:
            parent = bone.parent if bone.parent in self._order else None
            if parent is None or parent.name not in self.bones:
                bone.solve(root_x, root_y, root_rotation, root_scale_x, root_scale_y)
            else:
                bone.solve(parent.world_x, parent.world_y, parent.world_rotation,
                           parent.world_scale_x, parent.world_scale_y)
        return self

    def reset(self) -> "Skeleton":
        """所有骨头回到静止姿态（之后要自己 :meth:`solve`）。"""
        for bone in self._order:
            bone.reset()
        return self

    def rest(self) -> "Skeleton":
        """回到静止姿态**并**立刻解算一次（"立正"）。"""
        return self.reset().solve()

    # === 摆姿态 ===

    def setBone(self, name, **values) -> Bone:
        """改一根骨的局部变换（``x`` / ``y`` / ``rotation`` / ``scale`` /
        ``scale_x`` / ``scale_y``），返回骨头本身（还能接着链式调）。"""
        bone = self.getBone(name)
        bone.setLocal(**values)
        return bone

    def moveBone(self, name, dx: float, dy: float) -> Bone:
        """在局部坐标系里平移一根骨。"""
        return self.getBone(name).move(dx, dy)

    def rotateBone(self, name, degrees: float) -> Bone:
        """在局部旋转上叠加一根骨（做摆动 / 步态）。"""
        return self.getBone(name).rotate(degrees)

    def worldPoint(self, name, local_x: float, local_y: float) -> tuple:
        """骨空间点 → 世界坐标（等价 ``getBone(name).worldPoint(...)``）。"""
        return self.getBone(name).worldPoint(local_x, local_y)

    def apply(self, pose: dict, solve: bool = False) -> "Skeleton":
        """把 ``{骨头名: (x, y, rotation, scale_x, scale_y)}`` 一次性写进去。

        ``AnimationClip.sample()`` 就是给这个用的；``solve=True`` 时顺手解算。
        """
        for name, values in pose.items():
            bone = self.findBone(name)
            if bone is None:
                continue
            bone.setLocal(x=values[0], y=values[1], rotation=values[2],
                          scale_x=values[3] if len(values) > 3 else None,
                          scale_y=values[4] if len(values) > 4 else None)
        if solve:
            self.solve()
        return self

    # === 数据（AIGC / 编辑器导出的 JSON 直接能读）===

    def toDict(self) -> dict:
        """导出成可写进 JSON 的 dict。"""
        return {"name": self.name, "bones": [b.toDict() for b in self._order]}

    @classmethod
    def fromDict(cls, data: dict) -> "Skeleton":
        """从 dict 建骨架（``{"name": ..., "bones": [{name, parent, x, ...}]}``）。

        也接受 ``{"bones": {名字: {parent, x, ...}}}`` 这种"名字 → 参数"的写法；
        父骨可以出现在子骨**之后**（会先建好全部骨头再连父子）。
        """
        data = data or {}
        skeleton = cls(name=data.get("name"))
        bones = data.get("bones", data)
        if isinstance(bones, dict):
            entries = [dict(values or {}, name=name) for name, values in bones.items()]
        else:
            entries = [dict(entry) for entry in bones]
        for entry in entries:
            entry = dict(entry)
            bone_name = entry.pop("name")
            entry.pop("parent", None)
            entry.pop("children", None)
            # 只挑 Bone 认识的字段：AIGC / 编辑器导出的 JSON 常带多余键
            kwargs = {key: entry[key] for key in BONE_FIELDS if key in entry}
            skeleton.addBone(Bone(bone_name, **kwargs))
        for entry in entries:
            parent_name = entry.get("parent")
            if parent_name:
                bone = skeleton.getBone(entry["name"])
                parent = skeleton.getBone(parent_name)
                if bone is parent or parent.hasAncestor(bone):
                    raise ValueError("会绕成环：%r → %r" % (parent_name, bone.name))
                bone.setParent(parent)
        skeleton._rebuildOrder()
        return skeleton

    # === 便利 ===

    def __len__(self) -> int:
        return len(self.bones)

    def __contains__(self, name) -> bool:
        return name in self.bones

    def __iter__(self):
        return iter(self._order)

    def __getitem__(self, name) -> Bone:
        return self.getBone(name)

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return "<Skeleton%s %d 骨（根 %d）：%s>" % (
            label, len(self.bones), len(self.roots),
            ", ".join(b.name for b in self._order[:6])
            + ("…" if len(self._order) > 6 else ""))
