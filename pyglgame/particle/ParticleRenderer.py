"""把粒子写成顶点：**纯函数式**的绘制层（只读粒子 + 写 BufferBuilder，不碰 GL）。

两种模式（``ParticleSystem(render_mode=...)`` / :func:`makeRenderer` 选）：

``"points"``（默认）
    ``GL_POINTS`` + ``POS | COL | SIZ`` —— 每个粒子**一个顶点**，几万个粒子也是一次
    draw call。尺寸走顶点着色器的 ``SIZ``（``gl_PointSize = aSize / gl_Position.z``；
    引擎在 ``BaseWindow`` 已打开 ``GL_VERTEX_PROGRAM_POINT_SIZE``），所以自然**远小
    近大**。它是"方点"：``dis.frag`` 没有用 ``gl_PointCoord``，想要圆形柔边得用四边形
    模式（或自己加一个着色器分支）。

``"quads"``
    广告牌四边形：``GL_TRIANGLES`` + ``POS | COL | TEX``，每个粒子 6 个顶点，始终朝向
    **当前相机**（``RenderGlobal.using_camera``，由 ``RenderLayer`` 每帧设置；拿不到就
    退回 XY 平面 → 正好是 2D 想要的效果），可以贴一张圆形 soft dot 纹理。

尺寸语义（两种模式**不一样**，这是最容易踩的地方）：

- points 模式的 ``size`` 由 ``size_mode`` 决定（见 :class:`PointRenderer`）：
  ``"world"``（默认）= **世界单位**（屏幕上 = ``size`` 个世界单位高，远处自然变小）；
  ``"pixels"`` = **屏幕像素**（与距离无关，2D 想用它）；
  ``"raw"`` = 直接写引擎的 ``SIZ`` 属性（``gl_PointSize = size / gl_Position.z``）。
- quads 模式的 ``size`` 恒为**世界单位的直径**（与投影无关）。

⚠ 引擎原始的 ``SIZ`` 语义（``size / gl_Position.z``）里 ``size`` 的大小**强依赖相机**：
3D 透视下要写成"期望像素 × 距离"这种大数（16 单位远处想要 32 像素 → 约 500），
2D 的 NDC 直通投影下 ``z ≈ 0`` 又会被放大一千倍（想要 30 像素 → 约 0.03）。
默认的 ``"world"`` 就是来消掉这个坑的：它按当前相机的投影把"世界单位"换算成顶点属性，
2D 直通投影（没有世界尺度）会自动按像素算。

``buildVertices(system, builder)`` 返回写了多少个**粒子**（顶点数 = 粒子数 ×
每粒子顶点数），全程不做 GL 调用 —— 单测可以拿一个 BufferBuilder 直接验证顶点。
"""

import glm
from OpenGL.GL import GL_POINTS, GL_TEXTURE0, GL_TRIANGLES

from ..render.BufferBuilder import COL, POS, SIZ, TEX
from ..RenderGlobal import RenderGlobal

#: 取不到相机时的兜底朝向（XY 平面 → 2D 画面里四边形正好正对着屏幕）
FALLBACK_AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

#: 四边形四个角的 UV
QUAD_UV = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))

# === 点模式的尺寸语义（``PointRenderer(size_mode=...)``，见模块文档）===
#: 世界单位：屏幕上 = ``size`` 个世界单位高（3D 默认；远处自然变小）
SIZE_WORLD = "world"
#: 屏幕像素：不管距离，屏幕上就是 ``size`` 像素（2D / UI 友好）
SIZE_PIXELS = "pixels"
#: 引擎原始语义：``gl_PointSize = size / gl_Position.z``
SIZE_RAW = "raw"
SIZE_MODES = (SIZE_WORLD, SIZE_PIXELS, SIZE_RAW)


class ParticleRenderer:
    """绘制层基类：把粒子写成顶点（子类实现 :meth:`buildVertices`）。"""

    #: 顶点格式（决定 VAO 属性布局，与 ``BufferBuilder`` 的位一一对应）
    formatFlags: int = POS | COL
    #: 图元类型
    priType: int = GL_POINTS

    def __init__(self, texture=None, size_scale: float = 1.0) -> None:
        self.texture = texture
        #: 尺寸倍数（在效果表的 size 之上再乘一个系数，调手感用）
        self.size_scale = float(size_scale)

    def buildVertices(self, system, builder) -> int:
        """把 ``system`` 里所有粒子写进 ``builder``；返回写出的**粒子数**。"""
        raise NotImplementedError("ParticleRenderer 子类必须实现 buildVertices()")

    def prepare(self) -> None:
        """绘制前的 GL 状态准备（默认不做任何事）。"""

    def bindTexture(self) -> None:
        """绑定本渲染器要用的贴图（点模式不需要，默认不做任何事）。"""

    def release(self) -> None:
        """释放自己持有的资源（贴图由资源缓存管，这里默认什么都不做）。"""

    def __repr__(self) -> str:
        return "<%s size_scale=%g>" % (type(self).__name__, self.size_scale)


class PointRenderer(ParticleRenderer):
    """``GL_POINTS`` + ``POS | COL | SIZ``：每个粒子一个顶点（见模块文档）。

    ``size_mode``（``size`` 的单位）：

    - ``"world"``（默认）：**世界单位**。按当前相机的投影把它换算成顶点属性 ——
      屏幕上正好 ``size`` 个世界单位高（``gl_PointSize`` 里的 `/z` 由着色器做），
      于是**远处的粒子自然变小**，这正是 3D 想要的。2D 的 NDC 直通投影没有世界
      尺度（``z ≈ 0`` 会被放大一千倍），所以那种情况下自动按 ``"pixels"`` 算。
    - ``"pixels"``：**屏幕像素**，与距离无关（2D / UI 想要"永远 20 像素"就用它）。
    - ``"raw"``：直接写引擎原始的 ``SIZ`` 值（``gl_PointSize = size / gl_Position.z``）。

    拿不到相机（例如脱离窗口单测）时一律退回 ``"raw"``，行为可预期。
    """

    formatFlags = POS | COL | SIZ
    priType = GL_POINTS

    def __init__(self, texture=None, size_scale: float = 1.0,
                 size_mode: str = SIZE_WORLD) -> None:
        super().__init__(texture, size_scale)
        self.size_mode = self._normalizeMode(size_mode)

    @staticmethod
    def _normalizeMode(size_mode) -> str:
        name = str(size_mode).strip().lower()
        if name not in SIZE_MODES:
            raise ValueError("未知的点尺寸语义 %r（可用: %s）"
                             % (size_mode, " / ".join(SIZE_MODES)))
        return name

    def setSizeMode(self, size_mode) -> "PointRenderer":
        """换 ``size`` 的单位（``"world"`` / ``"pixels"`` / ``"raw"``）。"""
        self.size_mode = self._normalizeMode(size_mode)
        return self

    # === 每帧的换算（只在这里读相机，做纯计算）===

    @staticmethod
    def _cameraContext():
        """当前的 ``(projection, view, height, identity_projection)``；缺东西就是 None。"""
        camera = getattr(RenderGlobal.instance, "using_camera", None)
        projection_of = getattr(camera, "m", None)
        projection = getattr(projection_of, "projection", None)
        if projection is None:
            return None
        window = getattr(RenderGlobal.instance, "window", None)
        size = getattr(window, "size", None)
        height = float(getattr(size, "h", 0) or 0)
        if height <= 0.0:
            return None
        view_of = getattr(camera, "viewMatrix", None)
        view = view_of() if view_of is not None else None
        return (projection, view, height,
                bool(getattr(projection_of, "identity_projection", False)))

    def resolveSizeMode(self) -> str:
        """本帧真正生效的模式（拿不到相机 → ``"raw"``；2D 直通投影 → ``"pixels"``）。"""
        if self.size_mode == SIZE_RAW:
            return SIZE_RAW
        context = self._cameraContext()
        if context is None:
            return SIZE_RAW
        if self.size_mode == SIZE_WORLD and context[3]:
            # NDC 直通投影：世界坐标就是屏幕坐标，没有"世界单位"可言 → 按像素最直观
            return SIZE_PIXELS
        return self.size_mode

    @staticmethod
    def _clipZRow(projection, view):
        """``projection * view`` 的 z 行（把粒子的裁剪空间 z 算出来用）。"""
        matrix = projection if view is None else projection * view
        return (float(matrix[0][2]), float(matrix[1][2]),
                float(matrix[2][2]), float(matrix[3][2]))

    @staticmethod
    def _isOrthographic(projection) -> bool:
        """正交投影？第 4 行是 ``[0, 0, 0, 1]``（透视是 ``[0, 0, -1, 0]``）。"""
        row = projection[3]
        return (abs(float(row[3]) - 1.0) < 1e-6
                and abs(float(row[2])) < 1e-6
                and abs(float(row[0])) < 1e-6)

    def _sizeContext(self):
        """``(mode, world_scale, z_row, compensate)``。

        - ``world_scale``：``"world"`` 模式下的"每世界单位多少像素"，即 ``(H/2)·P[1][1]``
          （透视投影的 ``P[1][1] = 1/tan(fov/2)``）；``/z`` 由着色器负责；
        - ``z_row``：用来算每个粒子的**裁剪空间 z**（视图投影矩阵的 z 行）——
          把它乘回 ``SIZ``，着色器里那次除法正好抵消，屏幕上就是精确的尺寸；
        - ``compensate``：要不要做这次抵消。``"pixels"`` 总要；``"world"`` 只在
          **正交投影**下要（见 :meth:`_isOrthographic`）。

        为什么正交要抵消：着色器是 ``gl_PointSize = aSize / max(gl_Position.z, 0.001)``，
        而正交投影的 ``gl_Position.z`` **不是距离** —— 2D 场景里它常在 0.01~0.05
        （相机 z=30、粒子 z≈0 时是 0.03），一次除法就把 4 像素放大成 130 多像素
        （实测：方块上的像素火焰糊成一整片白）。透视投影的 ``gl_Position.z`` 才和距离
        同量级，保持原样（远处自然变小）。
        """
        mode = self.resolveSizeMode()
        if mode == SIZE_RAW:
            return (SIZE_RAW, 1.0, None, False)
        projection, view, height, identity = self._cameraContext()
        if mode == SIZE_PIXELS:
            return (SIZE_PIXELS, 1.0, self._clipZRow(projection, view), True)
        world_scale = height * 0.5 * float(projection[1][1])
        if self._isOrthographic(projection):
            return (SIZE_WORLD, world_scale, self._clipZRow(projection, view), True)
        return (SIZE_WORLD, world_scale, None, False)

    def buildVertices(self, system, builder) -> int:
        mode, world_scale, z_row, compensate = self._sizeContext()
        scale = self.size_scale * world_scale
        color_of = system.colorOf
        size_of = system.sizeOf
        count = 0
        if compensate:
            row_x, row_y, row_z, row_w = z_row
            for particle in system.pool.alive:
                if not particle.alive:
                    continue
                size = size_of(particle) * scale
                clip_z = (row_x * particle.x + row_y * particle.y
                          + row_z * particle.z + row_w)
                if clip_z < 1e-3:
                    # 在相机后面/贴近平面的粒子：给一个下限，免得算出负数或爆炸值
                    clip_z = 1e-3
                builder.pos(particle.x, particle.y, particle.z) \
                    .col(*color_of(particle)) \
                    .siz(size * clip_z).end()
                count += 1
            return count
        for particle in system.pool.alive:
            if not particle.alive:
                continue
            builder.pos(particle.x, particle.y, particle.z) \
                .col(*color_of(particle)) \
                .siz(size_of(particle) * scale).end()
            count += 1
        return count

    def __repr__(self) -> str:
        return ("<PointRenderer size_mode=%s size_scale=%g>"
                % (self.size_mode, self.size_scale))



class QuadRenderer(ParticleRenderer):
    """广告牌四边形（见模块文档）：每个粒子 6 个顶点，朝向当前相机。"""

    formatFlags = POS | COL | TEX
    priType = GL_TRIANGLES

    def __init__(self, texture=None, size_scale: float = 1.0, uv=None,
                 axes=None) -> None:
        super().__init__(texture, size_scale)
        self.uv = tuple(uv) if uv is not None else (0.0, 0.0, 1.0, 1.0)
        #: 固定朝向 ``(right, up)``；``None`` = 每帧问相机（默认）
        self.axes_override = axes

    @staticmethod
    def cameraAxes() -> tuple:
        """当前相机的 ``(right, up)``（世界空间，单位向量）。

        视图矩阵的旋转部分是 ``R^T``，它的第 0 / 1 **行**就是相机在世界里的右 / 上
        方向 —— ``glm`` 的 ``m[列][行]`` 取值方式下正好是 ``(m[0][0], m[0][1],
        m[0][2])`` 与 ``(m[1][0], m[1][1], m[1][2])``。
        """
        camera = getattr(RenderGlobal.instance, "using_camera", None)
        view_of = getattr(camera, "viewMatrix", None)
        if view_of is None:
            return FALLBACK_AXES
        view = view_of()
        if view is None:
            return FALLBACK_AXES
        right = (float(view[0][0]), float(view[1][0]), float(view[2][0]))
        up = (float(view[0][1]), float(view[1][1]), float(view[2][1]))
        if glm.length(glm.vec3(right)) < 1e-6 or glm.length(glm.vec3(up)) < 1e-6:
            return FALLBACK_AXES
        return (right, up)

    def axes(self) -> tuple:
        """本帧要用的 ``(right, up)``（有 :attr:`axes_override` 就用它）。"""
        if self.axes_override is not None:
            return self.axes_override
        return QuadRenderer.cameraAxes()

    def buildVertices(self, system, builder) -> int:
        right, up = self.axes()
        rx, ry, rz = (float(right[0]), float(right[1]), float(right[2]))
        ux, uy, uz = (float(up[0]), float(up[1]), float(up[2]))
        u0, v0, u1, v1 = self.uv
        scale = self.size_scale
        color_of = system.colorOf
        size_of = system.sizeOf
        count = 0
        for particle in system.pool.alive:
            if not particle.alive:
                continue
            half = size_of(particle) * scale * 0.5
            x, y, z = particle.x, particle.y, particle.z
            # 四个角：左下 / 右下 / 右上 / 左上（沿 (right, up) 张开）
            corners = ((x - rx * half - ux * half, y - ry * half - uy * half,
                        z - rz * half - uz * half, u0, v0),
                       (x + rx * half - ux * half, y + ry * half - uy * half,
                        z + rz * half - uz * half, u1, v0),
                       (x + rx * half + ux * half, y + ry * half + uy * half,
                        z + rz * half + uz * half, u1, v1),
                       (x - rx * half + ux * half, y - ry * half + uy * half,
                        z - rz * half + uz * half, u0, v1))
            color = color_of(particle)
            # 两个三角形：左下→右下→右上 / 左下→右上→左上
            for index in (0, 1, 2, 0, 2, 3):
                cx, cy, cz, u, v = corners[index]
                builder.pos(cx, cy, cz).col(*color).tex(u, v).end()
            count += 1
        return count

    def bindTexture(self) -> None:
        # 没贴图就绑 1x1 白纹理：避免采样到上一批留下的贴图
        from ..render.Texture import Texture
        texture = self.texture if self.texture is not None else Texture.white()
        texture.bind(GL_TEXTURE0)

    def __repr__(self) -> str:
        return ("<QuadRenderer size_scale=%g uv=%s%s>"
                % (self.size_scale, self.uv,
                   "" if self.axes_override is None else " 固定朝向"))


def makeRenderer(mode="points", texture=None, **kwargs) -> ParticleRenderer:
    """按名字造渲染器：``"points"`` / ``"quads"``（也可以直接传一个实例）。"""
    if isinstance(mode, ParticleRenderer):
        return mode
    name = str(mode).strip().lower()
    if name in ("points", "point", "gl_points", "sprite_points"):
        return PointRenderer(texture, **kwargs)
    if name in ("quads", "quad", "billboard", "sprites", "sprite"):
        return QuadRenderer(texture, **kwargs)
    raise ValueError("未知的粒子绘制模式 %r（可用: points / quads）" % (mode,))
