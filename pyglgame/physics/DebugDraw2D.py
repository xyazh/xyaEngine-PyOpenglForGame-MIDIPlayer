"""碰撞体线框调试绘制：把 2D 物理里的形状**画出来**。

为什么不复用 Box2D 自带的 debug draw：pybox2d 的 ``b2DebugDraw`` 需要宿主实现
绘制回调，而引擎的绘制是"顶点进 VBO 一次 draw call"，直接在 Python 侧读
:meth:`Fixture2D.worldVertices` 更简单、也更快。

顶点是**世界坐标**直接烘进去的，画的时候只上传光照等 uniform —— **不碰**
``RenderGlobal`` 的那几个模型矩阵（它们是**整体世界坐标系的调节**，对象不该覆盖）。
"""

from OpenGL.GL import GL_LINES

from ..gameobject.GameObject import GameObject
from ..render.BufferBuilder import COL, POS
from ..render.RenderBuffer import RenderBuffer
from ..RenderGlobal import RenderGlobal
from .Body2D import SHAPE_CHAIN, SHAPE_EDGE, BodyType

#: 各类刚体的默认线框颜色（RGBA）
COLOR_STATIC = (0.45, 0.45, 0.52, 1.0)
COLOR_KINEMATIC = (0.25, 0.60, 1.00, 1.0)
COLOR_DYNAMIC = (0.25, 1.00, 0.45, 1.0)
COLOR_SLEEPING = (0.28, 0.48, 0.34, 1.0)
COLOR_SENSOR = (1.00, 0.35, 0.90, 1.0)
COLOR_HIGHLIGHT = (1.00, 0.95, 0.60, 1.0)


class DebugDraw2D(GameObject):
    """把 ``world`` 里所有刚体的碰撞体画成线框（``GL_LINES``，一次 draw call）。

    ::

        world = Physics2D(gravity=(0.0, -9.8))
        debug = DebugDraw2D(world)               # 自动进当前渲染层
        debug.highlight(crate)                   # 单独点亮一个（拾取测试很好用）
        debug.active = False                     # 关掉

    颜色：静态 = 灰、运动学 = 蓝、动态 = 绿、睡着的动态 = 暗绿、传感器 = 品红。
    """

    def __new__(cls, *args, **kwargs):
        # ⚠ GameObject.__new__ 的第二个位置参数是 is_camera：不在这里把构造参数吃掉，
        # ``DebugDraw2D(world)`` 会被注册成**相机**（world 是真值）→ RenderLayer 会拿它
        # 调 renderStart() 而炸掉。Body2D / Physics2D / FogVolume 都是同样的处理。
        return super().__new__(cls)

    def __init__(self, world, draw_sensors: bool = True, draw_awake_only: bool = False,
                 z: float = 0.02, colors: dict = None, name: str = None) -> None:
        super().__init__()
        if not getattr(world, "is_physics_2d", False):
            raise TypeError(
                f"DebugDraw2D 的第一个参数必须是 Physics2D（世界），"
                f"收到 {type(world).__name__}")
        self.world = world
        self.name = name
        self.draw_sensors = bool(draw_sensors)
        self.draw_awake_only = bool(draw_awake_only)
        #: 线框的 z（正交相机看向 -Z，z 越大越"靠前"，默认抬 0.02 压在实体上面）
        self.z = float(z)
        #: 高亮的刚体（:meth:`highlight`）
        self.highlight_body = None
        self.colors = {
            BodyType.STATIC: COLOR_STATIC,
            BodyType.KINEMATIC: COLOR_KINEMATIC,
            BodyType.DYNAMIC: COLOR_DYNAMIC,
            "sleeping": COLOR_SLEEPING,
            "sensor": COLOR_SENSOR,
            "highlight": COLOR_HIGHLIGHT,
        }
        if colors:
            self.colors.update(colors)
        #: 顶点每帧重建：动态刚体一直在动，静态的也便宜（一个世界几百条线无所谓）
        self.render_buffer = RenderBuffer()
        self.render_buffer.createBuffer(GL_LINES, POS | COL)
        #: 上一帧画了多少条线段（调试/测试用）
        self.line_count = 0

    def highlight(self, body) -> "DebugDraw2D":
        """把某个刚体画成高亮色（传 ``None`` 取消）。"""
        self.highlight_body = body
        return self

    def bodyColor(self, body) -> tuple:
        """一个刚体的颜色（不含传感器 / 高亮的覆盖）。"""
        if body is self.highlight_body:
            return self.colors["highlight"]
        if body.body_type == BodyType.DYNAMIC and not body.awake:
            return self.colors["sleeping"]
        return self.colors.get(body.body_type, COLOR_STATIC)

    def _shouldDraw(self, body) -> bool:
        if getattr(body, "_destroyed", False) or getattr(body, "_body", None) is None:
            return False
        if self.draw_awake_only and body.body_type == BodyType.DYNAMIC and not body.awake:
            return False
        return True


    # === 顶点生成 ===

    def buildLines(self, builder) -> int:
        """把所有形状写成 ``GL_LINES`` 顶点，返回线段数（纯逻辑 → 可单测）。"""
        segments = 0
        for body in tuple(self.world.bodies):
            if not self._shouldDraw(body):
                continue
            base_color = self.bodyColor(body)
            for fixture in body.fixtures:
                if fixture.sensor and not self.draw_sensors:
                    continue
                color = self.colors["sensor"] if fixture.sensor else base_color
                for (x0, y0), (x1, y1) in self._segmentsOf(fixture):
                    builder.pos(x0, y0, self.z).col(*color).end()
                    builder.pos(x1, y1, self.z).col(*color).end()
                    segments += 1
        return segments

    @staticmethod
    def _segmentsOf(fixture) -> list:
        """形状 → 线段列表（盒子/多边形/圆自动连回起点；链形按是否闭合处理）。"""
        vertices = fixture.worldVertices()
        if len(vertices) < 2:
            return []
        if fixture.kind == SHAPE_EDGE:
            return [(vertices[0], vertices[1])]
        pairs = [(vertices[index], vertices[index + 1])
                 for index in range(len(vertices) - 1)]
        if fixture.kind == SHAPE_CHAIN:
            if fixture.loop:
                pairs.append((vertices[-1], vertices[0]))
            return pairs
        pairs.append((vertices[-1], vertices[0]))
        return pairs

    # === 渲染 ===

    def render(self, dt: float, fps: float) -> None:
        if not self.active or self.world is None:
            return
        builder = self.render_buffer.buffer_builder
        builder.clear()
        self.line_count = self.buildLines(builder)
        if self.line_count == 0:
            return
        self.render_buffer.build()
        # 顶点已经是世界坐标：只上传光照 / 环境等的 uniform，不碰那几个全局模型矩阵
        RenderGlobal.instance.updateGlobalUniforms()
        self.render_buffer.draw(False)
        super().render(dt, fps)

    def onRemoved(self) -> None:
        """注销时释放 VBO/VAO（要在 GL 线程里，delete() 会走到这）。"""
        if self.render_buffer is not None:
            self.render_buffer.release()

    def __repr__(self) -> str:
        return (f"<DebugDraw2D {self.world!r} lines={self.line_count}"
                f"{' off' if not self.active else ''}>")
