"""2D 阴影的几何（Phase 7）：把遮挡多边形"沿光源方向拉长"成阴影区三角形。

## 模型：几何遮挡，不做深度比较

2D 场景分两层：**接收层**（背景 / 地面）与**光源 + 物体层**。光源可以是点光、
平行光、聚光；物体挡住光 → 它背光那一侧就没光了（**硬阴影**，与视角无关）。
"点 ``P`` 有没有被挡住"的精确说法只有一句话：

    从 P **逆着光**往回走（朝光源那边），路上碰到遮挡多边形 → P 在阴影里

把这句话变成几何：把遮挡多边形的每条边**朝远离光源的方向扫出去**，扫出的四边形
（每条边 2 个三角形）的**并集**就是阴影区：

- **平行光**（``direction``）：所有边沿同一个方向 ``d`` 扫 ``length`` 长；
- **点光 / 聚光**（``origin``）：每个顶点各自"从光源位置径向向外"扫 ``length`` 长
  —— 聚光与点光的区别只在光锥（着色器里算），遮挡关系完全一样。

不用先求"轮廓边"（silhouette）：把**所有**边都扫一遍也是对的 —— 背光那几条边扫出的
四边形本来就落在别的边的并集里面（凸的、凹的都成立：从多边形内部一点出发的射线
必定从某条边穿出去）。多画几个三角形，换来实现上零特判，凹多边形也不会漏。

## 与旧做法（方向光阴影贴图）的区别

旧做法是把这些体积画进 **3D 的深度阴影贴图**，因此必须凑两件事：``thickness``
（沿 z 的厚度，否则遮挡平面在光空间里退化成一条线）和带 z 斜率的光方向，
采样端还要 3×3 PCF。现在阴影区就是**屏幕空间遮罩**里的一堆三角形（每盏投影灯
一层，见 :mod:`pyglgame.render.ShadowMask2D`），``dis.frag`` 在光照循环里逐灯采样 ——
光源类型与层高（z）都不再有额外约束，遮挡体也不需要厚度。
"""

import math


def rotatePoints(points, degrees: float) -> list:
    """把二维点集绕原点旋转（度）。"""
    rad = math.radians(float(degrees))
    cosine, sine = math.cos(rad), math.sin(rad)
    return [(x * cosine - y * sine, x * sine + y * cosine) for x, y in points]


def rectPoints(width: float, height: float, inset: float = 0.0) -> list:
    """矩形轮廓（左下起、逆时针）。``inset`` 向内缩进一点，避免与接收面同高时自遮挡。"""
    half_w = max(float(width) * 0.5 - float(inset), 1e-4)
    half_h = max(float(height) * 0.5 - float(inset), 1e-4)
    return [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]


def circlePoints(radius: float, segments: int = 16) -> list:
    """圆（正多边形近似）轮廓。"""
    segments = max(3, int(segments))
    step = 2.0 * math.pi / segments
    return [(math.cos(index * step) * float(radius),
             math.sin(index * step) * float(radius)) for index in range(segments)]


def polygonArea(points) -> float:
    """有向面积（正值 = 逆时针）。"""
    total = 0.0
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        total += x0 * y1 - x1 * y0
    return total * 0.5


def polygonCentroid(points) -> tuple:
    """多边形质心（顶点平均，够用：这里只当参考点）。"""
    count = len(points)
    if count == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in points) / count, sum(p[1] for p in points) / count)


def normalizedDirection2D(direction, fallback=(0.0, -1.0)) -> tuple:
    """3D 光方向 → **平面内**的单位方向（只用 xy 分量）。

    2D 阴影是投在 XY 平面上的：光的 z 分量只影响"照亮多少"（``N·L``），
    不影响阴影往哪边投。方向正好垂直向下（``xy ≈ 0``）时没有平面内投影 →
    返回 ``fallback``（默认"从上往下"），避免退化成零向量。
    """
    x, y = float(direction[0]), float(direction[1])
    length = math.sqrt(x * x + y * y)
    if length < 1e-6:
        return (float(fallback[0]), float(fallback[1]))
    return (x / length, y / length)


def _checkPolygon(points) -> list:
    """校验并归一化轮廓点（至少 3 个点）。"""
    points = [(float(p[0]), float(p[1])) for p in points]
    if len(points) < 3:
        raise ValueError(f"遮挡多边形至少要 3 个点，收到 {len(points)}")
    return points


def _quad(a, b, a_far, b_far, z: float) -> list:
    """四边形 ``a → b → b_far → a_far`` → 两个三角形（顶点都带 z）。"""
    pa = (a[0], a[1], z)
    pb = (b[0], b[1], z)
    pc = (b_far[0], b_far[1], z)
    pd = (a_far[0], a_far[1], z)
    return [(pa, pb, pc), (pa, pc, pd)]


def directionalShadowTriangles(points, direction, length: float, z: float = 0.0) -> list:
    """**平行光**的阴影区三角形（每条边沿 ``direction`` 扫出 ``length`` 长）。

    :param points: 遮挡多边形（世界坐标；凸 / 凹都可以）
    :param direction: 光线传播方向（从光源射出；只用 xy 分量，内部会归一化）
    :param length: 沿光线方向扫出的长度（给够场景尺度即可，例如场景半径 ×4）
    :param z: 顶点写进几何的 z（遮罩 pass 关掉了深度测试，给多少都不影响结果）
    """
    points = _checkPolygon(points)
    dx, dy = normalizedDirection2D(direction)
    reach = max(float(length), 0.0)
    triangles = []
    count = len(points)
    for index in range(count):
        a = points[index]
        b = points[(index + 1) % count]
        triangles.extend(_quad(a, b, (a[0] + dx * reach, a[1] + dy * reach),
                               (b[0] + dx * reach, b[1] + dy * reach), z))
    return triangles


def radialShadowTriangles(points, origin, length: float, z: float = 0.0) -> list:
    """**点光 / 聚光**的阴影区三角形（每个顶点从 ``origin`` 径向向外扫 ``length``）。

    ``origin`` 是光源在平面上的位置（``(x, y)`` 或 ``(x, y, z)``，只取前两个分量）。
    """
    points = _checkPolygon(points)
    origin_x, origin_y = float(origin[0]), float(origin[1])
    reach = max(float(length), 0.0)
    far = []
    for x, y in points:
        dx, dy = x - origin_x, y - origin_y
        distance = math.sqrt(dx * dx + dy * dy)
        if distance < 1e-6:
            # 顶点正好落在光源位置：没有径向方向可用 → 朝屏幕外兜一个方向，避免 NaN
            far.append((x, y - reach))
        else:
            far.append((x + dx / distance * reach, y + dy / distance * reach))
    triangles = []
    count = len(points)
    for index in range(count):
        nxt = (index + 1) % count
        triangles.extend(_quad(points[index], points[nxt], far[index], far[nxt], z))
    return triangles


def shadowVolumeTriangles(points, length: float, direction=None, origin=None,
                          z: float = 0.0) -> list:
    """遮挡多边形 → 阴影区三角形（直接可喂 ``GL_TRIANGLES``）。

    传 ``origin`` = 点光 / 聚光（径向扫出）；只传 ``direction`` = 平行光（同向扫出）。
    两个都传时以 ``origin`` 为准（径向更"具体"）；两个都不传按平行光处理，
    方向回退成"从上往下"。
    """
    if origin is not None:
        return radialShadowTriangles(points, origin, length, z)
    return directionalShadowTriangles(
        points, (0.0, 0.0, 0.0) if direction is None else direction, length, z)


def triangleVertexCount(triangles) -> int:
    """三角形列表的顶点数（``RenderBuffer`` 要的是顶点数）。"""
    return len(triangles) * 3
