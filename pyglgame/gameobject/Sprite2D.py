"""2D 精灵：一个躺在 XY 平面、朝向 +Z 的带贴图矩形（Phase 7）。

它把"2D 光照"需要的东西凑齐了：**颜色贴图 + 可选的法线贴图 / 高光贴图**，
顶点格式随材质自动带上 ``N2D`` / ``S2D`` 两个位，所以光照开关是逐 draw call 的，
不会泄漏给后面的图元。

::

    from pyglgame.gameobject.Sprite2D import Sprite2D
    from pyglgame.render.Lighting2D import Material2D

    Sprite2D(albedo_tex, width=2.0, height=2.0, x=0.0, y=0.0, z=0.5,
             material=Material2D(normal_map=normal_tex, specular_map=spec_tex))

也可以把材质贴图**直接当构造参数**（与 ``TileMapLayer`` 一致；给了 ``material=`` 就用它）：

    Sprite2D(albedo_tex, normal_map=normal_tex, specular_map=spec_tex, shininess=48.0)

场景文档里这两行都不用写：``res/img/brick.png`` 旁边有 ``brick_n.png`` / ``brick_l.png``
时由 ``scene.registry`` 自动补上（见 ``render/MaterialMaps.py``）。


    sprite.attachShadow()                     # 顺手挂一个隐形遮挡体（要相机开 useShadow2D）
    sprite.transparent = True                # 需要半透明就打开（走半透明 pass）

注意：

- 尺寸烘在顶点里，改 ``width`` / ``height`` 后调 :meth:`rebuild` 才生效；
- ``uv`` 是 ``(u0, v0, u1, v1)`` 图集矩形，翻转就是把它写反；
  **注意法线贴图也要跟着镜像** —— 把材质的 ``scale`` 对应分量设成 ``-1`` 即可；
- 没有贴图时绑 1x1 白纹理（``Texture.white()``），避免采样到上一个 pass 的残留纹理。
"""

import math

import glm
from OpenGL.GL import GL_TRIANGLES, GL_TEXTURE0

from ..render.BufferBuilder import COL, POS, TEX
from ..render.Lighting2D import Material2D
from ..render.RenderBuffer import RenderBuffer
from ..render.Texture import Texture
from ..RenderGlobal import RenderGlobal
from .GameObject import GameObject


class Sprite2D(GameObject):
    """带贴图的 2D 精灵（可选法线/高光贴图）。见模块文档。"""

    def __new__(cls, *args, **kwargs):
        # ⚠ GameObject.__new__ 的第二个位置参数是 is_camera：构造参数必须在这里吃掉，
        # 否则 ``Sprite2D(texture, 2.0, ...)`` 里那个漂亮的位置参数会被当成 is_camera。
        return super().__new__(cls)

    def __init__(self, texture=None, width: float = 1.0, height: float = 1.0,
                 x: float = 0.0, y: float = 0.0, z: float = 0.0, r: float = 0.0,
                 color=(1.0, 1.0, 1.0, 0.9), uv=(0.0, 0.0, 1.0, 1.0),
                 material: Material2D = None, transparent: bool = False,
                 name: str = None, normal_map=None, specular_map=None,
                 normal_strength: float = 1.0, specular_strength: float = 1.0,
                 specular_color=(1.0, 1.0, 1.0), specular_contrast: float = 1.0,
                 shininess: float = 32.0, specular_mode: str = "highlight",
                 receive_shadow: bool = True, lighting_mode: str = "full") -> None:
        super().__init__()
        self.name = name
        self.texture = texture
        self.width = float(width)
        self.height = float(height)
        self.x, self.y, self.z, self.r = float(x), float(y), float(z), float(r)
        self.color = tuple(float(v) for v in color)
        self.uv = tuple(float(v) for v in uv)
        #: 材质（永远不为 None）：给了 ``material`` 就用它，否则按法线 / 高光那几个参数现造
        #: 一个 —— 与 ``TileMapLayer`` 同一套写法（场景文档里也是这么配的）
        self.material = material if material is not None else Material2D(
            normal_map=normal_map, specular_map=specular_map,
            normal_strength=normal_strength, specular_strength=specular_strength,
            specular_color=specular_color, specular_contrast=specular_contrast,
            shininess=shininess, specular_mode=specular_mode,
            receive_shadow=receive_shadow, lighting_mode=lighting_mode)
        self.transparent = bool(transparent)
        self.render_buffer = RenderBuffer()
        self._flags = None
        self._shader = None
        #: 上一次烘进顶点的那组参数（位置 / 角度 / 尺寸 / UV / 颜色）—— 直接改字段也能被发现
        self._built_signature = None
        self.rebuild()

    # === 几何 ===

    @property
    def formatFlags(self) -> int:
        """本精灵的顶点格式（材质决定是否带 ``N2D`` / ``S2D``）。"""
        flags = POS | COL | TEX | self.material.formatFlags()
        return flags

    def rebuild(self) -> "Sprite2D":
        """（重新）生成**CPU 侧**顶点：尺寸 / 位置 / 角度 / UV / 材质开关变了之后调用。

        顶点直接算成**世界坐标**（绕自身原点转 ``r``、再平移到 ``(x, y, z)``）——
        位置不再走 ``RenderGlobal`` 的模型矩阵：那几个矩阵是**整体世界坐标系**的调节
        （平移 / 旋转 / 缩放整个世界），对象不该拿它们摆自己，也不该覆盖它们。

        ⚠ 这里**不碰 GL**：``Sprite2D(...)`` 常常写在模块顶层（GL 上下文还没起来），
        真正的上传推迟到第一次 :meth:`render`（``RenderBuffer.build()`` 会自己判断）。
        移动 / 转动对象之后要调本方法（或直接改 ``uv`` / 尺寸后调），顶点才会跟上。
        """
        flags = self.formatFlags
        if flags != self._flags:
            self.render_buffer.createBuffer(GL_TRIANGLES, flags)
            self._flags = flags
        builder = self.render_buffer.buffer_builder
        builder.clear()
        half_w = self.width * 0.5
        half_h = self.height * 0.5
        u0, v0, u1, v1 = self.uv
        angle = math.radians(self.r)
        cos_a, sin_a = (math.cos(angle), math.sin(angle)) if angle else (1.0, 0.0)
        ox, oy, oz = float(self.x), float(self.y), float(self.z)
        # 两个三角形（左下 → 右下 → 右上 / 左下 → 右上 → 左上）
        corners = ((-half_w, -half_h, u0, v0), (half_w, -half_h, u1, v0),
                   (half_w, half_h, u1, v1), (-half_w, -half_h, u0, v0),
                   (half_w, half_h, u1, v1), (-half_w, half_h, u0, v1))
        for px, py, u, v in corners:
            builder.pos(ox + px * cos_a - py * sin_a,
                        oy + px * sin_a + py * cos_a, oz
                        ).col(*self.color).tex(u, v).end()
        self._built_signature = self.buildSignature()
        return self

    def buildSignature(self) -> tuple:
        """顶点里烘了哪些参数（位置 / 角度 / 尺寸 / UV / 颜色）—— 变了就得重建几何。"""
        return (round(float(self.x), 6), round(float(self.y), 6),
                round(float(self.z), 6), round(float(self.r), 6),
                round(float(self.width), 6), round(float(self.height), 6),
                self.uv, tuple(self.color))

    def rebuildIfNeeded(self) -> bool:
        """只在"顶点该变了"时重建（直接改 ``x`` / ``r`` / ``width`` 也算）；返回是否重建了。

        位置 / 旋转现在是**烘进顶点**的（不再走 ``RenderGlobal`` 的模型矩阵），所以每帧
        渲染前都要对一次签名 —— 没变化就不重建、也不重传 VBO。
        """
        if self.buildSignature() == self._built_signature:
            return False
        self.rebuild()
        return True

    def setTexture(self, texture) -> "Sprite2D":
        self.texture = texture
        return self

    def setMaterial(self, material: Material2D) -> "Sprite2D":
        """换材质（顶点格式可能随之变化 → 自动重建几何）。"""
        self.material = material if material is not None else Material2D()
        self.material.invalidate()
        if self.formatFlags != self._flags:
            self.rebuild()
        return self

    def setSize(self, width: float, height: float) -> "Sprite2D":
        self.width, self.height = float(width), float(height)
        return self.rebuild()

    def setPosition(self, x: float, y: float, z: float = None) -> "Sprite2D":
        """设世界坐标（``z`` 不传就不改）并立刻重建顶点。"""
        self.x, self.y = float(x), float(y)
        if z is not None:
            self.z = float(z)
        return self.rebuild()

    def setRotation(self, degrees: float) -> "Sprite2D":
        """设绕 Z 的角度（度）并立刻重建顶点（位置 / 角度是烘进顶点的）。"""
        self.r = float(degrees)
        return self.rebuild()

    def setColor(self, color=(1.0, 1.0, 1.0, 1.0)) -> "Sprite2D":
        """顶点染色（0-1 RGBA）并立刻重建顶点。"""
        self.color = tuple(float(v) for v in color)
        return self.rebuild()

    def attachShadow(self, **kwargs):
        """挂一个跟着自己的 :class:`~pyglgame.gameobject.ShadowCaster2D.ShadowCaster2D`。

        需要相机开 ``useShadow2D()`` 并且场景里有 ``cast_shadow=True`` 的光源
        （点光 / 平行光 / 聚光都行）。``kwargs`` 透传给遮挡体：
        ``sprite.attachShadow(length=500.0)`` / ``sprite.attachShadow(z=0.6)``。

        提示：遮挡体默认跟着精灵的位置 / 旋转 / 尺寸走，**不需要**再手动同步。
        """
        from .ShadowCaster2D import ShadowCaster2D
        caster = ShadowCaster2D.fromSprite(self, **kwargs)
        return caster

    # === 渲染 ===

    def render(self, dt: float, fps: float) -> None:
        if not self.active:
            return
        render_global = RenderGlobal.instance
        shader = render_global.using_shader
        (self.texture if self.texture is not None else Texture.white()).bind(GL_TEXTURE0)
        if shader is not self._shader:
            # 换了 program（shader 重载等）：材质缓存作废，保证这一帧真的上传一遍
            self.material.invalidate()
            self._shader = shader
        self.material.apply(shader, rotation=self.r)
        # ⚠ **不碰** ``RenderGlobal`` 的 translate / rotate / scale：那几个矩阵是**整体世界
        # 坐标系**的调节（平移 / 旋转 / 缩放整个世界），对象的摆位由顶点自己算（见 rebuild）。
        # 这里只把光照 / 环境等 uniform 与"当前的世界变换"同步给着色器。
        render_global.updateGlobalUniforms()
        self.rebuildIfNeeded()               # 直接改 x / y / r 也会在这里补上
        # 顶点数据有变化（rebuild）或第一次绘制时上传；没变化时 build() 会自己跳过
        self.render_buffer.build()
        self.render_buffer.draw(False)
        super().render(dt, fps)

    def onRemoved(self) -> None:
        """注销时释放 VBO/VAO（贴图由 TextureCache/使用者自己管，不在这里释放）。"""
        if self.render_buffer is not None:
            self.render_buffer.release()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return (f"<Sprite2D{label} {self.width:g}x{self.height:g} at "
                f"({self.x:.2f}, {self.y:.2f}, {self.z:.2f}) r={self.r:g} "
                f"{self.material}>")
