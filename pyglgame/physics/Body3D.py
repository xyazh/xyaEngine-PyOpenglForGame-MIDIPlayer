"""3D 刚体：pybullet（Bullet 3）``createMultiBody`` 的 GameObject 包装。

坐标与单位约定（与 2D 一致，见 docs/物理引擎-3D.md）
---------------------------------------------------

- 单位就是引擎世界单位（当"米"用）；``x / y / z`` 是刚体**原点**（质心附近）；
- 姿态：引擎用 ``p / t / r``（pitch/yaw/roll，**度**，与渲染器 ``Rx·Ry·Rz`` 同源），
  pybullet 用四元数 —— 转换在 :mod:`pyglgame.physics.Transform3D` 里，双向一致；
- 谁拥有位姿：``dynamic`` 归物理（每步回写 ``x/y/z/p/t/r``，瞬移用 ``teleport()``）；
  ``static`` / ``kinematic`` 归引擎这侧（每帧 ``update()`` 自动推给物理）。

与 2D 的差异（pybullet 的模型决定的）
------------------------------------

1. pybullet 用**质量**而不是密度：构造给 ``mass=<kg>``，或给 ``density=`` 让引擎按
   形状体积算（盒/球/胶囊/圆柱能算，平面/凸包必须直接给 ``mass``）。
2. **一个刚体暂时只支持一个形状**：pybullet 的形状必须在建体那一刻一次给全，而且
   基座形状不能带局部偏移/旋转。想要"多形状刚体"要走 pybullet 的 links（第二期）。
   形状是**延迟创建**的：``addBox(...)`` 只是记下规格，等世界第一次推步（或访问
   ``body.body``）时再真正建出来 —— 这样 API 手感与 2D 一致。
3. 没有"传感器"开关：引擎用 ``sensor=True`` 表示"只做查询、不参与碰撞响应"
   （把它放进单独的碰撞组 + ``mask`` 排除），要检测重叠用 ``Physics3D.queryAABB``。
"""

import math

import glm

from ..gameobject.GameObject import GameObject
from . import Backend
from .Body2D import BodyType
from .Transform3D import eulerToQuaternion, quaternionToEuler

ALL_BODY_TYPES = (BodyType.STATIC, BodyType.KINEMATIC, BodyType.DYNAMIC)

#: 形状种类（``ShapeSpec3D.kind``）
SHAPE_BOX = "box"
SHAPE_SPHERE = "sphere"
SHAPE_CAPSULE = "capsule"
SHAPE_CYLINDER = "cylinder"
SHAPE_PLANE = "plane"
SHAPE_CONVEX = "convex"

#: 传感器（只查询、不阻挡）的碰撞组
SENSOR_GROUP = 0x0008
#: 默认碰撞组（1）+ 默认掩码（-1 = 和所有组碰）
DEFAULT_GROUP = 0x0001
DEFAULT_MASK = -1


def _pb():
    """3D 后端模块（第一次用才真的 import pybullet）。"""
    return Backend.load3D()


def _packVector(value, what: str = "向量") -> tuple:
    """把 ``(x, y, z)`` / 带 ``x/y/z`` 的对象统一成 ``(float, float, float)``。"""
    if isinstance(value, (tuple, list)):
        if len(value) != 3:
            raise ValueError(f"{what}需要 (x, y, z) 三个数，收到 {value!r}")
        return (float(value[0]), float(value[1]), float(value[2]))
    x = getattr(value, "x", None)
    y = getattr(value, "y", None)
    z = getattr(value, "z", None)
    if x is None or y is None or z is None:
        raise TypeError(f"{what}需要 (x, y, z) 或带 x/y/z 的对象，收到 {type(value).__name__}")
    return (float(x), float(y), float(z))


def _volume(kind: str, size, radius: float, height: float) -> float:
    """形状体积（算质量用；平面/凸包返回 0 = 必须显式给质量）。"""
    if kind == SHAPE_BOX:
        return abs(size[0] * size[1] * size[2]) * 8.0        # size 是半长
    if kind == SHAPE_SPHERE:
        return 4.0 / 3.0 * math.pi * radius ** 3
    if kind == SHAPE_CAPSULE:
        return math.pi * radius ** 2 * height + 4.0 / 3.0 * math.pi * radius ** 3
    if kind == SHAPE_CYLINDER:
        return math.pi * radius ** 2 * height
    return 0.0


class ShapeSpec3D:
    """一个碰撞形状的规格 —— 同时也是"几何自述"（命中测试与调试线框都用它）。

    pybullet 的形状必须在建体那一刻给全，所以 ``add*`` 先把规格存在这里，
    :meth:`Body3D._ensureBody` 再一次性建出来；形状的局部偏移/旋转通过 pybullet 的
    ``collisionFramePosition`` / ``collisionFrameOrientation`` 下发。

    几何自述的部分**不依赖 pybullet**（纯 Python + glm），可以直接单测：包围盒、
    命中测试、线框线段都在这里算。
    """

    def __init__(self, kind: str, *, size=(0.0, 0.0, 0.0), radius: float = 0.0,
                 height: float = 0.0, offset=(0.0, 0.0, 0.0),
                 rotation=(0.0, 0.0, 0.0), points=None, normal=(0.0, 1.0, 0.0),
                 mass: float = 0.0) -> None:
        self.kind = kind
        self.size = tuple(float(v) for v in size)          # 盒子的**半长**
        self.radius = float(radius)
        self.height = float(height)                        # 胶囊/圆柱的**圆柱段**高度
        self.offset = tuple(float(v) for v in offset)      # 相对刚体原点
        self.rotation = tuple(float(v) for v in rotation)  # 度 (p, t, r)
        self.points = None if points is None else [
            (float(p[0]), float(p[1]), float(p[2])) for p in points]
        self.normal = tuple(float(v) for v in normal)
        self.mass = float(mass)
        self.shape_index = None                            # pybullet 的 shape id
        self._matrix = None                                # 缓存的形状→刚体局部旋转

    # === 几何自述（纯 Python + glm，可单测）===

    def matrix(self):
        """形状 → 刚体局部的旋转矩阵（``glm.mat3``，缓存）。"""
        if self._matrix is None:
            pitch, yaw, roll = self.rotation
            matrix = glm.mat4(1.0)
            matrix = glm.rotate(matrix, glm.radians(pitch), glm.vec3(1.0, 0.0, 0.0))
            matrix = glm.rotate(matrix, glm.radians(yaw), glm.vec3(0.0, 1.0, 0.0))
            matrix = glm.rotate(matrix, glm.radians(roll), glm.vec3(0.0, 0.0, 1.0))
            self._matrix = glm.mat3(matrix)
        return self._matrix

    def volume(self) -> float:
        """形状体积（平面/凸包算不出来 → 0，必须显式给质量）。"""
        return _volume(self.kind, self.size, self.radius, self.height)

    def toBodyLocal(self, point) -> tuple:
        """形状局部 → 刚体局部（先转姿态、再加偏移）。"""
        x, y, z = (float(v) for v in point[:3])
        if any(self.rotation):
            rotated = self.matrix() * glm.vec3(x, y, z)
            x, y, z = float(rotated.x), float(rotated.y), float(rotated.z)
        return (x + self.offset[0], y + self.offset[1], z + self.offset[2])

    def _shapeHalfExtents(self) -> tuple:
        """形状局部下的包围盒半长（平面返回全 0）。"""
        if self.kind == SHAPE_BOX:
            return self.size
        if self.kind == SHAPE_SPHERE:
            return (self.radius, self.radius, self.radius)
        if self.kind == SHAPE_CAPSULE:
            # 轴向 = 局部 Y（见 addCapsule 的说明）
            return (self.radius, self.height * 0.5 + self.radius, self.radius)
        if self.kind == SHAPE_CYLINDER:
            return (self.radius, self.height * 0.5, self.radius)
        if self.kind == SHAPE_CONVEX and self.points:
            return (max(abs(p[0]) for p in self.points),
                    max(abs(p[1]) for p in self.points),
                    max(abs(p[2]) for p in self.points))
        return (0.0, 0.0, 0.0)

    def localAABB(self) -> tuple:
        """形状在**刚体局部坐标**下的包围盒 ``(min, max)``。"""
        half = self._shapeHalfExtents()
        corners = []
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                for sz in (-1.0, 1.0):
                    corners.append(self.toBodyLocal((sx * half[0], sy * half[1], sz * half[2])))
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        zs = [c[2] for c in corners]
        return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


    def containsBodyLocal(self, point) -> bool:
        """点（**刚体局部坐标**）是否在形状内（解析判定，不需要 pybullet）。"""
        relative = (float(point[0]) - self.offset[0],
                    float(point[1]) - self.offset[1],
                    float(point[2]) - self.offset[2])
        if any(self.rotation):
            inverse = glm.transpose(self.matrix())          # 旋转矩阵的逆 = 转置
            local = inverse * glm.vec3(relative[0], relative[1], relative[2])
            relative = (float(local.x), float(local.y), float(local.z))
        kind = self.kind
        if kind == SHAPE_BOX:
            return (abs(relative[0]) <= self.size[0] and abs(relative[1]) <= self.size[1]
                    and abs(relative[2]) <= self.size[2])
        if kind == SHAPE_SPHERE:
            return (relative[0] ** 2 + relative[1] ** 2 + relative[2] ** 2) <= self.radius ** 2
        if kind == SHAPE_CAPSULE:
            half = self.height * 0.5
            clamped = max(-half, min(half, relative[1]))     # 轴向 = 局部 Y
            dx, dy, dz = relative[0], relative[1] - clamped, relative[2]
            return (dx * dx + dy * dy + dz * dz) <= self.radius ** 2
        if kind == SHAPE_CYLINDER:
            radial = relative[0] ** 2 + relative[2] ** 2
            return radial <= self.radius ** 2 and abs(relative[1]) <= self.height * 0.5
        if kind == SHAPE_CONVEX and self.points:
            return self._containsConvex(relative)
        if kind == SHAPE_PLANE:
            return abs(relative[1]) <= 1e-6                  # 平面：只在它自己那一层
        return False

    def _containsConvex(self, point) -> bool:
        """凸包命中：以顶点质心为扇心，逐个三角形判"同侧"（顶点少时够用）。"""
        count = len(self.points)
        center = tuple(sum(p[i] for p in self.points) / count for i in range(3))
        for index in range(count):
            a = self.points[index]
            b = self.points[(index + 1) % count]
            normal = ((b[1] - a[1]) * (center[2] - a[2]) - (b[2] - a[2]) * (center[1] - a[1]),
                      (b[2] - a[2]) * (center[0] - a[0]) - (b[0] - a[0]) * (center[2] - a[2]),
                      (b[0] - a[0]) * (center[1] - a[1]) - (b[1] - a[1]) * (center[0] - a[0]))
            side = (normal[0] * (point[0] - a[0]) + normal[1] * (point[1] - a[1])
                    + normal[2] * (point[2] - a[2]))
            if side < 0.0:
                return False
        return True

    # === 线框（给 DebugDraw3D 用；坐标都在**刚体局部**）===

    def localLines(self, segments: int = 12, grid: float = 40.0,
                   grid_step: float = 2.0) -> list:
        """形状的线框线段列表 ``[(起点, 终点), ...]``（刚体局部坐标）。"""
        kind = self.kind
        if kind == SHAPE_BOX:
            return self._boxLines()
        if kind == SHAPE_SPHERE:
            return self._sphereLines(segments)
        if kind == SHAPE_CAPSULE:
            return self._tubeLines(self.height * 0.5, self.radius, segments, True)
        if kind == SHAPE_CYLINDER:
            return self._tubeLines(self.height * 0.5, self.radius, segments, False)
        if kind == SHAPE_CONVEX and self.points:
            return self._convexLines()
        if kind == SHAPE_PLANE:
            return self._planeLines(grid, grid_step)
        return []

    def _emitRing(self, lines, axis: str, offset: float, radius: float,
                  segments: int) -> None:
        points = []
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            cosine, sine = math.cos(angle) * radius, math.sin(angle) * radius
            if axis == "y":
                points.append((cosine, offset, sine))
            elif axis == "x":
                points.append((offset, cosine, sine))
            else:
                points.append((cosine, sine, offset))
        for index in range(segments):
            lines.append((self.toBodyLocal(points[index]),
                          self.toBodyLocal(points[(index + 1) % segments])))


    def _boxLines(self) -> list:
        half_x, half_y, half_z = self.size
        corners = [(sx * half_x, sy * half_y, sz * half_z)
                   for sx in (-1.0, 1.0) for sy in (-1.0, 1.0) for sz in (-1.0, 1.0)]
        edges = ((0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3), (2, 6), (3, 7),
                 (4, 5), (4, 6), (5, 7), (6, 7))
        return [(self.toBodyLocal(corners[a]), self.toBodyLocal(corners[b]))
                for a, b in edges]

    def _sphereLines(self, segments: int) -> list:
        lines = []
        for axis in ("y", "x", "z"):
            self._emitRing(lines, axis, 0.0, self.radius, segments)
        return lines

    def _tubeLines(self, half: float, radius: float, segments: int,
                   capsule: bool) -> list:
        """胶囊/圆柱的线框：两端圆 + 4 条侧线（胶囊的两端圆就是半球口沿）。"""
        lines = []
        for offset in (half, -half):
            self._emitRing(lines, "y", offset, radius, segments)
        for index in range(4):
            angle = math.pi * 0.5 * index
            x, z = math.cos(angle) * radius, math.sin(angle) * radius
            lines.append((self.toBodyLocal((x, half, z)), self.toBodyLocal((x, -half, z))))
        return lines

    def _convexLines(self) -> list:
        lines = []
        count = len(self.points)
        for index in range(count):
            lines.append((self.toBodyLocal(self.points[index]),
                          self.toBodyLocal(self.points[(index + 1) % count])))
        return lines

    def _planeLines(self, grid: float, step: float) -> list:
        lines = []
        half = grid * 0.5
        count = int(grid / step) + 1
        for index in range(count):
            value = -half + index * step
            lines.append((self.toBodyLocal((value, 0.0, -half)),
                          self.toBodyLocal((value, 0.0, half))))
            lines.append((self.toBodyLocal((-half, 0.0, value)),
                          self.toBodyLocal((half, 0.0, value))))
        return lines

    def __repr__(self) -> str:
        if self.kind == SHAPE_BOX:
            detail = (f"{self.size[0] * 2:g}x{self.size[1] * 2:g}x{self.size[2] * 2:g}")
        elif self.kind == SHAPE_SPHERE:
            detail = f"r={self.radius:g}"
        elif self.kind in (SHAPE_CAPSULE, SHAPE_CYLINDER):
            detail = f"r={self.radius:g} h={self.height:g}"
        elif self.points is not None:
            detail = f"{len(self.points)} 点"
        else:
            detail = ""
        return f"<ShapeSpec3D {self.kind} {detail}>"


class Body3D(GameObject):
    """一个 3D 刚体（:class:`GameObject` 子类 —— 它自己进更新/渲染循环）。

    典型用法::

        world = Physics3D(gravity=(0.0, -9.8, 0.0))
        ground = Body3D(world, body_type="static", y=-1.0)
        ground.addBox(50.0, 1.0, 50.0, friction=0.8)      # 宽/高/深是**全长**

        crate = Body3D(world, x=0.0, y=6.0, yaw=25.0)     # 姿态是**度**（p/t/r）
        crate.addBox(1.0, 1.0, 1.0, mass=2.0, restitution=0.1)
        crate.applyImpulse(2.0, 0.0, 0.0)                 # 只对 dynamic 有效
        crate.teleport(3.0, 6.0, 0.0)                     # 瞬移（任何类型都能用）

    - 形状：``addBox`` / ``addSphere`` / ``addCapsule`` / ``addCylinder`` /
      ``addPlane`` / ``addConvex`` —— **一个刚体暂时只能有一个形状**（pybullet 的形状
      必须建体时一次给全），重复调用会给出明确报错；
    - 质量：给 ``mass=<kg>``，或给 ``density=`` 让引擎按形状体积算（顺序：形状的
      ``mass`` > 刚体的 ``density`` > 刚体的 ``mass`` > 动态默认 1.0）；
    - 不是 :class:`GameObject` 的部分：它**没有** ``.render()``（要画自己画），
      调试线框用 :class:`~pyglgame.physics.DebugDraw3D`。
    """

    #: 让 :class:`Physics3D` 与渲染层一眼认出
    is_body_3d = True

    def __new__(cls, *args, **kwargs):
        # GameObject.__new__ 只认 is_camera：构造参数在这里吃掉（同 Body2D）
        return super().__new__(cls)

    def __init__(self, world, body_type: str = BodyType.DYNAMIC, x: float = 0.0,
                 y: float = 0.0, z: float = 0.0, pitch: float = 0.0, yaw: float = 0.0,
                 roll: float = 0.0, mass: float = None, density: float = None,
                 friction: float = 0.5, restitution: float = 0.0,
                 rolling_friction: float = 0.0, spinning_friction: float = 0.0,
                 linear_damping: float = 0.0, angular_damping: float = 0.0,
                 group: int = DEFAULT_GROUP, mask: int = DEFAULT_MASK,
                 sensor: bool = False, awake: bool = True, auto_sync: bool = None,
                 name: str = None) -> None:
        super().__init__()
        if not getattr(world, "is_physics_3d", False):
            raise TypeError(
                f"Body3D 的第一个参数必须是 Physics3D（世界），收到 {type(world).__name__}；"
                "先建世界：world = Physics3D(gravity=(0.0, -9.8, 0.0))")
        if body_type not in ALL_BODY_TYPES:
            raise ValueError(f"未知刚体类型: {body_type!r}（可选 {ALL_BODY_TYPES}）")
        self.world = world
        self.name = name
        self.body_type = body_type                 # 只读（pybullet 不支持运行时改类型）
        #: pybullet 的刚体 id（**延迟创建**：第一次推步或访问它时才真正建出来）
        self.body = None
        #: 形状规格（本期只允许一个；见类文档）
        self.shapes: list = []
        # 材质 / 过滤
        self._mass = None if mass is None else float(mass)
        self._density = None if density is None else float(density)
        self.friction = float(friction)
        self.restitution = float(restitution)
        self.rolling_friction = float(rolling_friction)
        self.spinning_friction = float(spinning_friction)
        self.linear_damping = float(linear_damping)
        self.angular_damping = float(angular_damping)
        self.sensor = bool(sensor)
        if sensor:
            # 传感器：不参与碰撞响应，只用 queryAABB/射线去查它
            self.group, self.mask = SENSOR_GROUP, 0
        else:
            self.group, self.mask = int(group), int(mask)
        self._awake = bool(awake)
        # 位姿（引擎约定：x/y/z + p/t/r 度；pitch/yaw/roll 是 p/t/r 的等价别名）
        self.x, self.y, self.z = float(x), float(y), float(z)
        self.p, self.t, self.r = float(pitch), float(yaw), float(roll)
        #: 接触回调：``body.on_contact = func(event)`` 或子类覆写 :meth:`onContact`
        self.on_contact = None
        #: 用户数据口袋
        self.data = {}
        # 动态刚体位姿归物理；静态/运动学归引擎这侧
        self.auto_sync = (body_type != BodyType.DYNAMIC) if auto_sync is None else bool(auto_sync)
        self._destroyed = False
        world._addBody(self)


    # === 形状（pybullet 的形状必须建体时给全 → 这里先记规格）===

    def _addShape(self, spec: ShapeSpec3D) -> ShapeSpec3D:
        if self.body is not None:
            raise RuntimeError(
                "pybullet 的刚体形状必须在创建那一刻一起给全，建体之后不能再加形状；"
                "要么在 add* 之前建好，要么这个刚体用两个 Body3D（第二期会支持多形状 links）")
        if self.shapes:
            raise NotImplementedError(
                "一个 Body3D 暂时只能有一个形状（pybullet 的基座形状不带局部变换；"
                "多形状要等第二期用 links 实现）。当前已有 "
                f"{self.shapes[0]!r}，想加 {spec!r} 请拆成两个刚体")
        self.shapes.append(spec)
        return spec

    def addBox(self, width: float, height: float, depth: float,
               offset=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0),
               mass: float = None, density: float = None, **material) -> ShapeSpec3D:
        """加一个长方体（三个尺寸是**全长**）；``offset`` / ``rotation`` 是相对刚体原点。"""
        if width <= 0.0 or height <= 0.0 or depth <= 0.0:
            raise ValueError(f"盒子的三个尺寸必须为正，收到 {width}x{height}x{depth}")
        self._applyShapeMaterial(mass, density, material)
        return self._addShape(ShapeSpec3D(
            SHAPE_BOX, size=(width * 0.5, height * 0.5, depth * 0.5),
            offset=_packVector(offset, "形状偏移"), rotation=_packVector(rotation, "形状旋转")))

    def addSphere(self, radius: float, offset=(0.0, 0.0, 0.0),
                  mass: float = None, density: float = None, **material) -> ShapeSpec3D:
        """加一个球（``radius`` 是半径）。"""
        if radius <= 0.0:
            raise ValueError(f"球半径必须为正，收到 {radius}")
        self._applyShapeMaterial(mass, density, material)
        return self._addShape(ShapeSpec3D(
            SHAPE_SPHERE, radius=radius, offset=_packVector(offset, "形状偏移")))

    def addCapsule(self, radius: float, height: float, offset=(0.0, 0.0, 0.0),
                   rotation=(0.0, 0.0, 0.0), mass: float = None, density: float = None,
                   **material) -> ShapeSpec3D:
        """加一个胶囊（**轴向 = 局部 Y**，即"立着"的人形/柱子）。

        ``height`` 是**圆柱段**的高度（两端半球不算在内，与 pybullet 的参数一致）：
        总高 = ``height + 2 * radius``。
        """
        if radius <= 0.0 or height <= 0.0:
            raise ValueError(f"胶囊的半径/柱高必须为正，收到 r={radius} h={height}")
        self._applyShapeMaterial(mass, density, material)
        return self._addShape(ShapeSpec3D(
            SHAPE_CAPSULE, radius=radius, height=height,
            offset=_packVector(offset, "形状偏移"), rotation=_packVector(rotation, "形状旋转")))

    def addCylinder(self, radius: float, height: float, offset=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0), mass: float = None, density: float = None,
                    **material) -> ShapeSpec3D:
        """加一个圆柱（**轴向 = 局部 Y**；``height`` 是全高）。"""
        if radius <= 0.0 or height <= 0.0:
            raise ValueError(f"圆柱的半径/高必须为正，收到 r={radius} h={height}")
        self._applyShapeMaterial(mass, density, material)
        return self._addShape(ShapeSpec3D(
            SHAPE_CYLINDER, radius=radius, height=height,
            offset=_packVector(offset, "形状偏移"), rotation=_packVector(rotation, "形状旋转")))

    def addPlane(self, normal=(0.0, 1.0, 0.0), mass: float = None,
                 density: float = None, **material) -> ShapeSpec3D:
        """加一个**无限大**平面（地面用；``normal`` 默认朝上 = y 轴）。"""
        self._applyShapeMaterial(mass, density, material)
        return self._addShape(ShapeSpec3D(SHAPE_PLANE,
                                          normal=_packVector(normal, "平面法线")))

    def addConvex(self, points, offset=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0),
                  mass: float = None, density: float = None, **material) -> ShapeSpec3D:
        """加一个凸包（顶点是**局部坐标**，至少 4 个不共面的点）。

        顶点顺序按"给定顺序连成环"三角化（凸包用质心扇形），所以要按凸包表面顺序给点。
        凸包体积算不出来 → **必须显式给 ``mass``**。
        """
        packed = [_packVector(point, "凸包顶点") for point in points]
        if len(packed) < 4:
            raise ValueError(f"凸包至少需要 4 个顶点，收到 {len(packed)}")
        self._applyShapeMaterial(mass, density, material)
        if self._mass is None and self._density is None:
            raise ValueError("凸包体积算不出来，必须显式给 mass=<kg>")
        return self._addShape(ShapeSpec3D(
            SHAPE_CONVEX, points=packed, offset=_packVector(offset, "形状偏移"),
            rotation=_packVector(rotation, "形状旋转")))

    def _applyShapeMaterial(self, mass, density, material=None) -> None:
        """``add*`` 的 mass/density 覆盖刚体级别；``friction=`` 之类的材质也在这里生效。

        （材质最终是**刚体级**的：pybullet 的摩擦/弹性按刚体对取，没有 2D 那种逐 fixture
        材质。所以给某个形状写 ``friction=0.9`` 等于把这个刚体的摩擦设成 0.9。）
        """
        if mass is not None:
            self._mass = float(mass)
        if density is not None:
            self._density = float(density)
        for name in ("friction", "restitution", "rolling_friction", "spinning_friction",
                     "linear_damping", "angular_damping", "group", "mask"):
            if material and name in material:
                setattr(self, name, int(material[name]) if name in ("group", "mask")
                        else float(material[name]))


    # === 真正建体（延迟到第一次推步 / 访问 body 时）===

    def geometryMass(self) -> float:
        """质量规则：形状 ``mass`` > ``density × 体积`` > 刚体 ``mass`` > 动态默认 1.0。"""
        if self.body_type == BodyType.STATIC:
            return 0.0
        shape = self.shapes[0] if self.shapes else None
        if shape is not None and shape.mass > 0.0:
            return shape.mass
        if self._density is not None and shape is not None and shape.volume() > 0.0:
            return self._density * shape.volume()
        if self._mass is not None:
            return self._mass
        return 1.0

    def _collisionShapeArgs(self, shape: ShapeSpec3D) -> dict:
        """形状规格 → ``createCollisionShape`` 的关键字参数。"""
        pb = _pb()
        kwargs = {"physicsClientId": self.world.client}
        orientation = eulerToQuaternion(*shape.rotation)
        if shape.kind == SHAPE_BOX:
            kwargs["shapeType"] = pb.GEOM_BOX
            kwargs["halfExtents"] = list(shape.size)
        elif shape.kind == SHAPE_SPHERE:
            kwargs["shapeType"] = pb.GEOM_SPHERE
            kwargs["radius"] = shape.radius
        elif shape.kind in (SHAPE_CAPSULE, SHAPE_CYLINDER):
            kwargs["shapeType"] = (pb.GEOM_CAPSULE if shape.kind == SHAPE_CAPSULE
                                   else pb.GEOM_CYLINDER)
            kwargs["radius"] = shape.radius
            kwargs["height"] = shape.height
            # ⚠ 实测（pybullet 202010061）：胶囊/圆柱的轴向是 **Z**，而引擎约定轴向是 **Y**
            #   （立着的人形/柱子）。所以补一个绕 X 转 -90° 的碰撞形状朝向：
            #   实测 AABB 从 [1.0, 1.0, 3.0] 变成 [1.0, 3.0, 1.0]，落地高度也正确。
            kwargs["collisionFrameOrientation"] = list(
                _quatMultiply(orientation, _Z_AXIS_TO_Y))
            orientation = None                     # 已经用在上面的乘积里了
        elif shape.kind == SHAPE_PLANE:
            kwargs["shapeType"] = pb.GEOM_PLANE
            kwargs["planeNormal"] = list(shape.normal)
        elif shape.kind == SHAPE_CONVEX:
            vertices, indices = _convexMesh(shape.points)
            kwargs["shapeType"] = pb.GEOM_MESH
            kwargs["vertices"] = vertices
            kwargs["indices"] = indices
        else:
            raise ValueError(f"未知形状种类: {shape.kind!r}")
        if any(shape.offset):
            kwargs["collisionFramePosition"] = list(shape.offset)
        if orientation is not None and any(shape.rotation):
            kwargs["collisionFrameOrientation"] = list(orientation)
        return kwargs

    def _ensureBody(self):
        """真正在 pybullet 里建出刚体（幂等；由 :meth:`Physics3D.stepFixed` 调用）。"""
        if self.body is not None or self._destroyed:
            return self.body
        pb = _pb()
        if not self.shapes:
            raise RuntimeError(
                f"{self!r} 还没有形状：先调 addBox/addSphere/addCapsule/addCylinder/"
                "addPlane/addConvex")
        shape = self.shapes[0]
        index = pb.createCollisionShape(**self._collisionShapeArgs(shape))
        shape.shape_index = index
        self.body = pb.createMultiBody(
            baseMass=self.geometryMass(), baseCollisionShapeIndex=index,
            baseVisualShapeIndex=-1, basePosition=[self.x, self.y, self.z],
            baseOrientation=list(eulerToQuaternion(self.p, self.t, self.r)),
            physicsClientId=self.world.client)
        self.applyMaterial()
        self.world._registerBodyId(self)
        if not self._awake:
            self.sleep()
        elif self.body_type == BodyType.DYNAMIC and not self.world.do_sleep:
            self.awake = True                      # 世界关掉休眠 → 也不让它睡
        return self.body

    def setMaterial(self, friction: float = None, restitution: float = None,
                    rolling_friction: float = None, spinning_friction: float = None,
                    linear_damping: float = None, angular_damping: float = None
                    ) -> "Body3D":
        """改材质（只给的字段生效），立刻下发给 pybullet。"""
        if friction is not None:
            self.friction = float(friction)
        if restitution is not None:
            self.restitution = float(restitution)
        if rolling_friction is not None:
            self.rolling_friction = float(rolling_friction)
        if spinning_friction is not None:
            self.spinning_friction = float(spinning_friction)
        if linear_damping is not None:
            self.linear_damping = float(linear_damping)
        if angular_damping is not None:
            self.angular_damping = float(angular_damping)
        self.applyMaterial()
        return self

    def applyMaterial(self) -> None:
        """把材质与碰撞过滤下发给 pybullet（建体后自动调一次）。"""
        if self.body is None:
            return
        pb = _pb()
        values = dict(lateralFriction=self.friction, restitution=self.restitution,
                      rollingFriction=self.rolling_friction,
                      spinningFriction=self.spinning_friction,
                      linearDamping=self.linear_damping,
                      angularDamping=self.angular_damping)
        for key, value in values.items():
            try:
                pb.changeDynamics(self.body, -1, **{key: value},
                                  physicsClientId=self.world.client)
            except Exception:
                pass                                  # 老版本 pybullet 少某个字段时跳过
        try:
            pb.setCollisionFilterGroupMask(self.body, -1, int(self.group), int(self.mask),
                                           physicsClientId=self.world.client)
        except Exception:
            pass

    # === 姿态别名（p/t/r 的另一种写法，二者等价）===

    @property
    def pitch(self) -> float:
        return self.p

    @pitch.setter
    def pitch(self, value: float) -> None:
        self.p = float(value)

    @property
    def yaw(self) -> float:
        return self.t

    @yaw.setter
    def yaw(self, value: float) -> None:
        self.t = float(value)

    @property
    def roll(self) -> float:
        return self.r

    @roll.setter
    def roll(self, value: float) -> None:
        self.r = float(value)

    @property
    def position(self) -> tuple:
        """``(x, y, z)``；读 = 物理状态，写 = :meth:`teleport`。"""
        return (self.x, self.y, self.z)

    @position.setter
    def position(self, value) -> None:
        point = _packVector(value, "position")
        self.teleport(point[0], point[1], point[2])

    # === 同步（物理 <-> 引擎对象）===

    def syncFromBody(self) -> None:
        """物理 → 对象：把 pybullet 的位姿写回 ``x/y/z/p/t/r``。"""
        if self._destroyed or self.body is None:
            return
        pb = _pb()
        position, orientation = pb.getBasePositionAndOrientation(
            self.body, physicsClientId=self.world.client)
        self.x, self.y, self.z = float(position[0]), float(position[1]), float(position[2])
        pitch, yaw, roll = quaternionToEuler(orientation)
        self.p, self.t, self.r = pitch, yaw, roll

    def syncToBody(self) -> bool:
        """对象 → 物理：把 ``x/y/z/p/t/r`` 推给 pybullet（返回是否真的写了）。

        静态/运动学刚体每帧调用不会产生多余开销（值没变就直接返回）；运动学刚体挪动
        之后还会**叫醒附近的动态刚体**（pybullet 的休眠是逐刚体的，睡着的箱子不会被
        运动学平台自动唤醒）。
        """
        if self._destroyed or self.body is None:
            return False
        pb = _pb()
        target_position = (self.x, self.y, self.z)
        target_orientation = eulerToQuaternion(self.p, self.t, self.r)
        position, orientation = pb.getBasePositionAndOrientation(
            self.body, physicsClientId=self.world.client)
        if (abs(position[0] - target_position[0]) < 1e-9
                and abs(position[1] - target_position[1]) < 1e-9
                and abs(position[2] - target_position[2]) < 1e-9
                and abs(orientation[0] - target_orientation[0]) < 1e-7
                and abs(orientation[1] - target_orientation[1]) < 1e-7
                and abs(orientation[2] - target_orientation[2]) < 1e-7
                and abs(orientation[3] - target_orientation[3]) < 1e-7):
            return False
        pb.resetBasePositionAndOrientation(self.body, list(target_position),
                                           list(target_orientation),
                                           physicsClientId=self.world.client)
        if self.body_type == BodyType.KINEMATIC:
            world = getattr(self, "world", None)
            if world is not None:
                world.wakeBodiesIn(self.aabb())
        return True

    def teleport(self, x: float, y: float, z: float = 0.0, pitch: float = None,
                 yaw: float = None, roll: float = None) -> "Body3D":
        """瞬移 + 可选改姿态（都是**度**）；动态刚体也能用。"""
        self.x, self.y, self.z = float(x), float(y), float(z)
        if pitch is not None:
            self.p = float(pitch)
        if yaw is not None:
            self.t = float(yaw)
        if roll is not None:
            self.r = float(roll)
        if self._destroyed or self.body is None:
            return self
        pb = _pb()
        pb.resetBasePositionAndOrientation(
            self.body, [self.x, self.y, self.z],
            list(eulerToQuaternion(self.p, self.t, self.r)),
            physicsClientId=self.world.client)
        self.wake()
        return self


    # === 运动（力/冲量只对 dynamic 有效）===

    @staticmethod
    def _unpackTriple(first, second, third, what: str) -> tuple:
        """支持 ``applyImpulse(1, 0, 0)`` 与 ``applyImpulse((1, 0, 0))`` 两种写法。"""
        if second is None and third is None:
            return _packVector(first, what)
        return (float(first), float(second), float(third))

    @property
    def velocity(self) -> tuple:
        """线速度 ``(vx, vy, vz)``。"""
        if self.body is None or self._destroyed:
            return (0.0, 0.0, 0.0)
        pb = _pb()
        linear, _ = pb.getBaseVelocity(self.body, physicsClientId=self.world.client)
        return (float(linear[0]), float(linear[1]), float(linear[2]))

    @velocity.setter
    def velocity(self, value) -> None:
        point = _packVector(value, "velocity")
        self.setVelocity(point[0], point[1], point[2])

    def setVelocity(self, vx: float, vy: float, vz: float = 0.0) -> "Body3D":
        """直接设线速度（只对 dynamic 有意义）。"""
        if self.body is not None and not self._destroyed:
            pb = _pb()
            _, angular = pb.getBaseVelocity(self.body, physicsClientId=self.world.client)
            pb.resetBaseVelocity(self.body, linearVelocity=[float(vx), float(vy), float(vz)],
                                 angularVelocity=list(angular),
                                 physicsClientId=self.world.client)
            self.wake()
        return self

    @property
    def angularVelocity(self) -> tuple:
        """角速度（弧度/秒）。"""
        if self.body is None or self._destroyed:
            return (0.0, 0.0, 0.0)
        pb = _pb()
        _, angular = pb.getBaseVelocity(self.body, physicsClientId=self.world.client)
        return (float(angular[0]), float(angular[1]), float(angular[2]))

    @angularVelocity.setter
    def angularVelocity(self, value) -> None:
        point = _packVector(value, "angularVelocity")
        self.setAngularVelocity(point[0], point[1], point[2])

    def setAngularVelocity(self, wx: float, wy: float, wz: float = 0.0) -> "Body3D":
        if self.body is not None and not self._destroyed:
            pb = _pb()
            linear, _ = pb.getBaseVelocity(self.body, physicsClientId=self.world.client)
            pb.resetBaseVelocity(self.body, linearVelocity=list(linear),
                                 angularVelocity=[float(wx), float(wy), float(wz)],
                                 physicsClientId=self.world.client)
            self.wake()
        return self

    def applyForce(self, fx, fy=None, fz=None, point=None) -> "Body3D":
        """在世界点 ``point`` 施力（省略 = 刚体原点）。

        ⚠ pybullet 的"外部力"**只作用于下一步**，所以要**每帧都调**（连续推力）。
        """
        if self.body is None or self._destroyed:
            return self
        pb = _pb()
        force = self._unpackTriple(fx, fy, fz, "力")
        target = _packVector(point, "受力点") if point is not None else (0.0, 0.0, 0.0)
        pb.applyExternalForce(self.body, -1, list(force), list(target),
                              pb.WORLD_FRAME, physicsClientId=self.world.client)
        return self

    def applyImpulse(self, ix, iy=None, iz=None, point=None) -> "Body3D":
        """一次性冲量（立刻改速度：``Δv = J / mass``）。

        pybullet 没有 "apply impulse" 接口，所以这里读速度、加上 ``J/m``、再写回。
        """
        if self.body is None or self._destroyed:
            return self
        impulse = self._unpackTriple(ix, iy, iz, "冲量")
        mass = max(self.mass, 1e-6)
        linear = self.velocity
        self.setVelocity(linear[0] + impulse[0] / mass, linear[1] + impulse[1] / mass,
                         linear[2] + impulse[2] / mass)
        return self

    def applyTorque(self, tx, ty=None, tz=None) -> "Body3D":
        """施加力矩（同样**只作用于下一步**，要每帧调）。"""
        if self.body is None or self._destroyed:
            return self
        pb = _pb()
        torque = self._unpackTriple(tx, ty, tz, "力矩")
        pb.applyExternalTorque(self.body, -1, list(torque), pb.WORLD_FRAME,
                               physicsClientId=self.world.client)
        return self

    @property
    def mass(self) -> float:
        """质量（建体前按规则预估；建体后读 pybullet 的实际值）。"""
        if self.body is None:
            return self.geometryMass()
        pb = _pb()
        try:
            info = pb.getDynamicsInfo(self.body, -1, physicsClientId=self.world.client)
            return float(info[0])
        except Exception:
            return self.geometryMass()


    # === 状态 ===

    @property
    def awake(self) -> bool:
        """是否"醒着"（pybullet 不暴露实时睡眠状态，这里返回引擎记录的意图）。"""
        return self._awake

    @awake.setter
    def awake(self, value: bool) -> None:
        self._awake = bool(value)
        self._applyActivation()

    def _applyActivation(self) -> None:
        """把"醒/睡"的意图下发给 pybullet。

        常量名是**实测**出来的（这个 build 里没有 ``DISABLE_DEACTIVATION``）：
        ``ACTIVATION_STATE_WAKE_UP``=4、``DISABLE_SLEEPING``=2、``SLEEP``=8。
        """
        if self.body is None or self._destroyed:
            return
        pb = _pb()
        if self._awake:
            state = (getattr(pb, "ACTIVATION_STATE_WAKE_UP", 4)
                     | getattr(pb, "ACTIVATION_STATE_DISABLE_SLEEPING", 2))
        else:
            state = getattr(pb, "ACTIVATION_STATE_SLEEP", 8)
        try:
            pb.changeDynamics(self.body, -1, activationState=state,
                              physicsClientId=self.world.client)
        except Exception:
            pass

    def sleep(self) -> "Body3D":
        """让它睡（``ACTIVATION_STATE_SLEEP`` 是真正立即休眠）+ 清速度。"""
        if self.body is not None and not self._destroyed:
            self.setVelocity(0.0, 0.0, 0.0)
            self.setAngularVelocity(0.0, 0.0, 0.0)
        self._awake = False
        self._applyActivation()
        return self

    def wake(self) -> "Body3D":
        """叫醒它（唤醒 + 禁止休眠，这样它不会又睡回去）。"""
        self._awake = True
        self._applyActivation()
        return self

    # === 几何（世界 ⇄ 刚体局部、包围盒、命中测试）===

    def matrix(self):
        """刚体 → 世界的旋转矩阵（``glm.mat3``，与渲染器的 ``Rx·Ry·Rz`` 一致）。"""
        matrix = glm.mat4(1.0)
        matrix = glm.rotate(matrix, glm.radians(self.p), glm.vec3(1.0, 0.0, 0.0))
        matrix = glm.rotate(matrix, glm.radians(self.t), glm.vec3(0.0, 1.0, 0.0))
        matrix = glm.rotate(matrix, glm.radians(self.r), glm.vec3(0.0, 0.0, 1.0))
        return glm.mat3(matrix)

    def toWorld(self, point) -> tuple:
        """刚体局部 → 世界。"""
        local = glm.vec3(*(float(v) for v in point[:3]))
        rotated = self.matrix() * local
        return (float(rotated.x) + self.x, float(rotated.y) + self.y,
                float(rotated.z) + self.z)

    def toLocal(self, point) -> tuple:
        """世界 → 刚体局部。"""
        local = glm.transpose(self.matrix()) * glm.vec3(
            float(point[0]) - self.x, float(point[1]) - self.y, float(point[2]) - self.z)
        return (float(local.x), float(local.y), float(local.z))

    def aabb(self) -> tuple:
        """世界坐标包围盒 ``((min_x, min_y, min_z), (max_x, max_y, max_z))``。

        用形状规格自己算（不查 pybullet）：调试绘制、运动学平台唤醒、AABB 查询都用它。
        无限大平面返回一个很大的盒子（±1e4），免得查询/唤醒逻辑出现退化。
        """
        if not self.shapes:
            return ((self.x, self.y, self.z), (self.x, self.y, self.z))
        if any(shape.kind == SHAPE_PLANE for shape in self.shapes):
            return ((self.x - 1.0e4, self.y - 1.0e4, self.z - 1.0e4),
                    (self.x + 1.0e4, self.y + 1.0e4, self.z + 1.0e4))
        corners = []
        for shape in self.shapes:
            low, high = shape.localAABB()
            for sx in (low[0], high[0]):
                for sy in (low[1], high[1]):
                    for sz in (low[2], high[2]):
                        corners.append(self.toWorld((sx, sy, sz)))
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        zs = [c[2] for c in corners]
        return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))

    def containsPoint(self, x, y=None, z=None) -> bool:
        """世界点是否落在这个刚体的形状里（解析判定；鼠标拾取/触发器都能用）。"""
        if y is None and z is None:
            point = _packVector(x, "点")
        else:
            point = (float(x), float(y), float(z))
        local = self.toLocal(point)
        return any(shape.containsBodyLocal(local) for shape in self.shapes)

    #: 顺手起的别名
    contains = containsPoint


    # === 接触 / 生命周期 ===

    def onContact(self, event) -> None:
        """接触回调（子类覆写它，或设 ``body.on_contact = func``）。

        事件由 :meth:`Physics3D.stepFixed` 在**推步之后**派发（pybullet 是快照式
        接触查询，引擎对比两帧的接触对来生成 begin/end），所以回调里增删刚体是安全的。
        """

    def update(self, dt: float, tps: float) -> None:
        """静态/运动学刚体：把引擎这侧改过的 ``x/y/z/p/t/r`` 推给物理。"""
        if self.auto_sync:
            self.syncToBody()
        super().update(dt, tps)

    def delete(self) -> None:
        """从世界与渲染层注销，并销毁 pybullet 刚体（下一步开头真正执行）。"""
        if self._destroyed:
            return
        self._destroyed = True
        world = self.world
        if world is not None:
            world._removeBody(self)          # 排队销毁（含它身上的关节记录）
        self.body = None
        super().delete()

    def onRemoved(self) -> None:
        """兜底：别人直接调 ``GameObject.delete(body)`` 时，物理那边也要清干净。"""
        if not self._destroyed:
            self.delete()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        shape = self.shapes[0] if self.shapes else None
        return (f"<Body3D{label} {self.body_type} at ({self.x:.2f}, {self.y:.2f}, "
                f"{self.z:.2f}) yaw={self.t:.1f}° {shape}"
                f"{'' if self.body is not None else ' (未建体)'}>")


def _quatMultiply(first, second) -> tuple:
    """四元数相乘（都是 ``(x, y, z, w)``；等价于先转 second 再转 first）。"""
    a = glm.quat(float(first[3]), float(first[0]), float(first[1]), float(first[2]))
    b = glm.quat(float(second[3]), float(second[0]), float(second[1]), float(second[2]))
    result = a * b
    return (float(result.x), float(result.y), float(result.z), float(result.w))


#: ⚠ **实测结论**：pybullet 的胶囊/圆柱**轴向是 Z**，引擎对外约定是 **Y**
#: （立着的人形/柱子）。这个四元数把形状局部绕 X 转 -90°：Z → Y。
#: 实测：加它之前胶囊 AABB 是 ``[1.0, 1.0, 3.0]``，加了之后是 ``[1.0, 3.0, 1.0]``，
#: 落地高度 = 半径 + 半高，正确。
_Z_AXIS_TO_Y = eulerToQuaternion(-90.0, 0.0, 0.0)


def _convexMesh(points) -> tuple:
    """凸包 → ``(顶点表, 三角形索引)``：把质心当第 0 个顶点做扇形三角化。"""
    count = len(points)
    center = [sum(p[i] for p in points) / count for i in range(3)]
    vertices = [center] + [list(p) for p in points]
    indices = []
    for index in range(count):
        indices.extend((0, 1 + index, 1 + (index + 1) % count))
    return vertices, indices
