"""3D 碰撞体线框：把 :class:`Body3D` 的形状画出来（``GL_LINES``）。

形状的线框由 :meth:`ShapeSpec3D.localLines` 在**刚体局部**生成，再用
:meth:`Body3D.toWorld` 变换到世界坐标 —— 所以这里**不查 pybullet**，
画出来的东西和物理里的形状一定一致（同一份规格）。

顶点世界坐标直接烘进 VBO；画的时候只上传光照等 uniform —— 不碰 ``RenderGlobal`` 的
那几个模型矩阵（它们是**整体世界坐标系的调节**，对象不该覆盖）。
"""

from OpenGL.GL import GL_LINES

from ..gameobject.GameObject import GameObject
from ..render.BufferBuilder import COL, POS
from ..render.RenderBuffer import RenderBuffer
from ..RenderGlobal import RenderGlobal
from .Body3D import BodyType

COLOR_STATIC = (0.45, 0.45, 0.52, 1.0)
COLOR_KINEMATIC = (0.25, 0.60, 1.00, 1.0)
COLOR_DYNAMIC = (0.25, 1.00, 0.45, 1.0)
COLOR_SLEEPING = (0.28, 0.48, 0.34, 1.0)
COLOR_SENSOR = (1.00, 0.35, 0.90, 1.0)
COLOR_HIGHLIGHT = (1.00, 0.95, 0.60, 1.0)
COLOR_PLANE = (0.35, 0.40, 0.48, 1.0)


class DebugDraw3D(GameObject):
    """把 ``world`` 里所有刚体的形状画成线框（一次 draw call，每帧重建）。

    ::

        world = Physics3D(gravity=(0.0, -9.8, 0.0))
        debug = DebugDraw3D(world)          # 自动进当前渲染层
        debug.highlight(crate)              # 单独点亮一个
        debug.active = False                # 关掉

    颜色：静态 = 灰、运动学 = 蓝、动态 = 绿、睡着的动态 = 暗绿、传感器 = 品红。
    """

    def __new__(cls, *args, **kwargs):
        # ⚠ GameObject.__new__ 的第二个位置参数是 is_camera：必须在这里把构造参数吃掉，
        #   否则 DebugDraw3D(world) 会被注册成"相机"（world 是真值）→ RenderLayer 崩。
        return super().__new__(cls)

    def __init__(self, world, draw_sensors: bool = True, draw_awake_only: bool = False,
                 colors: dict = None, name: str = None) -> None:
        super().__init__()
        if not getattr(world, "is_physics_3d", False):
            raise TypeError(
                f"DebugDraw3D 的第一个参数必须是 Physics3D（世界），"
                f"收到 {type(world).__name__}")
        self.world = world
        self.name = name
        self.draw_sensors = bool(draw_sensors)
        self.draw_awake_only = bool(draw_awake_only)
        #: 高亮的刚体（:meth:`highlight`）
        self.highlight_body = None
        self.colors = {
            BodyType.STATIC: COLOR_STATIC,
            BodyType.KINEMATIC: COLOR_KINEMATIC,
            BodyType.DYNAMIC: COLOR_DYNAMIC,
            "sleeping": COLOR_SLEEPING,
            "sensor": COLOR_SENSOR,
            "highlight": COLOR_HIGHLIGHT,
            "plane": COLOR_PLANE,
        }
        if colors:
            self.colors.update(colors)
        self.render_buffer = RenderBuffer()
        self.render_buffer.createBuffer(GL_LINES, POS | COL)
        #: 上一帧画了多少条线段（调试/测试用）
        self.line_count = 0

    def highlight(self, body) -> "DebugDraw3D":
        """把某个刚体画成高亮色（传 ``None`` 取消）。"""
        self.highlight_body = body
        return self

    def bodyColor(self, body) -> tuple:
        """一个刚体的颜色（不含传感器/平面的覆盖）。"""
        if body is self.highlight_body:
            return self.colors["highlight"]
        if body.body_type == BodyType.DYNAMIC and not body.awake:
            return self.colors["sleeping"]
        if body.body_type == BodyType.STATIC:
            return self.colors[BodyType.STATIC]
        return self.colors.get(body.body_type, COLOR_STATIC)

    def _shouldDraw(self, body) -> bool:
        if getattr(body, "_destroyed", False):
            return False
        if not body.shapes:
            return False
        if self.draw_awake_only and body.body_type == BodyType.DYNAMIC and not body.awake:
            return False
        return True

    # === 顶点生成（纯逻辑，可单测）===

    def buildLines(self, builder) -> int:
        """把当前所有形状写成 ``GL_LINES`` 顶点，返回线段数。"""
        segments = 0
        for body in tuple(self.world.bodies):
            if not self._shouldDraw(body):
                continue
            base_color = self.bodyColor(body)
            for shape in body.shapes:
                if shape.kind == "plane":
                    color = self.colors["plane"]
                elif body.sensor:
                    if not self.draw_sensors:
                        continue
                    color = self.colors["sensor"]
                else:
                    color = base_color
                for start, end in shape.localLines():
                    first = body.toWorld(start)
                    second = body.toWorld(end)
                    builder.pos(*first).col(*color).end()
                    builder.pos(*second).col(*color).end()
                    segments += 1
        return segments

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
        """注销时释放 VBO/VAO。"""
        if self.render_buffer is not None:
            self.render_buffer.release()

    def __repr__(self) -> str:
        return (f"<DebugDraw3D {self.world!r} lines={self.line_count}"
                f"{' off' if not self.active else ''}>")
