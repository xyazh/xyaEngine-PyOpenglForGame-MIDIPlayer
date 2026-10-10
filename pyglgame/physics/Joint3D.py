"""3D 关节：pybullet ``createConstraint`` 的封装（铰链 / 球窝 / 滑动 / 焊接）。

设计要点
--------

- pybullet 的"关节"是**约束**（constraint）：两具刚体 + 锚点（+ 轴）。引擎这边对外
  统一用**世界坐标锚点**，内部再转成各自刚体的局部坐标（初始两点重合 → 约束成立）。
- 关节两端必须**已经建出来**：所以构造时会顺手调一次 ``body._ensureBody()``。
- ⚠ **马达/限位不支持**：那需要 pybullet 的"带 link 的刚体"（URDF 式），本期不做；
  需要马达请用我方的"运动学刚体 + 每帧 teleport"来驱动（文档里有例子）。
- 其它关节类型走逃生口：:meth:`Joint3D.fromConstraint`。
"""

from .Body3D import _pb, _packVector


def _normalize(vector) -> tuple:
    """把向量归一化（长度 0 时返回 (0, 1, 0)）。"""
    x, y, z = (float(v) for v in vector[:3])
    length = (x * x + y * y + z * z) ** 0.5
    if length <= 1e-12:
        return (0.0, 1.0, 0.0)
    return (x / length, y / length, z / length)


class Joint3D:
    """关节基类（不是 :class:`GameObject`，只挂在 ``world.joints`` 上）。"""

    #: 关节种类（日志/调试用）
    kind = "joint"
    #: 子类用 pybullet 的 ``JOINT_*`` 常量名填
    PY_BULLET_TYPE = None

    def __init__(self, world, body_a, body_b, anchor, axis=(0.0, 1.0, 0.0),
                 joint_type: str = None, auto_create: bool = True) -> None:
        if body_a is None or body_b is None:
            raise TypeError("关节需要两个刚体（body_a / body_b）")
        if getattr(body_a, "_destroyed", False) or getattr(body_b, "_destroyed", False):
            raise ValueError("关节两端的刚体已经被销毁了")
        self.world = world
        self.body_a = body_a
        self.body_b = body_b
        self.anchor = _packVector(anchor, "anchor")
        self.axis = _packVector(axis, "axis")
        #: pybullet 的 constraint id 列表（铰链用两个球窝拼出来，所以可能是多个）
        self.constraints: list = []
        #: 第一个 constraint（调试/信息用；没有约束时 ``None``）
        self.constraint = None
        body_a._ensureBody()
        body_b._ensureBody()
        if auto_create:
            self._createConstraint(joint_type or self.PY_BULLET_TYPE, self.anchor)
        world._addJoint(self)

    def _createConstraint(self, joint_type: str, anchor) -> int:
        """在 ``anchor``（世界坐标）建一个 pybullet 约束，返回它的 id。"""
        pb = _pb()
        constraint_type = getattr(pb, joint_type)
        constraint = pb.createConstraint(
            self.body_a.body, -1, self.body_b.body, -1, constraint_type,
            list(self._localAxis(self.body_a)),
            list(self.body_a.toLocal(anchor)),
            list(self.body_b.toLocal(anchor)), physicsClientId=self.world.client)
        self.constraints.append(constraint)
        if self.constraint is None:
            self.constraint = constraint
        return constraint

    @classmethod
    def fromConstraint(cls, world, body_a, body_b, joint_type, anchor,
                       axis=(0.0, 1.0, 0.0)) -> "Joint3D":
        """逃生口：直接用 pybullet 的 ``JOINT_*`` 常量建约束（未封装类型走它）。"""
        joint = cls(world, body_a, body_b, anchor, axis)
        joint.kind = str(joint_type)
        return joint

    def _localAxis(self, body) -> tuple:
        """世界轴向 → 刚体局部轴向（只转方向，不平移）。"""
        import glm
        local = glm.transpose(body.matrix()) * glm.vec3(*self.axis)
        return (float(local.x), float(local.y), float(local.z))

    @property
    def active(self) -> bool:
        """约束是否还在（刚体被销毁后变 ``False``）。"""
        return bool(self.constraints)

    def info(self) -> dict:
        """pybullet 的 ``getConstraintInfo`` 原始结果（调试用；只看第一个约束）。"""
        if self.constraint is None:
            return {}
        return _pb().getConstraintInfo(self.constraint, physicsClientId=self.world.client)

    def setMaxForce(self, force: float) -> "Joint3D":
        """设置约束的最大力（越大越"硬"）。"""
        for constraint in self.constraints:
            try:
                _pb().changeConstraint(constraint, maxForce=float(force),
                                       physicsClientId=self.world.client)
            except Exception:
                pass
        return self

    def delete(self) -> None:
        """删掉关节（幂等；从 ``world.joints`` 摘掉并排队销毁，多个约束一起删）。"""
        if not self.constraints and self.constraint is None:
            return
        world = self.world
        if world is not None:
            world._removeJoint(self)
        self.constraints = []
        self.constraint = None

    def __repr__(self) -> str:
        return (f"<{type(self).__name__} {self.body_a} × {self.body_b} "
                f"anchor={self.anchor}{'' if self.active else ' (dead)'}>")


class BallSocketJoint3D(Joint3D):
    """球窝关节（``JOINT_POINT2POINT``）：两具刚体在一点相连、可自由转动。

    做摆锤、绳索链条、布娃娃的关节都用它。
    """

    kind = "ball"
    PY_BULLET_TYPE = "JOINT_POINT2POINT"

    def __init__(self, world, body_a, body_b, anchor) -> None:
        super().__init__(world, body_a, body_b, anchor, axis=(0.0, 1.0, 0.0))


class HingeJoint3D(Joint3D):
    """铰链关节：绕一个轴相对转动（门、轮子、桥板）。

    ⚠ **实测**：pybullet 的 ``createConstraint`` **不接受 ``JOINT_REVOLUTE``**
    （报 "unknown constraint type"）。所以这里用**两个球窝约束**沿轴向错开一点拼出铰链
    （社区通用做法）：两个球窝把两点都钉住 → 只剩"绕这两点连线（= axis）转动"这一自由度。
    """

    kind = "hinge"
    PY_BULLET_TYPE = "JOINT_POINT2POINT"

    def __init__(self, world, body_a, body_b, anchor, axis=(0.0, 1.0, 0.0),
                 spacing: float = 0.4) -> None:
        # auto_create=False：基类不建默认约束，这里自己建**两个**球窝
        super().__init__(world, body_a, body_b, anchor, axis=axis, auto_create=False)
        unit = _normalize(self.axis)
        offset = tuple(unit[i] * float(spacing) * 0.5 for i in range(3))
        self._createConstraint("JOINT_POINT2POINT",
                               tuple(self.anchor[i] + offset[i] for i in range(3)))
        self._createConstraint("JOINT_POINT2POINT",
                               tuple(self.anchor[i] - offset[i] for i in range(3)))


class SliderJoint3D(Joint3D):
    """滑动关节（``JOINT_PRISMATIC``）：沿一个轴相对平移（抽屉、升降台）。"""

    kind = "slider"
    PY_BULLET_TYPE = "JOINT_PRISMATIC"

    def __init__(self, world, body_a, body_b, anchor, axis=(1.0, 0.0, 0.0)) -> None:
        super().__init__(world, body_a, body_b, anchor, axis=axis)


class FixedJoint3D(Joint3D):
    """焊接关节（``JOINT_FIXED``）：两具刚体完全锁死（拼装复合物体）。"""

    kind = "fixed"
    PY_BULLET_TYPE = "JOINT_FIXED"

    def __init__(self, world, body_a, body_b, anchor=None) -> None:
        if anchor is None:
            anchor = body_b.position
        super().__init__(world, body_a, body_b, anchor, axis=(0.0, 1.0, 0.0))
