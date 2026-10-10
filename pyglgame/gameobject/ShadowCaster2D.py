"""2D 隐形遮挡体（Phase 7）：物体挡光 → 它背后没有光。

用法（配合 ``camera.useShadow2D()`` 与任何 ``cast_shadow=True`` 的光源）::

    from pyglgame.gameobject.ShadowCaster2D import ShadowCaster2D

    ShadowCaster2D.rect(1.2, 1.8, x=0.0, y=1.0)     # 隐形矩形
    ShadowCaster2D.circle(0.8, segments=16, x=2.0)   # 圆形
    sprite.attachShadow()                            # 跟着精灵（位置/旋转/尺寸自动同步）

它不产生任何可见几何：

- **颜色 pass**：默认什么都不画（``setOutline(True)`` 才画黄色轮廓，调试用）；
- **2D 阴影遮罩 pass**（``RenderGlobal.shadow2d_pass``）：画"当前那盏投影灯"
  （``RenderGlobal.shadow2d_light``）的阴影三角形，片元写 0 = 挡住。

顶点是**世界坐标**（对象自己把位置算进顶点，**不碰** ``RenderGlobal`` 的那几个模型
矩阵 —— 它们是整体世界坐标系的调节），几何与原理见
:mod:`pyglgame.render.Shadow2D`：平行光沿光方向扫出、点光/聚光从光源位置径向扫出。
因为几何里带上了光源的位置/方向，**移动遮挡体或移动光源都会重建几何**
（缓存键 = 轮廓 / 自身旋转 / 位置 / 长度 / 光源签名）。
"""

import glm
from OpenGL.GL import GL_LINES, GL_TRIANGLES

from ..render.BufferBuilder import COL, POS
from ..render.Light import DIRECTIONAL
from ..render.RenderBuffer import RenderBuffer
from ..render.Shadow2D import (circlePoints, rectPoints, rotatePoints,
                               shadowVolumeTriangles)
from ..RenderGlobal import RenderGlobal
from .GameObject import GameObject

#: 默认沿光线方向扫出的长度（够覆盖一般 2D 场景：几十米量级）
DEFAULT_LENGTH = 400.0


class ShadowCaster2D(GameObject):
    """一个 2D 遮挡体（默认隐形）。见模块文档。"""

    #: 让引擎/相机一眼认出（遮罩 pass 只挑这些对象画）
    is_shadow_caster = True

    def __new__(cls, *args, **kwargs):
        # ⚠ GameObject.__new__ 的第二个位置参数是 is_camera：必须在这里把构造参数吃掉，
        # 否则 ``ShadowCaster2D(points, x)`` 会被注册成相机 → RenderLayer 调 renderStart() 崩。
        return super().__new__(cls)

    def __init__(self, points=None, x: float = 0.0, y: float = 0.0, z: float = 0.0,
                 r: float = 0.0, length: float = DEFAULT_LENGTH,
                 cast_shadow: bool = True, follows=None, name: str = None) -> None:
        super().__init__()
        self.points = [(float(p[0]), float(p[1]))
                       for p in (points if points else rectPoints(1.0, 1.0))]
        if len(self.points) < 3:
            raise ValueError(f"遮挡多边形至少要 3 个点，收到 {len(self.points)}")
        self.x, self.y, self.z, self.r = float(x), float(y), float(z), float(r)
        self.length = float(length)
        self.cast_shadow = bool(cast_shadow)
        #: 跟着别的对象走（位置/旋转/尺寸每次 update 同步）
        self.follows = follows
        self.name = name
        #: 调试：在颜色 pass 里把轮廓画出来（默认关 = 隐形）
        self.show_outline = False
        self.outline_color = (1.0, 0.9, 0.35, 0.9)
        self.render_buffer = RenderBuffer()
        self.render_buffer.createBuffer(GL_TRIANGLES, POS)
        self.outline_buffer = None
        #: 上次建几何用的参数（变了才重建）
        self._cache_key = None
        #: 上一次写了多少个阴影三角形（调试/测试用）
        self.triangle_count = 0

    # === 便捷构造 ===

    @classmethod
    def rect(cls, width: float, height: float, inset: float = 0.0, **kwargs
             ) -> "ShadowCaster2D":
        """矩形遮挡体（``inset`` 向内缩一点，避免边缘像素正好压在自己身上）。"""
        kwargs.setdefault("points", rectPoints(width, height, inset))
        return cls(**kwargs)

    @classmethod
    def circle(cls, radius: float, segments: int = 16, **kwargs) -> "ShadowCaster2D":
        """圆形（正多边形近似）遮挡体。"""
        kwargs.setdefault("points", circlePoints(radius, segments))
        return cls(**kwargs)

    @classmethod
    def fromSprite(cls, sprite, **kwargs) -> "ShadowCaster2D":
        """跟着一个 :class:`~pyglgame.gameobject.Sprite2D.Sprite2D` 的矩形遮挡体。

        ``kwargs`` 可以覆盖默认值（``x`` / ``y`` / ``z`` / ``r`` / ``length`` /
        ``cast_shadow`` 等）—— 比如把遮挡体抬上一层：``fromSprite(sprite, z=0.6)``。
        """
        options = dict(points=rectPoints(sprite.width, sprite.height),
                       x=sprite.x, y=sprite.y, z=sprite.z, r=sprite.r,
                       follows=sprite)
        options.update(kwargs)
        return cls(**options)


    # === 几何 ===

    def worldPoints(self) -> list:
        """轮廓的世界坐标（自身旋转 + 平移之后）。"""
        return [(self.x + px, self.y + py)
                for px, py in rotatePoints(self.points, self.r)]

    @staticmethod
    def _lightSignature(light) -> tuple:
        """光源的可比较签名（光移动 / 转向 → 几何重建）。"""
        if int(getattr(light, "kind", DIRECTIONAL)) == DIRECTIONAL:
            direction = getattr(light, "direction", (0.0, -1.0, 0.0))
            return ("dir", round(float(direction[0]), 6), round(float(direction[1]), 6))
        return ("pos", round(float(getattr(light, "x", 0.0)), 6),
                round(float(getattr(light, "y", 0.0)), 6))

    def shadowTriangles(self, light) -> list:
        """按某盏灯算阴影三角形（**世界坐标**；纯函数，可脱离 GL 单测）。

        平行光（``Light.DIRECTIONAL``）沿光方向扫出；点光 / 聚光从光源位置径向扫出。
        """
        points = self.worldPoints()
        if int(getattr(light, "kind", DIRECTIONAL)) == DIRECTIONAL:
            return shadowVolumeTriangles(
                points, self.length,
                direction=getattr(light, "direction", (0.0, -1.0, 0.0)))
        return shadowVolumeTriangles(
            points, self.length,
            origin=(float(getattr(light, "x", 0.0)), float(getattr(light, "y", 0.0))))

    def _ensureGeometry(self, light) -> None:
        """按 ``(轮廓, 旋转, 位置, 长度, 光源签名)`` 缓存：只有变了才重建 VBO。"""
        key = (tuple(self.points), round(self.r, 6), round(self.x, 6),
               round(self.y, 6), round(self.length, 4), self._lightSignature(light))
        if key == self._cache_key:
            return
        triangles = self.shadowTriangles(light)
        builder = self.render_buffer.buffer_builder
        builder.clear()
        for triangle in triangles:
            for x, y, z in triangle:
                builder.pos(x, y, z).end()
        self.render_buffer.build()
        self._cache_key = key
        self.triangle_count = len(triangles)

    def _buildOutline(self) -> None:
        """（重新）生成调试轮廓的顶点（世界坐标，``GL_LINES``）。"""
        if self.outline_buffer is None:
            self.outline_buffer = RenderBuffer()
            self.outline_buffer.createBuffer(GL_LINES, POS | COL)
        points = self.worldPoints()
        builder = self.outline_buffer.buffer_builder
        builder.clear()
        for index in range(len(points)):
            x0, y0 = points[index]
            x1, y1 = points[(index + 1) % len(points)]
            builder.pos(x0, y0, self.z).col(*self.outline_color).end()
            builder.pos(x1, y1, self.z).col(*self.outline_color).end()
        self.outline_buffer.build()

    def setOutline(self, active: bool = True, color: tuple = None) -> "ShadowCaster2D":
        """开关调试轮廓（颜色可选）。轮廓在颜色 pass 里按需构建（避免上下文未就绪）。"""
        self.show_outline = bool(active)
        if color is not None:
            self.outline_color = color
        if self.show_outline:
            self._buildOutline()
        return self

    def setPolygon(self, points) -> "ShadowCaster2D":
        """换轮廓（会触发几何重建）。"""
        self.points = [(float(p[0]), float(p[1])) for p in points]
        if len(self.points) < 3:
            raise ValueError("遮挡多边形至少要 3 个点")
        self._cache_key = None
        if self.show_outline:
            self._buildOutline()
        return self

    @staticmethod
    def _uploadGlobalUniforms() -> None:
        """顶点已经是世界坐标 → 只把光照 / 环境等的 uniform 同步过去。

        **不复位**那几个模型矩阵：它们是**整体世界坐标系**的调节（谁都可以在外面设
        "把整个世界转 30°"），对象不该覆盖它们。
        """
        RenderGlobal.instance.updateGlobalUniforms()


    # === 生命周期 / 渲染 ===

    def update(self, dt: float, tps: float) -> None:
        # 跟着别的对象走（常见用法：跟一个精灵）
        target = self.follows
        if target is not None and not getattr(target, "_deleted", False):
            self.x, self.y, self.r = target.x, target.y, target.r
            width = getattr(target, "width", None)
            height = getattr(target, "height", None)
            if width is not None and height is not None:
                rect = rectPoints(width, height)
                if rect != self.points:
                    self.points = rect
                    self._cache_key = None
                    if self.show_outline:
                        self._buildOutline()
        super().update(dt, tps)

    def render(self, dt: float, fps: float) -> None:
        if not self.active:
            return
        render_global = RenderGlobal.instance
        light = getattr(render_global, "shadow2d_light", None)
        if getattr(render_global, "shadow2d_pass", False) and light is not None:
            # 2D 阴影遮罩 pass：画这盏灯的阴影三角形（顶点是世界坐标）
            if not self.cast_shadow:
                return
            self._ensureGeometry(light)
            if self.triangle_count == 0:
                return
            if not getattr(render_global, "shadow2d_matrix_ready", False):
                # 相机在遮罩 pass 里已经上传过一次（**每层一次**）：这里不必再传。
                # 自己单独调 ``render()``（没有相机那一步）时补上。
                self._uploadGlobalUniforms()
            self.render_buffer.draw(False)
            return
        # 颜色 pass：默认隐形（只有调试开关打开时才画轮廓）
        if self.show_outline:
            if self.outline_buffer is None:
                self._buildOutline()
            self._uploadGlobalUniforms()
            self.outline_buffer.draw(False)

    def onRemoved(self) -> None:
        """注销时释放 VBO/VAO。"""
        for buffer in (self.render_buffer, self.outline_buffer):
            if buffer is not None:
                buffer.release()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return (f"<ShadowCaster2D{label} {len(self.points)} 点 at "
                f"({self.x:.2f}, {self.y:.2f}, {self.z:.2f}) 长度 {self.length:g}"
                f"{'' if self.cast_shadow else '（不投影）'}>")
