"""2D 刚体：Box2D ``b2Body`` 的 GameObject 包装（引擎里"放个物理物体"就用它）。

坐标与单位约定（**和 3D 后端共用**，见 docs/物理引擎.md 第 1 节）
------------------------------------------------------------

- 单位就是引擎的世界单位（Box2D 建议当成"米"用，物体尺寸 0.1~10 之间最稳）；
- ``x / y`` 是刚体的**质心**（不是左上角），``z`` 只给 2D 场景分层用（物理不管）；
- 角度：``r`` 是**度**（引擎里 p/t/r 都是度），``angle`` 是**弧度**（Box2D 原生）；
  逆时针为正，与 Box2D / OpenGL 的 ``+Z`` 旋转一致。

谁拥有 x/y/r（重要）
--------------------

====================  ======================================================
刚体类型               位置/角度的"主人"
====================  ======================================================
``dynamic``            物理。每步之后回写 ``x/y/r``；瞬移用 ``teleport()``，
                       直接写 ``body.x = ...`` 会在下一步被覆盖
``kinematic`` /        引擎这侧。每帧自动把 ``x/y/r`` 推给物理
``static``             （``auto_sync=True``；值没变就不写，不会无谓唤醒刚体）
====================  ======================================================

力/冲量只对 ``dynamic`` 有效（Box2D 的规则）。
"""

import math

from ..gameobject.GameObject import GameObject
from . import Backend


class BodyType:
    """刚体类型（用字符串，避免模块级 import Box2D —— 3D 后端沿用同一套名字）。"""

    STATIC = "static"
    KINEMATIC = "kinematic"
    DYNAMIC = "dynamic"


ALL_BODY_TYPES = (BodyType.STATIC, BodyType.KINEMATIC, BodyType.DYNAMIC)

#: 形状种类（``Fixture2D.kind``）
SHAPE_BOX = "box"
SHAPE_CIRCLE = "circle"
SHAPE_POLYGON = "polygon"
SHAPE_EDGE = "edge"
SHAPE_CHAIN = "chain"

#: 圆在调试绘制 / 包围盒里用多少段折线近似
CIRCLE_SEGMENTS = 16


def _b2():
    """后端模块（第一次用才真的 import Box2D）。"""
    return Backend.load()


def _b2BodyType(name: str):
    b2 = _b2()
    if name == BodyType.STATIC:
        return b2.b2_staticBody
    if name == BodyType.KINEMATIC:
        return b2.b2_kinematicBody
    if name == BodyType.DYNAMIC:
        return b2.b2_dynamicBody
    raise ValueError(f"未知刚体类型: {name!r}（可选 {ALL_BODY_TYPES}）")


def _packPoint(value, what: str = "点") -> tuple:
    """把 ``(x, y)`` / ``b2Vec2`` / 带 ``x/y`` 的对象统一成 ``(float, float)``。"""
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise ValueError(f"{what}需要 (x, y) 两个数，收到 {value!r}")
        return (float(value[0]), float(value[1]))
    x = getattr(value, "x", None)
    y = getattr(value, "y", None)
    if x is None or y is None:
        raise TypeError(f"{what}需要 (x, y) 或带 x/y 的对象，收到 {type(value).__name__}")
    return (float(x), float(y))


def _packPoints(values, what: str = "点列表") -> list:
    return [_packPoint(value, what) for value in values]


def _unpackPair(first, second, what: str):
    """支持 ``applyForce(2.0, 0.0)`` 与 ``applyForce((2.0, 0.0))`` 两种写法。"""
    if second is None:
        return _packPoint(first, what)
    return (float(first), float(second))


class Fixture2D:
    """一个碰撞形状（= 一个 Box2D fixture）的 Python 记录。

    它同时是：

    - **记录**：``body.fixtures`` 里能看到自己加了哪些形状、多大、什么材质；
    - **调试/查询的数据源**：:meth:`localVertices` / :meth:`worldVertices` /
      :meth:`area` / :meth:`containsPoint` / :meth:`aabb` 都不依赖 b2 侧配合
      （画线框、命中测试、包围盒都够用）。

    材质（density/friction/restitution/sensor/过滤器）改完立刻同步给 Box2D；
    改 ``density`` 还会顺手 ``ResetMassData()`` —— 不重算的话质量不会变（踩过）。
    """

    def __init__(self, body: "Body2D", kind: str, *, size=None, offset=(0.0, 0.0),
                 points=None, angle: float = 0.0, loop: bool = False,
                 density: float = 1.0, friction: float = 0.3, restitution: float = 0.0,
                 sensor: bool = False, category: int = 0x0001, mask: int = 0xFFFF,
                 group: int = 0) -> None:
        self.body = body
        self.kind = kind
        self.size = None if size is None else (float(size[0]), float(size[1]))
        self.offset = (float(offset[0]), float(offset[1]))
        self.angle = float(angle)                  # 形状自身相对刚体的旋转（度）
        self.points = None if points is None else list(points)
        self.loop = bool(loop)                     # 仅链形用：闭合环 / 开放链
        self.fixture = None                        # 创建完后由 create() 填
        self._density = float(density)
        self._friction = float(friction)
        self._restitution = float(restitution)
        self._sensor = bool(sensor)
        self._category = int(category)
        self._mask = int(mask)
        self._group = int(group)

    # === 几何（不依赖 b2，纯 Python 也算得出来 → 可单测）===

    def localVertices(self) -> list:
        """形状在**刚体局部坐标**下的折线顶点（圆用 :data:`CIRCLE_SEGMENTS` 段近似）。"""
        if self.kind == SHAPE_CIRCLE:
            radius = self.size[0]
            return [(self.offset[0] + math.cos(i * 2.0 * math.pi / CIRCLE_SEGMENTS) * radius,
                     self.offset[1] + math.sin(i * 2.0 * math.pi / CIRCLE_SEGMENTS) * radius)
                    for i in range(CIRCLE_SEGMENTS)]
        if self.kind in (SHAPE_EDGE, SHAPE_CHAIN):
            return list(self.points)
        fixture = self.fixture
        shape = getattr(fixture, "shape", None) if fixture is not None else None
        vertices = getattr(shape, "vertices", None)
        if vertices:                               # Box2D 算过凸包，以它为准
            return [(float(v[0]), float(v[1])) for v in vertices]
        if self.kind == SHAPE_POLYGON:
            return list(self.points)               # 还没创建/拿不到 b2 形状时用原始输入
        half_x, half_y = self.size[0] * 0.5, self.size[1] * 0.5
        cosine = math.cos(math.radians(self.angle))
        sine = math.sin(math.radians(self.angle))
        result = []
        for local_x, local_y in ((-half_x, -half_y), (half_x, -half_y),
                                 (half_x, half_y), (-half_x, half_y)):
            result.append((self.offset[0] + local_x * cosine - local_y * sine,
                           self.offset[1] + local_x * sine + local_y * cosine))
        return result

    def worldVertices(self) -> list:
        """形状在**世界坐标**下的折线顶点。"""
        return [self.body.toWorld(point) for point in self.localVertices()]

    def area(self) -> float:
        """形状面积（边/链形按 Box2D 规则不参与质量，返回 0）。"""
        if self.kind == SHAPE_CIRCLE:
            return math.pi * self.size[0] * self.size[0]
        if self.kind == SHAPE_BOX:
            return self.size[0] * self.size[1]
        if self.kind == SHAPE_POLYGON:
            vertices = self.localVertices()
            total = 0.0
            for index, (x0, y0) in enumerate(vertices):
                x1, y1 = vertices[(index + 1) % len(vertices)]
                total += x0 * y1 - x1 * y0
            return abs(total) * 0.5
        return 0.0

    def containsPoint(self, x: float, y: float) -> bool:
        """点（世界坐标）是否落在形状内（走 Box2D 的 ``TestPoint``）。"""
        fixture = self.fixture
        if fixture is None:
            return False
        return bool(fixture.TestPoint((float(x), float(y))))

    def aabb(self) -> tuple:
        """世界坐标包围盒 ``(min_x, min_y, max_x, max_y)``。"""
        vertices = self.worldVertices()
        xs = [point[0] for point in vertices]
        ys = [point[1] for point in vertices]
        return (min(xs), min(ys), max(xs), max(ys))

    def __repr__(self) -> str:
        if self.kind == SHAPE_CIRCLE:
            detail = f"r={self.size[0]:g}"
        elif self.kind == SHAPE_BOX:
            detail = f"{self.size[0]:g}x{self.size[1]:g}"
        elif self.points is not None:
            detail = f"{len(self.points)} 点"
        else:
            detail = ""
        return f"<Fixture2D {self.kind} {detail} of {self.body}>"


    # === 材质 / 过滤器（写穿到 Box2D）===

    def _pushFilter(self) -> None:
        if self.fixture is None:
            return
        b2 = _b2()
        self.fixture.filterData = b2.b2Filter(categoryBits=self._category,
                                              maskBits=self._mask,
                                              groupIndex=self._group)

    def _pushDensity(self) -> None:
        """改密度后必须重算质量，否则 Box2D 里还是老质量。"""
        if self.fixture is not None:
            self.fixture.density = self._density
            self.fixture.body.ResetMassData()

    @property
    def density(self) -> float:
        return self._density

    @density.setter
    def density(self, value: float) -> None:
        self._density = float(value)
        self._pushDensity()

    @property
    def friction(self) -> float:
        return self._friction

    @friction.setter
    def friction(self, value: float) -> None:
        self._friction = float(value)
        if self.fixture is not None:
            self.fixture.friction = self._friction

    @property
    def restitution(self) -> float:
        return self._restitution

    @restitution.setter
    def restitution(self, value: float) -> None:
        self._restitution = float(value)
        if self.fixture is not None:
            self.fixture.restitution = self._restitution

    @property
    def sensor(self) -> bool:
        return self._sensor

    @sensor.setter
    def sensor(self, value: bool) -> None:
        self._sensor = bool(value)
        if self.fixture is not None:
            self.fixture.sensor = self._sensor

    @property
    def category(self) -> int:
        """碰撞类别位（``(a.category & b.mask)`` 与 ``(b.category & a.mask)`` 都非 0 才可能相撞）。"""
        return self._category

    @category.setter
    def category(self, value: int) -> None:
        self._category = int(value)
        self._pushFilter()

    @property
    def mask(self) -> int:
        return self._mask

    @mask.setter
    def mask(self, value: int) -> None:
        self._mask = int(value)
        self._pushFilter()

    @property
    def group(self) -> int:
        """同组非 0 时：正数永远相撞、负数永远不相撞（优先于 category/mask）。"""
        return self._group

    @group.setter
    def group(self, value: int) -> None:
        self._group = int(value)
        self._pushFilter()

    # === 真正在 Box2D 里创建（由 Body2D 内部的 add* 调用）===

    def create(self):
        b2 = _b2()
        body = self.body._body
        material = dict(density=self._density, friction=self._friction,
                        restitution=self._restitution, isSensor=self._sensor,
                        categoryBits=self._category, maskBits=self._mask,
                        groupIndex=self._group)
        if self.kind == SHAPE_CIRCLE:
            shape = b2.b2CircleShape(radius=self.size[0], pos=self.offset)
        elif self.kind in (SHAPE_BOX, SHAPE_POLYGON):
            try:
                shape = b2.b2PolygonShape(vertices=self.localVertices())
            except Exception as exc:
                # pybox2d 对退化输入（共线点等）直接抛 AssertionError，
                # 这里换成能看懂的话；顶点数上限 b2_maxPolygonVertices = 16。
                raise ValueError(
                    f"多边形顶点不合法（{len(self.localVertices())} 个点）：{exc}；"
                    "Box2D 要求至少 3 个不共线的点、最多 16 个，且输入不必是凸的"
                    "（引擎会用凸包）") from exc
        elif self.kind == SHAPE_EDGE:
            shape = b2.b2EdgeShape(vertices=self.points)
        elif self.kind == SHAPE_CHAIN:
            self.points, shape = self._createChainShape()
        else:
            raise ValueError(f"未知形状种类: {self.kind!r}")
        self.fixture = body.CreateFixture(shape=shape, **material)
        self.fixture.userData = self        # 射线/AABB 回调里靠它换回 Fixture2D
        return self.fixture

    def _createChainShape(self):
        """链形：闭合环用 ``vertices=``，开放链用 ``vertices_chain=``。

        开放链额外补 ghost 顶点（首尾段各外推一个点），端点处才不会出现"缝"。
        注意：**转折顶点**处漏过动态物体是 Box2D 的已知行为，静态地形建议用
        ``addBox`` / ``addEdge`` 拼接，见 docs/物理引擎.md 第 8 节。
        """
        b2 = _b2()
        points = list(self.points)
        if self.loop:
            return points, b2.b2ChainShape(vertices=points)
        shape = b2.b2ChainShape(vertices_chain=points)
        if len(points) >= 3:
            first, second = points[0], points[1]
            last, before = points[-1], points[-2]
            try:
                shape.SetPrevVertex((2.0 * first[0] - second[0], 2.0 * first[1] - second[1]))
                shape.SetNextVertex((2.0 * last[0] - before[0], 2.0 * last[1] - before[1]))
            except Exception:
                pass                        # ghost 顶点只是优化，失败不影响形状
        return points, shape


class Body2D(GameObject):
    """一个 2D 刚体（:class:`GameObject` 子类 —— 它自己就进更新/渲染循环）。

    典型用法::

        world = Physics2D(gravity=(0.0, -9.8))
        ground = Body2D(world, body_type="static", y=-2.0)
        ground.addBox(20.0, 0.5, friction=0.6)        # 宽/高是**全长**（不是半长）
        crate = Body2D(world, x=0.0, y=3.0)
        crate.addBox(0.8, 0.8, density=1.0, friction=0.4, restitution=0.1)
        crate.applyImpulse(2.0, 0.0)                  # 只对 dynamic 有效
        crate.teleport(1.0, 5.0)                      # 瞬移（任何类型都能用）

    一个刚体可以挂任意多个形状（``addBox`` / ``addCircle`` / ``addPolygon`` /
    ``addEdge`` / ``addChain``）—— 它们是同一个刚体的碰撞体，质量自动叠加。
    """

    #: 让 :class:`Physics2D` 与渲染层一眼认出（同 FogVolume 的标记套路）
    is_body_2d = True

    def __new__(cls, *args, **kwargs):
        # GameObject.__new__ 只认 is_camera；构造参数在这里吃掉（同 FogVolume 的做法），
        # 否则 Body2D(world) 会把 world 当成 is_camera 传进去
        return super().__new__(cls)

    def __init__(self, world, body_type: str = BodyType.DYNAMIC, x: float = 0.0,
                 y: float = 0.0, z: float = 0.0, angle: float = 0.0,
                 density: float = 1.0, friction: float = 0.3, restitution: float = 0.0,
                 sensor: bool = False, category: int = 0x0001, mask: int = 0xFFFF,
                 group: int = 0, fixed_rotation: bool = False, bullet: bool = False,
                 gravity_scale: float = 1.0, linear_damping: float = 0.0,
                 angular_damping: float = 0.0, awake: bool = True,
                 auto_sync: bool = None, name: str = None) -> None:
        super().__init__()
        b2 = _b2()
        if not getattr(world, "is_physics_2d", False):
            raise TypeError(
                f"Body2D 的第一个参数必须是 Physics2D（世界），收到 {type(world).__name__}；"
                "先建世界：world = Physics2D(gravity=(0.0, -9.8))")
        if body_type not in ALL_BODY_TYPES:
            raise ValueError(f"未知刚体类型: {body_type!r}（可选 {ALL_BODY_TYPES}）")
        self.world = world
        self.name = name
        self.body_type = body_type
        # 缺省材质：每个 add* 都能单独覆盖（不传就用这里的）
        self.default_density = float(density)
        self.default_friction = float(friction)
        self.default_restitution = float(restitution)
        self.default_sensor = bool(sensor)
        self.default_category = int(category)
        self.default_mask = int(mask)
        self.default_group = int(group)
        # 位置/朝向（引擎约定 x/y/z + r 度）
        self.x, self.y, self.z, self.r = float(x), float(y), float(z), float(angle)
        #: 形状列表（:class:`Fixture2D`）
        self.fixtures: list = []
        #: 接触回调：``body.on_contact = func`` 或子类覆写 :meth:`onContact`
        self.on_contact = None
        #: 用户随便挂数据的口袋（b2 的 ``userData`` 被引擎用来反查 Body2D 了）
        self.data = {}
        # 动态刚体的位置由物理拥有；静态/运动学的由引擎这侧拥有
        self.auto_sync = (body_type != BodyType.DYNAMIC) if auto_sync is None else bool(auto_sync)
        self._destroyed = False
        definition = b2.b2BodyDef(
            type=_b2BodyType(body_type), position=(self.x, self.y),
            angle=math.radians(self.r), fixedRotation=bool(fixed_rotation),
            bullet=bool(bullet), gravityScale=float(gravity_scale),
            linearDamping=float(linear_damping), angularDamping=float(angular_damping),
            awake=bool(awake))
        definition.userData = self          # 接触事件/回调里靠它把 b2Body 换回 Body2D
        self._body = world.b2world.CreateBody(definition)
        world._addBody(self)

    # === 形状 ===

    def _addFixture(self, fixture: Fixture2D) -> Fixture2D:
        fixture.create()
        self.fixtures.append(fixture)
        return fixture

    def _material(self, kwargs: dict) -> dict:
        """把 ``add*`` 的材质参数与缺省值合并（只认白名单，打错直接 TypeError）。"""
        allowed = {"density", "friction", "restitution", "sensor", "category",
                   "mask", "group"}
        unknown = set(kwargs) - allowed
        if unknown:
            raise TypeError(f"未知材质参数: {sorted(unknown)}（可用 {sorted(allowed)}）")
        return dict(density=kwargs.get("density", self.default_density),
                    friction=kwargs.get("friction", self.default_friction),
                    restitution=kwargs.get("restitution", self.default_restitution),
                    sensor=kwargs.get("sensor", self.default_sensor),
                    category=kwargs.get("category", self.default_category),
                    mask=kwargs.get("mask", self.default_mask),
                    group=kwargs.get("group", self.default_group))

    def addBox(self, width: float, height: float, offset=(0.0, 0.0), angle: float = 0.0,
               **material) -> Fixture2D:
        """加一个矩形（``width`` / ``height`` 是**全长**，单位同世界）。"""
        if width <= 0.0 or height <= 0.0:
            raise ValueError(f"矩形的宽高必须为正，收到 {width}x{height}")
        return self._addFixture(Fixture2D(
            self, SHAPE_BOX, size=(width, height), offset=_packPoint(offset, "形状偏移"),
            angle=float(angle), **self._material(material)))

    def addCircle(self, radius: float, offset=(0.0, 0.0), **material) -> Fixture2D:
        """加一个圆（``radius`` 是半径；``offset`` 是圆心相对质心的偏移）。"""
        if radius <= 0.0:
            raise ValueError(f"圆半径必须为正，收到 {radius}")
        return self._addFixture(Fixture2D(
            self, SHAPE_CIRCLE, size=(radius, radius),
            offset=_packPoint(offset, "形状偏移"), **self._material(material)))

    def addPolygon(self, points, **material) -> Fixture2D:
        """加一个多边形（顶点是**局部坐标**；输入不必凸 —— Box2D 会取凸包）。

        限制：至少 3 个不共线的点、最多 16 个顶点，否则 :class:`ValueError`。
        """
        packed = _packPoints(points, "多边形顶点")
        if len(packed) < 3:
            raise ValueError(f"多边形至少需要 3 个顶点，收到 {len(packed)}")
        if len(packed) > 16:
            raise ValueError(
                f"多边形最多 16 个顶点（Box2D 的 b2_maxPolygonVertices），收到 {len(packed)}")
        return self._addFixture(Fixture2D(
            self, SHAPE_POLYGON, size=(1.0, 1.0), points=packed,
            **self._material(material)))

    def addEdge(self, start, end, **material) -> Fixture2D:
        """加一条**单向**线段（局部坐标；Box2D 的边从 A 看向 B 的左侧是"里面"）。"""
        return self._addFixture(Fixture2D(
            self, SHAPE_EDGE, size=(1.0, 1.0),
            points=[_packPoint(start, "线段起点"), _packPoint(end, "线段终点")],
            **self._material(material)))

    def addChain(self, points, loop: bool = False, **material) -> Fixture2D:
        """加一条折线（局部坐标；``loop=True`` 自动闭合成长条/环形地形）。

        边/链形**不参与质量计算**（Box2D 的规则），适合静态地形。
        """
        packed = _packPoints(points, "折线顶点")
        if len(packed) < 2:
            raise ValueError(f"折线至少需要 2 个顶点，收到 {len(packed)}")
        return self._addFixture(Fixture2D(
            self, SHAPE_CHAIN, size=(1.0, 1.0), points=packed, loop=loop,
            **self._material(material)))


    # === 同步（物理 <-> 引擎对象）===

    def syncFromBody(self) -> None:
        """物理 → 对象：把 ``b2Body`` 的位姿写回 ``x/y/r``（世界每步之后调用）。"""
        if self._destroyed or self._body is None:
            return
        position = self._body.position
        self.x = float(position.x)
        self.y = float(position.y)
        self.r = math.degrees(self._body.angle)

    def syncToBody(self) -> bool:
        """对象 → 物理：把 ``x/y/r`` 推给 ``b2Body``（返回是否真的写了）。

        静态刚体每帧无条件 ``SetTransform`` 会把 broadphase 标脏、还会唤醒刚体，
        所以先比一次：没变就直接返回（这是 ``auto_sync`` 每帧调用也不掉帧的原因）。
        """
        if self._destroyed or self._body is None:
            return False
        angle = math.radians(self.r)
        position = self._body.position
        if (abs(position.x - self.x) < 1e-9 and abs(position.y - self.y) < 1e-9
                and abs(self._body.angle - angle) < 1e-9):
            return False
        self._body.transform = ((self.x, self.y), angle)
        if self._body_type_name == BodyType.KINEMATIC:
            # 运动学平台挪动之后要**叫醒附近的动态刚体**：这个 Box2D 不会自动做，
            # 睡着的箱子会被电梯直接穿过去（实测）。多一次 AABB 查询，很便宜。
            world = getattr(self, "world", None)
            if world is not None:
                world.wakeBodiesIn(self.aabb())
        return True

    def teleport(self, x: float, y: float, angle: float = None) -> "Body2D":
        """瞬移（``angle`` 是**度**，可省）—— 动态刚体也可以用（会直接搬到目标点）。"""
        if angle is not None:
            self.r = float(angle)
        self.x, self.y = float(x), float(y)
        if self._destroyed or self._body is None:
            return self
        self._body.transform = ((self.x, self.y), math.radians(self.r))
        self._body.awake = True
        return self

    # === 位姿 / 速度 ===

    @property
    def angle(self) -> float:
        """朝向（**弧度**，Box2D 原生；引擎里更常用 ``r`` = 度）。"""
        if self._destroyed:
            return math.radians(self.r)
        return float(self._body.angle)

    @angle.setter
    def angle(self, value: float) -> None:
        self.teleport(self.x, self.y, math.degrees(float(value)))

    @property
    def position(self) -> tuple:
        """``(x, y)``；读 = 物理状态，写 = :meth:`teleport`。"""
        return (self.x, self.y)

    @position.setter
    def position(self, value) -> None:
        point = _packPoint(value, "position")
        self.teleport(point[0], point[1])

    @property
    def velocity(self) -> tuple:
        """线速度 ``(vx, vy)``。"""
        if self._destroyed:
            return (0.0, 0.0)
        value = self._body.linearVelocity
        return (float(value.x), float(value.y))

    @velocity.setter
    def velocity(self, value) -> None:
        point = _packPoint(value, "velocity")
        self.setVelocity(point[0], point[1])

    def setVelocity(self, vx: float, vy: float) -> "Body2D":
        """直接设线速度（比施力更"指令式"；只对 dynamic 有意义）。"""
        if not self._destroyed:
            self._body.linearVelocity = (float(vx), float(vy))
            self._body.awake = True
        return self

    @property
    def angularVelocity(self) -> float:
        """角速度（弧度/秒）。"""
        return 0.0 if self._destroyed else float(self._body.angularVelocity)

    @angularVelocity.setter
    def angularVelocity(self, value: float) -> None:
        if not self._destroyed:
            self._body.angularVelocity = float(value)
            self._body.awake = True


    # === 力 / 冲量（只对 dynamic 有效，Box2D 的规则）===

    def applyForce(self, fx, fy=None, point=None) -> "Body2D":
        """在世界点 ``point`` 施力（``point`` 省略 = 质心）；力是**持续**的，每帧都要调。"""
        if self._destroyed:
            return self
        x, y = _unpackPair(fx, fy, "力")
        target = _packPoint(point, "受力点") if point is not None else self._body.worldCenter
        self._body.ApplyForce((x, y), target, True)
        return self

    def applyImpulse(self, ix, iy=None, point=None) -> "Body2D":
        """在世界点 ``point`` 施加冲击量（省略 = 质心）—— 一次性，立刻改速度。"""
        if self._destroyed:
            return self
        x, y = _unpackPair(ix, iy, "冲量")
        target = _packPoint(point, "受力点") if point is not None else self._body.worldCenter
        self._body.ApplyLinearImpulse((x, y), target, True)
        return self

    def applyTorque(self, torque: float) -> "Body2D":
        """施加力矩（持续，弧度制）。"""
        if not self._destroyed:
            self._body.ApplyTorque(float(torque), True)
        return self

    def applyAngularImpulse(self, impulse: float) -> "Body2D":
        """施加角冲量（一次性）。"""
        if not self._destroyed:
            self._body.ApplyAngularImpulse(float(impulse), True)
        return self

    # === 质量 / 状态 ===

    @property
    def mass(self) -> float:
        """质量 = Σ(每个形状的密度 × 面积)（边/链形不计）。"""
        return 0.0 if self._destroyed else float(self._body.mass)

    @property
    def inertia(self) -> float:
        """转动惯量。"""
        return 0.0 if self._destroyed else float(self._body.inertia)

    @property
    def worldCenter(self) -> tuple:
        """质心的世界坐标（有多个形状时和 ``(x, y)`` 可能略有差别）。"""
        if self._destroyed:
            return (self.x, self.y)
        center = self._body.worldCenter
        return (float(center.x), float(center.y))

    @property
    def body_type(self) -> str:
        """刚体类型（可写：运行中 ``static`` ↔ ``dynamic`` 转换是支持的）。"""
        return self._body_type_name

    @body_type.setter
    def body_type(self, value: str) -> None:
        if value not in ALL_BODY_TYPES:
            raise ValueError(f"未知刚体类型: {value!r}（可选 {ALL_BODY_TYPES}）")
        self._body_type_name = value
        body = getattr(self, "_body", None)
        if body is not None and not getattr(self, "_destroyed", False):
            body.type = _b2BodyType(value)

    @property
    def physicsActive(self) -> bool:
        """刚体是否参与模拟（``False`` = 从物理里摘掉，但对象照常渲染）。"""
        return False if self._destroyed else bool(self._body.active)

    @physicsActive.setter
    def physicsActive(self, value: bool) -> None:
        if not self._destroyed:
            self._body.active = bool(value)

    @property
    def awake(self) -> bool:
        """是否醒着（睡着的刚体不参与模拟，速度已归零）。"""
        return False if self._destroyed else bool(self._body.awake)

    @awake.setter
    def awake(self, value: bool) -> None:
        if not self._destroyed:
            self._body.awake = bool(value)

    def sleep(self) -> "Body2D":
        self.awake = False
        return self

    def wake(self) -> "Body2D":
        self.awake = True
        return self

    @property
    def sleepingAllowed(self) -> bool:
        """是否允许睡着（关掉它 = 这个刚体永远不省电，配合 ``awake`` 用）。"""
        return True if self._destroyed else bool(self._body.sleepingAllowed)

    @sleepingAllowed.setter
    def sleepingAllowed(self, value: bool) -> None:
        if not self._destroyed:
            self._body.sleepingAllowed = bool(value)

    @property
    def bullet(self) -> bool:
        """连续碰撞检测（高速小物体必须开，否则会穿墙）。"""
        return False if self._destroyed else bool(self._body.bullet)

    @bullet.setter
    def bullet(self, value: bool) -> None:
        if not self._destroyed:
            self._body.bullet = bool(value)

    @property
    def fixedRotation(self) -> bool:
        """锁定旋转（箱子不会自己转倒）。"""
        return False if self._destroyed else bool(self._body.fixedRotation)

    @fixedRotation.setter
    def fixedRotation(self, value: bool) -> None:
        if not self._destroyed:
            self._body.fixedRotation = bool(value)

    @property
    def gravityScale(self) -> float:
        """重力缩放（0 = 悬浮；负值会上升，做气球/反重力很方便）。"""
        return 0.0 if self._destroyed else float(self._body.gravityScale)

    @gravityScale.setter
    def gravityScale(self, value: float) -> None:
        if not self._destroyed:
            self._body.gravityScale = float(value)

    @property
    def linearDamping(self) -> float:
        return 0.0 if self._destroyed else float(self._body.linearDamping)

    @linearDamping.setter
    def linearDamping(self, value: float) -> None:
        if not self._destroyed:
            self._body.linearDamping = float(value)

    @property
    def angularDamping(self) -> float:
        return 0.0 if self._destroyed else float(self._body.angularDamping)

    @angularDamping.setter
    def angularDamping(self, value: float) -> None:
        if not self._destroyed:
            self._body.angularDamping = float(value)


    # === 形状访问 / 几何 ===

    @property
    def fixtureCount(self) -> int:
        return len(self.fixtures)

    def fixture(self, index: int = 0) -> Fixture2D:
        """取第 ``index`` 个形状（默认第一个）；越界抛 ``IndexError``。"""
        return self.fixtures[index]

    @property
    def firstFixture(self):
        return self.fixtures[0] if self.fixtures else None

    def toWorld(self, point) -> tuple:
        """局部坐标 → 世界坐标（按当前位姿旋转 + 平移）。"""
        x, y = _packPoint(point, "局部点")
        cosine = math.cos(math.radians(self.r))
        sine = math.sin(math.radians(self.r))
        return (self.x + x * cosine - y * sine, self.y + x * sine + y * cosine)

    def toLocal(self, point) -> tuple:
        """世界坐标 → 局部坐标。"""
        x, y = _packPoint(point, "世界点")
        cosine = math.cos(math.radians(-self.r))
        sine = math.sin(math.radians(-self.r))
        dx, dy = x - self.x, y - self.y
        return (dx * cosine - dy * sine, dx * sine + dy * cosine)

    def containsPoint(self, x, y=None) -> bool:
        """点（世界坐标）是否压在本刚体的任何形状上 —— **鼠标拾取就用它**。"""
        point = _packPoint(x if y is None else (x, y), "点")
        return any(fixture.containsPoint(point[0], point[1]) for fixture in self.fixtures)

    #: 顺手起的别名（``body.contains(x, y)``）
    contains = containsPoint

    def aabb(self) -> tuple:
        """所有形状的世界坐标包围盒 ``(min_x, min_y, max_x, max_y)``。"""
        boxes = [fixture.aabb() for fixture in self.fixtures]
        if not boxes:
            return (self.x, self.y, self.x, self.y)
        return (min(box[0] for box in boxes), min(box[1] for box in boxes),
                max(box[2] for box in boxes), max(box[3] for box in boxes))

    # === 接触 ===

    def touchingBodies(self) -> list:
        """当前正与本刚体接触的刚体列表（含传感器）—— 直接查 Box2D，不用自己记账。"""
        result = []
        if self._destroyed:
            return result
        for edge in self._body.contacts:
            contact = edge.contact
            if contact is None or not contact.touching:
                continue
            owner = getattr(edge.other, "userData", None)
            if owner is not None and owner not in result:
                result.append(owner)
        return result

    def isTouching(self, other) -> bool:
        """是否正与 ``other`` 接触（复杂度 = 自己的接触数，很便宜）。"""
        return other in self.touchingBodies()

    def onContact(self, event) -> None:
        """接触回调（子类覆写它，或设 ``body.on_contact = func``）。

        事件在 :meth:`Physics2D.stepFixed` 里 **Step 之后** 才派发（Box2D 在
        Step 内部不允许改世界），所以回调里增删刚体/施力都是安全的。
        """

    # === GameObject 集成 ===

    def update(self, dt: float, tps: float) -> None:
        """静态/运动学刚体：把引擎这侧改过的 ``x/y/r`` 推给物理（动态刚体什么都不做）。

        这样"像普通 2D 对象那样移动"就能带动碰撞体：``lift.x += 2.0 * dt``。
        """
        if self.auto_sync:
            self.syncToBody()
        super().update(dt, tps)

    def delete(self) -> None:
        """从世界与渲染层注销，并销毁 Box2D 刚体（连带它的关节与接触）。"""
        if self._destroyed:
            return
        self._destroyed = True
        world = self.world
        if world is not None:
            world._removeBody(self)      # 世界负责 DestroyBody + 从列表摘掉
        self._body = None
        super().delete()

    def onRemoved(self) -> None:
        """兜底：如果别人直接调了 ``GameObject.delete(body)``，物理那边也要清干净。"""
        if not self._destroyed:
            self.delete()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return (f"<Body2D{label} {self.body_type} at ({self.x:.2f}, {self.y:.2f}) "
                f"r={self.r:.1f}° shapes={len(self.fixtures)}>")
