"""发射器形状：粒子在哪儿出生、朝哪个方向飞。

约定
----

- ``sample(rng)`` 返回 ``(x, y, z, dx, dy, dz)``：位置是**相对发射原点**的偏移，
  方向是**单位向量**（``None`` 表示"用效果表里的 ``direction``"）；
- 只依赖传进来的 ``random.Random``（每个 ``ParticleSystem`` 自己持有一把，
  ``seed`` 给固定值就完全可复现），所以形状是**纯数学**，能脱离 GL 单测；
- 坐标系就是引擎的世界系：2D 用 xy 平面（z 给 0），3D 是 xyz（**y-up**）。

形状一览
--------

=================  ==========================================================
``PointEmitter``   一个点（可加抖动）
``BoxEmitter``     轴对齐盒子内均匀（2D 就把 z 厚度设 0）
``SphereEmitter``  球内 / 球面（可选径向喷出 → 爆炸）
``CircleEmitter``  xy 平面的圆盘 / 圆环（2D 的爆炸、光环、涟漪）
``ConeEmitter``    圆锥（喷口 / 火焰收束）
=================  ==========================================================

自定义：继承 :class:`EmitterShape` 实现 ``sample`` 即可（例如"沿一条曲线出生"——
把 :mod:`pyglgame.math.Curve` 的标量曲线当坐标源）。
"""

import math

from .Particle import _vec3

#: 默认方向（**+Y 朝上**，与引擎的 y-up 一致）
DEFAULT_DIRECTION = (0.0, 1.0, 0.0)


def normalize3(value, fallback=None):
    """把方向向量单位化；零向量 / ``None`` 时回落到 ``fallback``。"""
    if value is None:
        return fallback
    x, y, z = _vec3(value)
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-9:
        return fallback
    return (x / length, y / length, z / length)


def basisOf(axis) -> tuple:
    """给一个轴造一组正交基 ``(u, v)``（都单位长、且都垂直于轴）。

    取"和轴最不平行的世界轴"做叉乘，避免轴正好是 +Y / +X 时算出零向量。
    """
    x, y, z = normalize3(axis, DEFAULT_DIRECTION)
    helper = (1.0, 0.0, 0.0) if abs(x) < 0.9 else (0.0, 1.0, 0.0)
    ux = y * helper[2] - z * helper[1]
    uy = z * helper[0] - x * helper[2]
    uz = x * helper[1] - y * helper[0]
    length = math.sqrt(ux * ux + uy * uy + uz * uz)
    ux, uy, uz = ux / length, uy / length, uz / length
    vx = y * uz - z * uy
    vy = z * ux - x * uz
    vz = x * uy - y * ux
    return (ux, uy, uz), (vx, vy, vz)


class EmitterShape:
    """出生位置 / 方向的分布（基类；子类实现 :meth:`sample`）。"""

    def sample(self, rng) -> tuple:
        """返回 ``(x, y, z, dx, dy, dz)``（方向 ``None`` = 用效果表的方向）。"""
        raise NotImplementedError("EmitterShape 子类必须实现 sample(rng)")

    @staticmethod
    def _jitter(rng, amount: float) -> tuple:
        """盒子内均匀的三个偏移（``amount`` 是半边长）。"""
        if amount <= 0.0:
            return (0.0, 0.0, 0.0)
        return (rng.uniform(-amount, amount), rng.uniform(-amount, amount),
                rng.uniform(-amount, amount))

    def __repr__(self) -> str:
        return "<%s>" % type(self).__name__


class PointEmitter(EmitterShape):
    """一个点：粒子从发射原点出发；``jitter`` 给一点随机偏移（免得"太整齐"）。"""

    def __init__(self, jitter: float = 0.0, direction=None) -> None:
        self.jitter = float(jitter)
        self.direction = normalize3(direction)

    def sample(self, rng) -> tuple:
        x, y, z = EmitterShape._jitter(rng, self.jitter)
        return (x, y, z, self.direction)

    def __repr__(self) -> str:
        return "<PointEmitter jitter=%g>" % self.jitter


class BoxEmitter(EmitterShape):
    """盒子内均匀出生：``size = (宽, 高, 深)``（2D 就把深度设 0 或很小）。"""

    def __init__(self, size=(1.0, 1.0, 0.0), direction=None) -> None:
        self.size = _vec3(size)
        if min(self.size) < 0.0:
            raise ValueError("盒子尺寸不能是负数: %r" % (self.size,))
        self.direction = normalize3(direction)

    def sample(self, rng) -> tuple:
        half_x, half_y, half_z = (value * 0.5 for value in self.size)
        return (rng.uniform(-half_x, half_x), rng.uniform(-half_y, half_y),
                rng.uniform(-half_z, half_z), self.direction)

    def __repr__(self) -> str:
        return "<BoxEmitter size=%s>" % (self.size,)


class SphereEmitter(EmitterShape):
    """球内（默认）或球面（``surface=True``）出生。

    ``radial=True`` 时方向 = 从球心朝外（爆炸 / 冲击波）；否则用 ``direction``
    （没给就用效果表的方向）。
    """

    def __init__(self, radius: float = 1.0, surface: bool = False,
                 radial: bool = True, direction=None) -> None:
        self.radius = float(radius)
        if self.radius < 0.0:
            raise ValueError("球半径不能是负数: %r" % (radius,))
        self.surface = bool(surface)
        self.radial = bool(radial)
        self.direction = normalize3(direction)

    def sample(self, rng) -> tuple:
        # 球面均匀点：z 均匀 + 方位角均匀（比"拒绝采样"快，且循环次数的上限是常数）
        z = rng.uniform(-1.0, 1.0)
        phi = rng.uniform(0.0, math.tau)
        ring = math.sqrt(max(1.0 - z * z, 0.0))
        ux, uy, uz = ring * math.cos(phi), ring * math.sin(phi), z
        if self.surface:
            distance = self.radius
        else:
            # 体积均匀：半径按 u^(1/3) 分布（直接用 u 会让粒子挤在球心）
            distance = self.radius * (rng.random() ** (1.0 / 3.0))
        x, y, z_offset = ux * distance, uy * distance, uz * distance
        if not self.radial:
            return (x, y, z_offset, self.direction)
        if self.surface:
            return (x, y, z_offset, (ux, uy, uz))
        # 球内：方向 = 位置方向；正好落在球心时回落到效果表的方向
        return (x, y, z_offset, normalize3((x, y, z_offset), self.direction))

    def __repr__(self) -> str:
        return ("<SphereEmitter radius=%g %s%s>"
                % (self.radius, "球面" if self.surface else "球内",
                   " 径向" if self.radial else ""))


class CircleEmitter(EmitterShape):
    """xy 平面上的圆盘 / 圆环（2D 用）。

    ``inner_radius`` 到 ``radius`` 之间的环形区域；``inner_radius == radius``
    就是"一圈线"（:meth:`ring`）。``thickness`` 给圆环加一点厚度（不然 2D 光环
    整齐得看不出在动）。``radial=True`` → 方向 = 从圆心朝外。
    """

    def __init__(self, radius: float = 1.0, inner_radius: float = 0.0,
                 thickness: float = 0.0, radial: bool = True, direction=None) -> None:
        self.radius = float(radius)
        self.inner_radius = float(inner_radius)
        if self.radius < 0.0 or self.inner_radius < 0.0:
            raise ValueError("圆半径不能是负数")
        if self.inner_radius > self.radius:
            raise ValueError("内径不能大于外径: %r > %r"
                             % (self.inner_radius, self.radius))
        self.thickness = float(thickness)
        self.radial = bool(radial)
        self.direction = normalize3(direction)

    @classmethod
    def ring(cls, radius: float, thickness: float = 0.0,
             **kwargs) -> "CircleEmitter":
        """圆环（只有一圈，``thickness`` 是厚度抖动）。"""
        kwargs.setdefault("radius", radius)
        kwargs.setdefault("inner_radius", radius)
        kwargs.setdefault("thickness", thickness)
        return cls(**kwargs)

    @classmethod
    def disk(cls, radius: float, **kwargs) -> "CircleEmitter":
        """圆盘（从圆心到半径都有）。"""
        kwargs.setdefault("radius", radius)
        return cls(**kwargs)

    def sample(self, rng) -> tuple:
        angle = rng.uniform(0.0, math.tau)
        # 半径按 sqrt 分布 → 面积均匀（不然粒子全堆在圆心）
        weight = math.sqrt(rng.random())
        distance = self.inner_radius + (self.radius - self.inner_radius) * weight
        if self.thickness:
            distance += rng.uniform(-self.thickness, self.thickness)
        x = math.cos(angle) * distance
        y = math.sin(angle) * distance
        if not self.radial:
            return (x, y, 0.0, self.direction)
        direction = normalize3((math.cos(angle), math.sin(angle), 0.0),
                               self.direction)
        return (x, y, 0.0, direction)

    def __repr__(self) -> str:
        return ("<CircleEmitter %.3g~%.3g%s>"
                % (self.inner_radius, self.radius,
                   " 径向" if self.radial else ""))


class ConeEmitter(EmitterShape):
    """圆锥（喷口）：``angle`` 是**半角（度）**，``radius`` 是喷口半径。

    粒子在喷口的圆盘内出生，方向落在圆锥内（极角按 ``sqrt`` 分布 → 立体角均匀，
    边缘不会挤成一圈）。``direction`` 是圆锥轴（默认 +Y 朝上）。
    """

    def __init__(self, angle: float = 25.0, radius: float = 0.02,
                 direction=DEFAULT_DIRECTION) -> None:
        self.angle = float(angle)
        if not 0.0 <= self.angle <= 180.0:
            raise ValueError("圆锥半角必须在 0~180 度之间: %r" % (angle,))
        self.radius = float(radius)
        self.direction = normalize3(direction, DEFAULT_DIRECTION)

    def sample(self, rng) -> tuple:
        axis = self.direction
        u, v = basisOf(axis)
        # 喷口圆盘内的出生位置
        angle = rng.uniform(0.0, math.tau)
        distance = self.radius * math.sqrt(rng.random())
        du = math.cos(angle) * distance
        dv = math.sin(angle) * distance
        # 圆锥内的方向
        theta = math.radians(self.angle) * math.sqrt(rng.random())
        phi = rng.uniform(0.0, math.tau)
        sin_theta, cos_theta = math.sin(theta), math.cos(theta)
        cos_phi, sin_phi = math.cos(phi), math.sin(phi)
        direction = tuple(axis[index] * cos_theta
                          + (u[index] * cos_phi + v[index] * sin_phi) * sin_theta
                          for index in range(3))
        position = tuple(u[index] * du + v[index] * dv for index in range(3))
        return (position[0], position[1], position[2],
                normalize3(direction, axis))

    def __repr__(self) -> str:
        return ("<ConeEmitter %.1f° 喷口半径%.3g 轴%s>"
                % (self.angle, self.radius, self.direction))


class RotatedEmitter(EmitterShape):
    """把一个形状**整体绕 Z 轴转** ``degrees`` 度（出生位置 + 方向一起转）。

    用途：Tiled 里给对象转了角度（``r``）时，粒子生成器应该跟着转 —— 不然"竖着的火把
    转成横的"方向还是朝上（2D 的旋转就是绕 Z，正好只需要转 ``x/y`` 两个分量）。
    """

    def __init__(self, shape, degrees: float = 0.0) -> None:
        if shape is None or not hasattr(shape, "sample"):
            raise ValueError("RotatedEmitter 需要一个 EmitterShape，收到 %r" % (shape,))
        self.shape = shape
        self.degrees = float(degrees)
        radians = math.radians(self.degrees)
        self.cos = math.cos(radians)
        self.sin = math.sin(radians)

    def rotate(self, x: float, y: float) -> tuple:
        """把一个 ``(x, y)`` 绕原点转 ``degrees`` 度（逆时针）。"""
        return (x * self.cos - y * self.sin, x * self.sin + y * self.cos)

    def sample(self, rng) -> tuple:
        x, y, z, direction = self.shape.sample(rng)
        x, y = self.rotate(x, y)
        if direction is not None:
            dx, dy = self.rotate(direction[0], direction[1])
            direction = (dx, dy, direction[2])
        return (x, y, z, direction)

    def __repr__(self) -> str:
        return "<RotatedEmitter %.3g° %r>" % (self.degrees, self.shape)

