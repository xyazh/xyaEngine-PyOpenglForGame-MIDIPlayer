"""参数方程曲线发生器：把"参数 t → 一个值"封装成**可调用对象**。

.. code-block:: python

    from pyglgame.math.Curve import BezierCurve, ParabolaCurve, SineCurve

    bx = BezierCurve(0.0, 2.0, 6.0, 8.0)          # 三次贝塞尔（标量）
    by = ParabolaCurve(a=-1.0, b=4.0)             # -t² + 4t
    bz = SineCurve(amplitude=1.0, frequency=2.0)  # 2 Hz 正弦

    for index in range(11):
        t = index / 10.0
        print(bx(t), by(t), bz(t))                # 三条曲线各取一个值

三条标量曲线可以直接合成一个"点发生器"（就是你写的那种用法，只是少写两行）::

    from pyglgame.math.Curve import ParametricCurve3D
    path = ParametricCurve3D(bx, by, bz)
    point = path(t)                               # → glm.vec3

设计要点
--------

- 所有曲线都是 :class:`ParametricCurve` 的子类，实现 ``value(t)``；
  ``__call__`` 转发它，所以 ``curve(t)`` 就是"取参数 t 处的值"（标量或向量）。
- 控制点/系数**标量和向量都行**（``glm.vec2/vec3`` 这些支持 ``+`` ``-`` ``*``）：
  贝塞尔用 de Casteljau 递推（``a + (b - a)·t``，不展开 Bernstein 多项式，
  任意次数都行、数值更稳），递推式对向量同样成立 —— 于是**一条**
  ``BezierCurve`` 就能直接给出 vec2/vec3 曲线。点也可以直接写
  ``(x, y)`` / ``(x, y, z)`` 元组，会被自动转成 ``glm`` 向量。
- 参数 ``t`` 的含义由调用方决定（时间 / 进度 / 别的）：
  :meth:`ParametricCurve.scaled` 换参数尺度、:meth:`ParametricCurve.looped` 循环、
  :meth:`ParametricCurve.clamped` 夹住端点。
- :meth:`ParametricCurve.sample` 一次取一串值（画线、烘焙轨迹），
  :meth:`ParametricCurve.derivative` / :meth:`ParametricCurve.tangent` 求切线。
- **向量曲线的求导精度**：``glm.vec2/vec3`` 是 float32，向量曲线走默认的
  中心差分数值求导只能到 ~1e-4 量级；要更准就用各分量的曲线求导
  （本模块的标量曲线都实现了**解析**导数，见 ``BezierCurve.derivative`` 等）。

清单
----

=================  ==========================================================
BezierCurve        贝塞尔（任意次数，标量/向量控制点）
CatmullRomCurve    Catmull-Rom 样条（**穿过**所有给定点的平滑曲线）
SineCurve          正弦（``frequency`` 当 Hz、``t`` 当秒正好）
ParabolaCurve      抛物线（``a·t² + b·t + c``，有"顶点式/过三点"构造）
PowerCurve         幂曲线（缓动：``t²``、``t³``、``√t``…）
EllipseCurve       椭圆 / 圆（返回 ``glm.vec2`` 或 ``glm.vec3``）
LinearCurve        直线（``start + (end - start)·t``）
ConstantCurve      常数（给"不变的坐标"占位）
ParametricCurve2D  两条标量曲线 → ``glm.vec2``
ParametricCurve3D  三条标量曲线 → ``glm.vec3``
ScaledCurve        参数缩放 ``g(t) = f(scale·t + offset)``
LoopedCurve        参数循环（折回区间里）
ClampedCurve       参数夹取（超出就停在端点）
=================  ==========================================================
"""

import math

import glm

#: 数值求导的默认步长（中心差分）
DERIVATIVE_STEP = 1e-4


def asCurve(value) -> "ParametricCurve":
    """把"常数或曲线"统一成曲线：数字/向量 → :class:`ConstantCurve`，曲线原样返回。"""
    if isinstance(value, ParametricCurve):
        return value
    return ConstantCurve(value)


def _coercePoint(point):
    """元组/列表 → ``glm.vec2``/``glm.vec3``（可以直接写字面量点）；其它原样返回。

    ``glm`` 的向量支持 ``+`` ``-`` ``*``，Python 元组只支持整数乘法，
    所以先转一道，标量/向量/元组三种写法就都能用了。
    """
    if isinstance(point, (tuple, list)):
        if len(point) == 2:
            return glm.vec2(float(point[0]), float(point[1]))
        if len(point) == 3:
            return glm.vec3(float(point[0]), float(point[1]), float(point[2]))
        raise ValueError("点只能是 2 或 3 个分量（收到 %d 个：%r）"
                         % (len(point), tuple(point)))
    return point


def _coercePoints(points) -> tuple:
    """逐个 :func:`_coercePoint`（控制点列表用）。"""
    return tuple(_coercePoint(point) for point in points)


class ParametricCurve:
    """参数方程曲线基类：``curve(t)`` 取参数 ``t`` 处的值。

    子类只需要实现 :meth:`value`；求导、采样、参数变换都由基类给出。
    """

    def __call__(self, t: float):
        return self.value(t)

    def value(self, t: float) -> None:
        """参数 ``t`` 处的值（子类实现；标量或向量都行）。"""
        raise NotImplementedError("ParametricCurve 子类必须实现 value(t)")

    # === 求导 / 切线 ===

    def derivative(self, t: float, step: float = DERIVATIVE_STEP):
        """d(value)/dt。默认中心差分；有解析式的子类（贝塞尔/正弦/抛物线…）会覆盖。

        .. note:: 数值差分对**向量曲线**（``glm.vec2/vec3``，float32）只能准到
           ~1e-4；需要更精确的切线就用各分量曲线的解析导数。
        """
        step = max(abs(float(step)), 1e-9)
        return (self(t + step) - self(t - step)) / (2.0 * step)

    def tangent(self, t: float):
        """切线：向量曲线返回单位方向，标量曲线返回符号（+1/-1）。"""
        derivative = self.derivative(t)
        if isinstance(derivative, (int, float)):
            return 1.0 if derivative >= 0.0 else -1.0
        length = float(glm.length(derivative))
        if length < 1e-9:
            return derivative
        return derivative / length

    # === 采样 ===

    def sample(self, count: int, start: float = 0.0, end: float = 1.0) -> list:
        """在 ``[start, end]`` 上等距取 ``count`` 个值（含两端）。

        画线（顶点表）、烘焙动画轨迹、离线出图都靠它：
        ``curve.sample(64)`` 就是 64 个点。
        """
        count = int(count)
        if count <= 1:
            return [self(start)]
        step = (float(end) - float(start)) / (count - 1)
        return [self(float(start) + step * index) for index in range(count)]

    # === 参数变换（t 的含义交给调用方）===

    def scaled(self, scale: float = 1.0, offset: float = 0.0) -> "ScaledCurve":
        """换参数尺度：``g(t) = f(scale·t + offset)``（例如把秒映射到 0~1 的进度）。"""
        return ScaledCurve(self, scale, offset)

    def looped(self, start: float = 0.0, end: float = 1.0) -> "LoopedCurve":
        """让参数在 ``[start, end)`` 里循环（循环动画：t 一直涨也不怕）。"""
        return LoopedCurve(self, start, end)

    def clamped(self, start: float = 0.0, end: float = 1.0) -> "ClampedCurve":
        """把参数夹到 ``[start, end]``（超出就停在端点，等价于"播完停住"）。"""
        return ClampedCurve(self, start, end)

    # === 常用曲线的快捷构造（也可以直接 import 那些类）===

    @staticmethod
    def constant(value) -> "ConstantCurve":
        return ConstantCurve(value)

    @staticmethod
    def linear(start: float = 0.0, end: float = 1.0) -> "LinearCurve":
        return LinearCurve(start, end)

    @staticmethod
    def sine(amplitude: float = 1.0, frequency: float = 1.0,
             phase: float = 0.0, offset: float = 0.0) -> "SineCurve":
        return SineCurve(amplitude, frequency, phase, offset)

    @staticmethod
    def parabola(a: float = 0.0, b: float = 0.0, c: float = 0.0) -> "ParabolaCurve":
        return ParabolaCurve(a, b, c)

    @staticmethod
    def bezier(*control_points) -> "BezierCurve":
        return BezierCurve(*control_points)


# === 基础曲线 ===

class ConstantCurve(ParametricCurve):
    """常数曲线：``value(t) = 常数``（给"不变的坐标"占位，省得到处写 lambda）。"""

    def __init__(self, value) -> None:
        self.constant = value

    def value(self, t: float):
        return self.constant

    def derivative(self, t: float, step: float = DERIVATIVE_STEP):
        return self.constant * 0.0

    def __repr__(self):
        return "ConstantCurve(%r)" % (self.constant,)


class LinearCurve(ParametricCurve):
    """直线：``value(t) = start + (end - start)·t``。"""

    def __init__(self, start: float = 0.0, end: float = 1.0) -> None:
        self.start = start
        self.end = end

    def value(self, t: float):
        return self.start + (self.end - self.start) * float(t)

    def derivative(self, t: float, step: float = DERIVATIVE_STEP):
        return self.end - self.start

    def __repr__(self):
        return "LinearCurve(%r, %r)" % (self.start, self.end)


class BezierCurve(ParametricCurve):
    """贝塞尔曲线（任意次数）。

    .. code-block:: python

        x = BezierCurve(0.0, 2.0, 6.0, 8.0)      # 三次：4 个控制点
        x(0.0), x(1.0)                           # → 首尾控制点

        # 控制点也可以是向量：一条曲线直接给出 vec3
        path = BezierCurve(glm.vec3(0, 0, 0), glm.vec3(1, 2, 0),
                           glm.vec3(3, 2, 1), glm.vec3(4, 0, 0))
        path(0.5)                                # → glm.vec3

    - 端点性质：``B(0)`` = 第一个控制点，``B(1)`` = 最后一个控制点；
    - 只给一个控制点 → 退化成常数曲线；
    - 控制点给 ``(x, y)`` / ``(x, y, z)`` 元组也行（自动转成 ``glm.vec2/vec3``）；
    - 求导用"差分控制点再递推"的解析式（比数值差分准，向量同样适用）。
    """

    def __init__(self, *control_points) -> None:
        if len(control_points) < 1:
            raise ValueError("贝塞尔曲线至少要 1 个控制点")
        self.control_points = _coercePoints(control_points)

    @property
    def degree(self) -> int:
        """次数 = 控制点数 - 1。"""
        return len(self.control_points) - 1

    def value(self, t: float):
        return _deCasteljau(self.control_points, float(t))

    def derivative(self, t: float, step: float = DERIVATIVE_STEP):
        degree = self.degree
        if degree < 1:
            return self.control_points[0] * 0.0
        # B'(t) = n · Σ (P(i+1) - P(i)) 的贝塞尔曲线
        differences = tuple(self.control_points[index + 1] - self.control_points[index]
                            for index in range(degree))
        return _deCasteljau(differences, float(t)) * degree

    def __repr__(self):
        return "BezierCurve(%s)" % ", ".join(repr(point) for point in self.control_points)


def _deCasteljau(points, t: float):
    """de Casteljau 递推：``B(t)``。

    只用 ``a + (b - a)·t``（lerp），不展开 Bernstein 多项式 ——
    次数高时也不会因为阶乘/幂次把误差放大，而且标量、向量共用同一份代码。
    """
    layer = list(points)
    while len(layer) > 1:
        layer = [layer[index] + (layer[index + 1] - layer[index]) * t
                 for index in range(len(layer) - 1)]
    return layer[0]


class SineCurve(ParametricCurve):
    """正弦：``value(t) = offset + amplitude·sin(2π·frequency·t + phase)``。

    - ``frequency`` 是"每个 t 单位走几个周期"：**把 t 当秒、它当 Hz 就正好**；
    - ``phase`` 用弧度；``offset`` 是中线（不是 0 的话就不是绕原点振了）；
    - :attr:`period` 给出一个周期对应的 t 长度（``frequency`` 为 0 时是无穷大）。
    """

    def __init__(self, amplitude: float = 1.0, frequency: float = 1.0,
                 phase: float = 0.0, offset: float = 0.0) -> None:
        self.amplitude = float(amplitude)
        self.frequency = float(frequency)
        self.phase = float(phase)
        self.offset = float(offset)

    @property
    def period(self) -> float:
        if self.frequency == 0.0:
            return math.inf
        return 1.0 / abs(self.frequency)

    def value(self, t: float):
        angle = 2.0 * math.pi * self.frequency * float(t) + self.phase
        return self.offset + self.amplitude * math.sin(angle)

    def derivative(self, t: float, step: float = DERIVATIVE_STEP):
        angle = 2.0 * math.pi * self.frequency * float(t) + self.phase
        return (self.amplitude * 2.0 * math.pi * self.frequency * math.cos(angle))

    def __repr__(self):
        return ("SineCurve(amplitude=%r, frequency=%r, phase=%r, offset=%r)"
                % (self.amplitude, self.frequency, self.phase, self.offset))


class ParabolaCurve(ParametricCurve):
    """抛物线：``value(t) = a·t² + b·t + c``（``a = 0`` 就退化成直线）。

    两种更顺手的构造：

    .. code-block:: python

        # 顶点式：顶点在 (2, 3)、开口向下
        curve = ParabolaCurve.fromVertex(2.0, 3.0, a=-1.0)

        # 过三个 (t, value) 点：把三个关键帧串成一条抛物线
        curve = ParabolaCurve.fromPoints((0.0, 1.0), (1.0, 3.0), (2.0, 1.0))
    """

    def __init__(self, a: float = 0.0, b: float = 0.0, c: float = 0.0) -> None:
        self.a = float(a)
        self.b = float(b)
        self.c = float(c)

    @classmethod
    def fromVertex(cls, vertex_t: float, vertex_value: float,
                   a: float = 1.0) -> "ParabolaCurve":
        """顶点式：``value(t) = a·(t - vertex_t)² + vertex_value``。"""
        vertex_t = float(vertex_t)
        vertex_value = float(vertex_value)
        a = float(a)
        return cls(a, -2.0 * a * vertex_t,
                   a * vertex_t * vertex_t + vertex_value)

    @classmethod
    def fromPoints(cls, first: tuple, second: tuple, third: tuple) -> "ParabolaCurve":
        """过三个 ``(t, value)`` 点；三个点的 ``t`` 不能有重复。"""
        (t0, v0), (t1, v1), (t2, v2) = ((float(point[0]), float(point[1]))
                                        for point in (first, second, third))
        denominator = (t0 - t1) * (t0 - t2) * (t1 - t2)
        if abs(denominator) < 1e-12:
            raise ValueError("过三点的抛物线要求三个点的 t 互不相同")
        a = (t2 * (v1 - v0) + t1 * (v0 - v2) + t0 * (v2 - v1)) / denominator
        b = (t2 * t2 * (v0 - v1) + t1 * t1 * (v2 - v0)
             + t0 * t0 * (v1 - v2)) / denominator
        c = (t1 * t2 * (t1 - t2) * v0 + t2 * t0 * (t2 - t0) * v1
             + t0 * t1 * (t0 - t1) * v2) / denominator
        return cls(a, b, c)

    @property
    def vertex(self) -> tuple:
        """顶点 ``(t, value)``；``a`` 为 0 时退化成直线，返回 None。"""
        if self.a == 0.0:
            return None
        vertex_t = -self.b / (2.0 * self.a)
        return (vertex_t, self.value(vertex_t))

    def value(self, t: float):
        t = float(t)
        return self.a * t * t + self.b * t + self.c

    def derivative(self, t: float, step: float = DERIVATIVE_STEP):
        return 2.0 * self.a * float(t) + self.b

    def __repr__(self):
        return "ParabolaCurve(a=%r, b=%r, c=%r)" % (self.a, self.b, self.c)


class PowerCurve(ParametricCurve):
    """幂曲线：``value(t) = offset + scale·t^exponent``（缓动曲线用得多）。

    ``exponent`` = 2/3 是缓入、``0.5`` 是 √t、负数就"先快后慢"。
    ``t`` 为负时用带符号的幂（``-|t|^exponent``），免得算出复数。
    """

    def __init__(self, exponent: float = 2.0, scale: float = 1.0,
                 offset: float = 0.0) -> None:
        self.exponent = float(exponent)
        self.scale = float(scale)
        self.offset = float(offset)

    def value(self, t: float):
        t = float(t)
        if t < 0.0:
            powered = -((-t) ** self.exponent)
        else:
            powered = t ** self.exponent
        return self.offset + self.scale * powered

    def derivative(self, t: float, step: float = DERIVATIVE_STEP):
        return self.scale * self.exponent * (abs(float(t)) ** (self.exponent - 1.0))

    def __repr__(self):
        return ("PowerCurve(exponent=%r, scale=%r, offset=%r)"
                % (self.exponent, self.scale, self.offset))


class EllipseCurve(ParametricCurve):
    """椭圆 / 圆：``value(t) = center + (rx·cos θ, ry·sin θ)``，``θ = 2π·t + phase``。

    ``radius_x == radius_y`` 就是圆；``center`` 给 ``glm.vec2`` 就返回 ``vec2``，
    给 ``glm.vec3`` 就返回 ``vec3``（默认画在 xy 平面上）。``t`` 同样是"圈数"。
    """

    def __init__(self, radius_x: float = 1.0, radius_y: float = 1.0,
                 phase: float = 0.0, center=None) -> None:
        self.radius_x = float(radius_x)
        self.radius_y = float(radius_y)
        self.phase = float(phase)
        self.center = glm.vec2(0.0) if center is None else _coercePoint(center)

    def value(self, t: float):
        angle = 2.0 * math.pi * float(t) + self.phase
        x = self.radius_x * math.cos(angle)
        y = self.radius_y * math.sin(angle)
        if len(self.center) == 3:
            return self.center + glm.vec3(x, y, 0.0)
        return self.center + glm.vec2(x, y)

    def __repr__(self):
        return ("EllipseCurve(radius_x=%r, radius_y=%r, phase=%r, center=%r)"
                % (self.radius_x, self.radius_y, self.phase, self.center))


class CatmullRomCurve(ParametricCurve):
    """Catmull-Rom 样条：**穿过**所有给定点的平滑曲线（"路径过几个路标"就用它）。

    - ``t ∈ [0, 1]`` 覆盖整条路径（内部按段平分）；
    - ``closed=True`` 首尾相接成环；
    - 每段用相邻 4 个点做三次 Hermite 插值 → 每个路标处**切线连续（C¹）**，
      这就是它比"每两点之间各接一条贝塞尔"省事的地方：不用手工调切线；
    - 端点段用"重复端点"当虚拟邻居（标准做法），所以首尾不会乱甩；
    - 点可以是 ``glm.vec2/vec3``，也可以直接写 ``(x, y)`` / ``(x, y, z)`` 元组。
    """

    def __init__(self, *points, closed: bool = False) -> None:
        if len(points) < 1:
            raise ValueError("Catmull-Rom 至少要 1 个点")
        self.points = _coercePoints(points)
        self.closed = bool(closed)

    @property
    def segments(self) -> int:
        if len(self.points) < 2:
            return 0
        return len(self.points) if self.closed else len(self.points) - 1

    def _point(self, index: int):
        """取第 index 个点；两端用"重复端点"，闭环则绕回来。"""
        if self.closed:
            return self.points[index % len(self.points)]
        index = min(max(index, 0), len(self.points) - 1)
        return self.points[index]

    def value(self, t: float):
        segments = self.segments
        if segments <= 0:
            return self.points[0]
        position = min(max(float(t), 0.0), 1.0) * segments
        index = min(int(position), segments - 1)
        local = position - index
        return _catmullRom(self._point(index - 1), self._point(index),
                           self._point(index + 1), self._point(index + 2), local)

    def __repr__(self):
        return "CatmullRomCurve(%s%s)" % (
            ", ".join(repr(point) for point in self.points),
            ", closed=True" if self.closed else "")


def _catmullRom(p0, p1, p2, p3, t: float):
    """一段 Catmull-Rom（三次 Hermite 的统一形式）；标量 / 向量通用。"""
    t2 = t * t
    t3 = t2 * t
    return ((p1 * 2.0)
            + (p2 - p0) * t
            + (p0 * 2.0 - p1 * 5.0 + p2 * 4.0 - p3) * t2
            + (p1 * 3.0 - p0 - p2 * 3.0 + p3) * t3) * 0.5


# === 组合曲线：几条标量曲线拼成一个点发生器 ===

class ParametricCurve2D(ParametricCurve):
    """两条标量曲线 → 二维点发生器：``curve(t) = glm.vec2(x(t), y(t))``。

    .. code-block:: python

        from pyglgame.math.Curve import ParametricCurve2D, SineCurve

        circle_like = ParametricCurve2D(SineCurve(frequency=1.0, phase=math.pi / 2),
                                        SineCurve(frequency=1.0))

    分量也可以直接给数字（自动变成常数曲线）。
    """

    def __init__(self, x, y) -> None:
        self.x = asCurve(x)
        self.y = asCurve(y)

    def value(self, t: float):
        return glm.vec2(self.x(t), self.y(t))

    def __repr__(self):
        return "ParametricCurve2D(x=%r, y=%r)" % (self.x, self.y)


class ParametricCurve3D(ParametricCurve):
    """三条标量曲线 → 三维点发生器：``curve(t) = glm.vec3(x(t), y(t), z(t))``。

    .. code-block:: python

        x = BezierCurve(0.0, 2.0, 6.0, 8.0)
        y = ParabolaCurve(a=-1.0, b=4.0)
        z = SineCurve(frequency=2.0)
        path = ParametricCurve3D(x, y, z)

        point = path(t)              # → glm.vec3
        points = path.sample(64)     # → 64 个 vec3，可以直接喂给画线

    分量也可以直接给数字（自动变成常数曲线），比如
    ``ParametricCurve3D(x, y, 0.0)`` 就是一条平面曲线。
    """

    def __init__(self, x, y, z) -> None:
        self.x = asCurve(x)
        self.y = asCurve(y)
        self.z = asCurve(z)

    def value(self, t: float):
        return glm.vec3(self.x(t), self.y(t), self.z(t))

    def __repr__(self):
        return "ParametricCurve3D(x=%r, y=%r, z=%r)" % (self.x, self.y, self.z)


# === 参数变换：把"调用方的时间"映射成曲线的参数 ===

class ScaledCurve(ParametricCurve):
    """参数缩放：``g(t) = f(scale·t + offset)``（例如把秒映射成 0~1 的进度）。"""

    def __init__(self, curve, scale: float = 1.0, offset: float = 0.0) -> None:
        self.curve = asCurve(curve)
        self.scale = float(scale)
        self.offset = float(offset)

    def value(self, t: float):
        return self.curve(self.scale * float(t) + self.offset)

    def derivative(self, t: float, step: float = DERIVATIVE_STEP):
        inner = self.scale * float(t) + self.offset
        return self.curve.derivative(inner, step) * self.scale

    def __repr__(self):
        return ("ScaledCurve(%r, scale=%r, offset=%r)"
                % (self.curve, self.scale, self.offset))


class LoopedCurve(ParametricCurve):
    """参数循环：把 ``t`` 折回 ``[start, end)``（循环动画用，t 一直涨也不怕）。"""

    def __init__(self, curve, start: float = 0.0, end: float = 1.0) -> None:
        span = float(end) - float(start)
        if span == 0.0:
            raise ValueError("循环区间不能为空（end != start）")
        self.curve = asCurve(curve)
        self.start = float(start)
        self.end = float(end)
        self.span = span

    def value(self, t: float):
        return self.curve(self.start + (float(t) - self.start) % self.span)

    def __repr__(self):
        return ("LoopedCurve(%r, start=%r, end=%r)"
                % (self.curve, self.start, self.end))


class ClampedCurve(ParametricCurve):
    """参数夹取：``t`` 超出 ``[start, end]`` 就取端点值（等价于"播完停住"）。"""

    def __init__(self, curve, start: float = 0.0, end: float = 1.0) -> None:
        self.curve = asCurve(curve)
        self.start = float(start)
        self.end = float(end)

    def value(self, t: float):
        return self.curve(min(max(float(t), self.start), self.end))

    def __repr__(self):
        return ("ClampedCurve(%r, start=%r, end=%r)"
                % (self.curve, self.start, self.end))
