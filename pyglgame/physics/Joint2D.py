"""关节：把两个刚体连起来（距离 / 铰链 / 鼠标拖拽）。

关节不是 :class:`GameObject`（它没有"画面"），只挂在 ``world.joints`` 上。
删掉一个刚体时 Box2D 会连带删掉它身上的关节，引擎侧的记录也会失效
（``joint.joint`` 变成 ``None``，再调它的属性会报错 —— 这是刻意的，总比崩掉好）。
"""

import math

from .Body2D import _b2, _packPoint


class Joint2D:
    """关节基类。"""

    #: 关节种类（字符串，方便日志/调试里看）
    kind = "joint"

    def __init__(self, world, body_a, body_b, collide_connected: bool = False) -> None:
        if body_a is None or body_b is None:
            raise TypeError("关节需要两个刚体（body_a / body_b）")
        if getattr(body_a, "_body", None) is None or getattr(body_b, "_body", None) is None:
            raise ValueError("关节两端的刚体已经被销毁了")
        self.world = world
        self.body_a = body_a
        self.body_b = body_b
        self.collide_connected = bool(collide_connected)
        #: 原始 ``b2Joint``（销毁后变 ``None``）
        self.joint = None

    @classmethod
    def fromDef(cls, world, definition, body_a, body_b) -> "Joint2D":
        """用 Box2D 原生 ``*JointDef`` 造关节 —— 没封装的类型（Prismatic / Rope /
        Wheel / Weld / Pulley / Gear / Friction / Motor）都走这个口子::

            definition = b2.b2PrismaticJointDef()
            definition.Initialize(a._body, b._body, (0.0, 0.0), (1.0, 0.0))
            joint = Joint2D.fromDef(world, definition, a, b)
        """
        joint = cls(world, body_a, body_b,
                    collide_connected=bool(definition.collideConnected))
        definition.collideConnected = joint.collide_connected
        joint.joint = world.b2world.CreateJoint(definition)
        world._addJoint(joint)
        return joint

    @property
    def active(self) -> bool:
        """关节是否还在 Box2D 里（刚体被销毁后变 ``False``）。"""
        return self.joint is not None and bool(getattr(self.joint, "active", True))

    def delete(self) -> None:
        """删掉关节（幂等；从 ``world.joints`` 里摘掉并排队销毁）。"""
        if self.joint is None:
            return
        world = self.world
        if world is not None:
            world._removeJoint(self)
        self.joint = None

    def _check(self):
        if self.joint is None:
            raise RuntimeError(f"这个关节已经不存在了（{self!r}）—— 多半是它连着的"
                               "刚体被删了")
        return self.joint

    def __repr__(self) -> str:
        return (f"<{type(self).__name__} {self.body_a} × {self.body_b}"
                f"{'' if self.active else ' (dead)'}>")


class DistanceJoint2D(Joint2D):
    """距离关节：两个锚点之间保持固定距离（绳子 / 摆锤 / 桥面）。

    ``frequency>0`` 时变成**弹簧**（软约束）：frequency 是振荡频率(Hz)，
    damping 是阻尼比（0 = 一直抖，1 = 不给过冲）。
    """

    kind = "distance"

    def __init__(self, world, body_a, body_b, anchor_a=None, anchor_b=None, length=None,
                 frequency: float = 0.0, damping: float = 0.0,
                 collide_connected: bool = False) -> None:
        super().__init__(world, body_a, body_b, collide_connected)
        b2 = _b2()
        point_a = _packPoint(anchor_a if anchor_a is not None else body_a.position, "anchor_a")
        point_b = _packPoint(anchor_b if anchor_b is not None else body_b.position, "anchor_b")
        definition = b2.b2DistanceJointDef()
        definition.Initialize(body_a._body, body_b._body, point_a, point_b)
        definition.collideConnected = self.collide_connected
        if length is not None:
            definition.length = float(length)
        definition.frequencyHz = float(frequency)
        definition.dampingRatio = float(damping)
        self.joint = world.b2world.CreateJoint(definition)
        world._addJoint(self)

    @property
    def length(self) -> float:
        """当前目标长度（米）。"""
        return float(self._check().length)

    @length.setter
    def length(self, value: float) -> None:
        self._check().length = float(value)

    @property
    def frequency(self) -> float:
        """弹簧频率 (Hz)，0 = 硬杆。"""
        return float(self._check().frequency)

    @frequency.setter
    def frequency(self, value: float) -> None:
        self._check().frequency = float(value)

    @property
    def damping(self) -> float:
        """阻尼比 0~1。"""
        return float(self._check().dampingRatio)

    @damping.setter
    def damping(self, value: float) -> None:
        self._check().dampingRatio = float(value)


class RevoluteJoint2D(Joint2D):
    """旋转关节（铰链）：两个刚体共用一个锚点，可以绕它相对转动。

    常用两种玩法：

    - **限位**：``enable_limit=True, lower=-30, upper=30``（角度是**度**）
    - **马达**：``enable_motor=True, motor_speed=90, max_motor_torque=50``（度/秒）

    角度单位统一用**度**（引擎里的 ``r`` 也是度），转给 Box2D 时才转弧度。
    """

    kind = "revolute"

    def __init__(self, world, body_a, body_b, anchor=None, enable_limit: bool = False,
                 lower: float = 0.0, upper: float = 0.0, enable_motor: bool = False,
                 motor_speed: float = 0.0, max_motor_torque: float = 0.0,
                 collide_connected: bool = False) -> None:
        super().__init__(world, body_a, body_b, collide_connected)
        b2 = _b2()
        point = _packPoint(anchor if anchor is not None else body_a.position, "anchor")
        definition = b2.b2RevoluteJointDef()
        definition.Initialize(body_a._body, body_b._body, point)
        definition.collideConnected = self.collide_connected
        definition.enableLimit = bool(enable_limit)
        definition.lowerAngle = math.radians(float(lower))
        definition.upperAngle = math.radians(float(upper))
        definition.enableMotor = bool(enable_motor)
        definition.motorSpeed = math.radians(float(motor_speed))
        definition.maxMotorTorque = float(max_motor_torque)
        self.joint = world.b2world.CreateJoint(definition)
        world._addJoint(self)

    @property
    def angle(self) -> float:
        """两个刚体的相对转角（**度**）。"""
        return math.degrees(float(self._check().angle))

    @property
    def speed(self) -> float:
        """相对角速度（**度**/秒）。"""
        return math.degrees(float(self._check().speed))

    @property
    def limitEnabled(self) -> bool:
        return bool(self._check().limitEnabled)

    @limitEnabled.setter
    def limitEnabled(self, value: bool) -> None:
        self._check().limitEnabled = bool(value)

    @property
    def limits(self) -> tuple:
        """``(lower, upper)``（度）。"""
        joint = self._check()
        return (math.degrees(float(joint.lowerLimit)), math.degrees(float(joint.upperLimit)))

    @limits.setter
    def limits(self, value) -> None:
        self.setLimits(value[0], value[1])

    def setLimits(self, lower: float, upper: float) -> "RevoluteJoint2D":
        self._check().SetLimits(math.radians(float(lower)), math.radians(float(upper)))
        return self

    @property
    def motorEnabled(self) -> bool:
        return bool(self._check().motorEnabled)

    @motorEnabled.setter
    def motorEnabled(self, value: bool) -> None:
        self._check().motorEnabled = bool(value)

    @property
    def motorSpeed(self) -> float:
        """马达目标速度（**度**/秒）。"""
        return math.degrees(float(self._check().motorSpeed))

    @motorSpeed.setter
    def motorSpeed(self, value: float) -> None:
        self._check().motorSpeed = math.radians(float(value))

    @property
    def maxMotorTorque(self) -> float:
        """最大马达力矩（Box2D 那边只读要用 ``GetMaxMotorTorque()``）。"""
        return float(self._check().GetMaxMotorTorque())

    @maxMotorTorque.setter
    def maxMotorTorque(self, value: float) -> None:
        self._check().maxMotorTorque = float(value)

    def motorTorque(self, dt: float) -> float:
        """上一步马达实际给出的力矩（``dt`` = 步长，Box2D 要求传 1/dt）。"""
        if dt <= 0.0:
            raise ValueError("dt 必须为正数")
        return float(self._check().GetMotorTorque(1.0 / float(dt)))


class MouseJoint2D(Joint2D):
    """鼠标关节：把刚体上的一点"拖"向目标点（拖箱子/拉弓都用它）。

    ``body_a`` 是一个**看不见的静态锚点**（引擎自己建，无形状、不进
    ``world.bodies``），所以构造时只要给被拖的刚体::

        joint = MouseJoint2D(world, crate, (mouse_x, mouse_y))
        joint.target = (mouse_x, mouse_y)      # 每帧更新目标点
        joint.delete()                         # 松开鼠标
    """

    kind = "mouse"

    def __init__(self, world, body, target, max_force: float = 1000.0,
                 frequency: float = 5.0, damping: float = 0.7) -> None:
        b2 = _b2()
        anchor = world._mouseAnchor()
        super().__init__(world, anchor, body, collide_connected=False)
        point = _packPoint(target, "target")
        definition = b2.b2MouseJointDef()
        definition.bodyA = anchor._body
        definition.bodyB = body._body
        definition.target = point
        definition.maxForce = float(max_force)      # 太小拖不动重物，太大会"穿模"
        definition.frequencyHz = float(frequency)
        definition.dampingRatio = float(damping)
        self.joint = world.b2world.CreateJoint(definition)
        self.body.awake = True
        world._addJoint(self)

    @property
    def body(self):
        """被拖的刚体（``body_b``）。"""
        return self.body_b

    @property
    def target(self) -> tuple:
        """目标点（世界坐标），每帧更新它就能"拖着走"。"""
        return tuple(self._check().target)

    @target.setter
    def target(self, value) -> None:
        self.setTarget(value)

    def setTarget(self, x, y=None) -> "MouseJoint2D":
        point = _packPoint(x if y is None else (x, y), "target")
        self._check().target = point
        return self

    @property
    def maxForce(self) -> float:
        return float(self._check().maxForce)

    @maxForce.setter
    def maxForce(self, value: float) -> None:
        self._check().maxForce = float(value)

    @property
    def frequency(self) -> float:
        return float(self._check().frequency)

    @frequency.setter
    def frequency(self, value: float) -> None:
        self._check().frequency = float(value)

    @property
    def damping(self) -> float:
        return float(self._check().dampingRatio)

    @damping.setter
    def damping(self, value: float) -> None:
        self._check().dampingRatio = float(value)
