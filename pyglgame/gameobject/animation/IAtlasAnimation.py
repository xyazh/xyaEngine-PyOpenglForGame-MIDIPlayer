"""图集帧动画（接口）：按**帧名**从 :class:`~pyglgame.render.TextureAtlas.TextureAtlas`
取矩形，随时间切 UV —— 也就是"LibreSprite / Aseprite 导出的图集直接能播"。

与 :class:`~pyglgame.gameobject.animation.IAnimationFrame.IAnimationFrame` 的分工
----------------------------------------------------------------------------

============================================  ==============================================
``IAnimationFrame``                           ``IAtlasAnimation``（本模块）
============================================  ==============================================
一张精灵表**等分**成 ``rows × columns``       图集里每帧是**任意矩形**（打包器给的）
帧 = **帧号**                                  帧 = **帧名**（打包顺序变了也不会播错）
自己算 UV（``col * s_columns``）               查 ``TextureAtlas.frameUV``
trim 帧会抖（没有原始矩形信息）                用 ``source_rect`` 还原 trim，**不抖**
============================================  ==============================================

用法（和 ``IAnimationFrame`` 一样：接口写在**左边**）::

    from pyglgame.gameobject.Sprite2D import Sprite2D
    from pyglgame.gameobject.animation import IAtlasAnimation
    from pyglgame.render.TextureAtlas import TextureAtlas

    atlas = TextureAtlas.fromAseprite("res/atlas/hero.json")

    class Hero(IAtlasAnimation, Sprite2D):
        def __init__(self, x=0.0, y=0.0):
            super().__init__(atlas=atlas, animation="idle", x=x, y=y)

    hero = Hero(x=-2.0, y=-3.0)
    hero.playAnimation("walk")            # 切动作（帧率 / 循环跟着数据走）
    hero.setFrame("walk_3")               # 也可以直接跳某一帧

想让图集动画也吃 **2D 光照**（法线 / 高光贴图）就给 ``material``：

.. code-block:: python

    **其实不用手写下面这一段**：图集旁边有 ``<名字>_normal`` / ``<名字>_n``（法线）或
    ``<名字>_lightmap`` / ``<名字>_l``（高光）时，``IAtlasAnimation`` 会自动用图集自带的
    那一份（见 :mod:`pyglgame.render.MaterialMaps`）—— 写 ``material=`` 只是"想自己调
    参数 / 贴图另有路径"的情况：

    from pyglgame.render.Lighting2D import Material2D
    from pyglgame.render.TextureCache import loadTexture

    material = Material2D(normal_map=loadTexture("res/atlas/chr_normal.png"),
                          specular_map=loadTexture("res/atlas/chr_specular.png"),
                          shininess=48.0, normal_strength=1.2)

    class Hero(IAtlasAnimation, Sprite2D):
        def __init__(self, x=0.0, y=0.0):
            super().__init__(atlas=atlas, animation="idle", material=material,
                             x=x, y=y)

顶点格式会自动带上 ``N2D`` / ``S2D`` 位（挂上/摘掉时会重建顶点缓冲），
绘制时 :meth:`Material2D.apply` 把法线/高光贴图绑到 ``tex3`` / ``tex4``。

⚠ **法线 / 高光贴图必须是"和图集同布局的图集"**（帧矩形一一对应）——
因为逐帧用的是同一套 UV。做法：把整张颜色图集过一遍
``heightToNormal`` / ``brightnessToSpecular`` 存成同尺寸 PNG 即可
（生成脚本见 ``docs/2D帧动画与场景导入.md`` 的"给图集加法线 / 高光"一节）。

也可以当"对象的零件"用（宿主在 ``update`` / ``render`` 里转发）。

字段命名（**硬约定**，与 ``IAnimationFrame`` 同一套前缀）
------------------------------------------------------

宿主的 ``texture`` / ``width`` / ``height`` / ``uv`` / ``render_buffer`` / ``color``
一概不碰；动画自己的东西全部加 ``frame_`` 前缀：``frame_texture`` / ``frame_width`` /
``frame_height`` / ``frame_left`` / ``frame_right`` / ``frame_top`` / ``frame_bottom`` /
``frame_render_buffer`` / ``frame_name`` / ``frame_scale`` / ``frame_color`` /
``frame_sequence`` / ``frame_fps``。

顶点是**本地坐标**（轴心在原点），位置 / 旋转 / ``z`` 走宿主的模型矩阵 —— 与
``IAnimationFrame`` 完全一致，所以两者可以互换使用。
"""

import math

import glm
from OpenGL.GL import GL_TEXTURE0, GL_TRIANGLES

from ...render.BufferBuilder import COL, N2D, POS, S2D, TEX
from ...render.Lighting2D import Material2D
from ...render.RenderBuffer import RenderBuffer
from ...render.Texture import Texture
from ...render.TextureAtlas import TextureAtlas
from ...RenderGlobal import RenderGlobal
from .FrameSequence import FrameSequence
from .IAnimationBase import IAnimationBase


class IAtlasAnimation(IAnimationBase):
    """图集帧动画（见模块文档）。"""

    def __init__(self, atlas=None, animation=None, fps: float = None,
                 scale=(1.0, 1.0), color=(1.0, 1.0, 1.0, 1.0), blend_mode: str = None,
                 speed: float = 1.0, loop: bool = True, autoplay: bool = True,
                 self_draw: bool = True, pivot=None, material=None,
                 name: str = None, **kwargs) -> None:
        """
        :param atlas: :class:`~pyglgame.render.TextureAtlas.TextureAtlas`（或它的 dict）
        :param animation: 动作名（``str``）/ ``FrameSequence`` / **帧名列表**
        :param fps: 覆盖图集里的帧率（``None`` = 用数据里的）
        :param scale: 每帧的显示尺寸倍数（顶点尺寸 = 帧尺寸 × scale）
        :param color: 顶点色（``alpha`` 可以直接做淡入淡出）
        :param speed: 播放速度倍数（负值 = 倒放，与 ``IAnimationBase`` 一致）
        :param loop: 是否循环
        :param autoplay: 构造后是否立刻开始播
        :param self_draw: 是否自己画（``False`` 时配合 ``driveSprite`` 让精灵画）
        :param pivot: 轴心覆盖（像素，相对帧左上角、Y 向下）；``None`` = 用图集里的
        :param material: :class:`~pyglgame.render.Lighting2D.Material2D`（可选）——
            给了 ``normal_map`` / ``specular_map`` 就参与 2D 光照（顶点格式自动带上
            ``N2D`` / ``S2D`` 位）。⚠ 法线 / 高光贴图必须是**和图集同布局的图集**
            （帧矩形一一对应），否则光照会跟着帧"漂"；见 ``docs/2D光照与阴影.md``
        :param name: 调试用名字
        """
        super().__init__(**kwargs)               # 其余参数透传给宿主（x / y / material …）
        if isinstance(atlas, dict):
            atlas = TextureAtlas.fromDict(atlas)
        if atlas is None or not hasattr(atlas, "frameUV"):
            raise TypeError("IAtlasAnimation 需要一个 TextureAtlas（或它的 dict）")
        self.atlas = atlas
        self.name = name
        #: 材质：显式给的优先；没给就在**图集自带**的配对图里找
        #: （``chr.png`` 旁边的 ``chr_normal.png`` / ``chr_l.png``，见 ``render/MaterialMaps.py``）
        if material is None:
            material = atlas.material()
        #: 材质（永不 ``None``：没给就是一个"没有贴图"的材质 —— 与 ``Sprite2D`` 一致）
        self.material = material if material is not None else Material2D()
        self._flags = None
        self._shader = None
        # === 动画自己的槽（全部带 frame_ 前缀）===
        self.frame_texture = None
        self.frame_render_buffer = RenderBuffer()
        self._ensureBufferFormat()
        self.frame_scale = (float(scale[0]), float(scale[1]))
        self.frame_color = tuple(float(v) for v in color)
        self.blend_mode = blend_mode
        self.self_draw = bool(self_draw)
        self.flip_x = False
        self.flip_y = False
        self.frame_pivot = pivot
        self.frame_sequence = None
        self.frame_name = None
        self.frame_left = self.frame_right = 0.0
        self.frame_bottom = self.frame_top = 0.0
        self.frame_width = self.frame_height = 0.0
        self.frame_fps = None
        self._drawn_key = None
        self.drawn_frames = 0
        self.last_frame = -1
        #: 帧切换回调 ``callable(动画对象, 帧号)``
        self.on_frame = None
        self.animation_speed = float(speed)
        self.animation_loop = bool(loop)
        self.setAnimation(animation, fps=fps, keepTime=False)
        self.playing = bool(autoplay)
        self.finished = False
        self.applyFrame()
        self.refreshAnimation()

    # === 材质（法线 / 高光贴图）===

    @property
    def formatFlags(self) -> int:
        """顶点格式：``POS | COL | TEX`` + 材质要求的 ``N2D`` / ``S2D`` 位。

        ⚠ ``N2D`` / ``S2D`` **不产生新的顶点属性**，它们只是告诉片元着色器
        "``tex3`` / ``tex4`` 是 2D 法线 / 高光贴图"（见 ``docs/2D光照与阴影.md``），
        所以顶点行数不变，但 ``formatType`` 必须跟着换 —— 换它是靠**重建
        ``RenderBuffer``**（见 :meth:`_ensureBufferFormat`）。
        """
        return POS | COL | TEX | self.material.formatFlags()

    def _ensureBufferFormat(self) -> None:
        """材质开关变了（法线/高光贴图挂上或摘掉）→ 用新格式重建顶点缓冲。"""
        flags = self.formatFlags
        if flags != self._flags:
            self.frame_render_buffer.createBuffer(GL_TRIANGLES, flags)
            self._flags = flags
            self._drawn_key = None

    def setMaterial(self, material) -> "IAtlasAnimation":
        """换材质（``None`` = 回到"没有法线/高光"）；顶点格式跟着自动调整。"""
        self.material = material if material is not None else Material2D()
        self.material.invalidate()
        self._ensureBufferFormat()
        self._drawn_key = None
        return self

    def setNormalMap(self, texture, strength: float = None) -> "IAtlasAnimation":
        """只换法线贴图（``None`` = 摘掉）。``texture`` 要给**图集布局**的法线图。"""
        self.material.normal_map = texture
        if strength is not None:
            self.material.normal_strength = float(strength)
        self.material.invalidate()
        self._ensureBufferFormat()
        return self

    def setSpecularMap(self, texture, strength: float = None) -> "IAtlasAnimation":
        """只换高光贴图（``None`` = 摘掉）。"""
        self.material.specular_map = texture
        if strength is not None:
            self.material.specular_strength = float(strength)
        self.material.invalidate()
        self._ensureBufferFormat()
        return self

    # === 动作（切动作 / 列出动作）===

    @property
    def animations(self) -> list:
        """图集里可用的动作名。"""
        return self.atlas.animationNames

    def setAnimation(self, animation=None, fps: float = None,
                     keepTime: bool = False) -> "IAtlasAnimation":
        """换当前动作：接受动作名 / ``FrameSequence`` / 帧名列表。

        - ``animation is None`` → 用图集的**第一个**动作（没有动作就报错：硬凑一个空动画
          只会让问题更难查）；
        - ``fps`` 覆盖帧率；``keepTime=True`` 保留时间轴位置（切动作不闪帧）。
        """
        if animation is None:
            names = self.animations
            if not names:
                raise ValueError("图集 %r 里没有任何动作（也没有帧可播）" % (self.atlas,))
            animation = names[0]
        if isinstance(animation, FrameSequence):
            sequence = animation
        elif isinstance(animation, str):
            sequence = self.atlas.animation(animation)
        elif isinstance(animation, (list, tuple)):
            sequence = self.atlas.frameSequence(animation, fps=fps, name="inline")
        else:
            raise TypeError("animation 必须是动作名 / FrameSequence / 帧名列表，收到 %r"
                            % (type(animation).__name__,))
        if fps is not None:
            sequence = sequence.withFps(fps)
        self.frame_sequence = sequence
        self.frame_fps = sequence.fps
        self.duration = sequence.duration
        if not keepTime:
            self.animation_time = 0.0
            self.animation_loops = 0
            self.finished = False
        self.last_frame = -1
        self._drawn_key = None
        self.applyFrame()
        return self

    def playAnimation(self, name, restart: bool = True, fps: float = None,
                      loop: bool = None) -> "IAtlasAnimation":
        """切到动作 ``name`` 并播放（``restart=True`` 从第 0 帧开始）。"""
        self.setAnimation(name, fps=fps, keepTime=not restart)
        if loop is not None:
            self.animation_loop = bool(loop)
        return self.play(restart=restart)

    def setAtlas(self, atlas) -> "IAtlasAnimation":
        """换图集（``dict`` 也认）：贴图槽与当前动作都会跟着重来。"""
        if isinstance(atlas, dict):
            atlas = TextureAtlas.fromDict(atlas)
        if atlas is None or not hasattr(atlas, "frameUV"):
            raise TypeError("setAtlas 需要一个 TextureAtlas（或它的 dict）")
        current = self.frame_sequence.name if self.frame_sequence else None
        self.atlas = atlas
        self.frame_texture = None
        self._drawn_key = None
        self.setAnimation(self.tryAnimation(current) if current else None, keepTime=False)
        return self

    def tryAnimation(self, name) -> "FrameSequence":
        """"有就切"：图集里没有这个动作时返回 ``None``（不抛）。"""
        return self.atlas.tryAnimation(name)

    # === 帧与几何 ===

    @property
    def frameCount(self) -> int:
        """当前动作的帧数（含方向展开）。"""
        if self.frame_sequence is None:
            return 0
        return len(self.frame_sequence.resolvedFrames())

    @property
    def frameTime(self) -> float:
        """单帧时长（秒）。"""
        if self.frame_sequence is None:
            return 0.0
        return self.frame_sequence.frameTime

    @property
    def frameIndex(self) -> int:
        """当前帧号（0 起，按时间轴算；与 ``IAnimationFrame.frameIndex`` 同语义）。"""
        if self.frame_sequence is None:
            return 0
        return self.frame_sequence.indexAt(self.animation_time, loop=self.animation_loop)

    @property
    def framesPlayed(self) -> list:
        """当前动作**展开后**的帧名列表（``pingpong`` 已经展开）。"""
        if self.frame_sequence is None:
            return []
        return self.frame_sequence.resolvedFrames()

    def frameNames(self) -> list:
        """:meth:`framesPlayed` 的方法式写法（喜好而已）。"""
        return self.framesPlayed

    def setFrame(self, index_or_name) -> "IAtlasAnimation":
        """跳到某一帧：``int`` = 帧号，``str`` = 帧名。"""
        names = self.framesPlayed
        if not names:
            return self
        if isinstance(index_or_name, str):
            if index_or_name not in names:
                raise KeyError("动作 %r 里没有帧 %r（有 %r）"
                               % (self.frame_sequence.name, index_or_name, names))
            index = names.index(index_or_name)
        else:
            index = int(index_or_name) % len(names)
        return self.seek(index * self.frameTime)

    def bounds(self, name: str = None) -> tuple:
        """当前（或指定）帧的世界矩形 ``(left, right, bottom, top)``，轴心在原点。"""
        frame_name = self.frameName if name is None else name
        if frame_name is None:
            return (0.0, 0.0, 0.0, 0.0)
        if self.frame_pivot is not None:
            return self._boundsWithPivot(frame_name)
        return self.atlas.frameBounds(frame_name, self.frame_scale)

    def _boundsWithPivot(self, name: str) -> tuple:
        """``frame_pivot`` 覆盖时的矩形（与 ``TextureAtlas.frameBounds`` 同一套公式）。"""
        sx, sy, sw, sh = self.atlas.frameSourceRect(name)
        px, py = float(self.frame_pivot[0]), float(self.frame_pivot[1])
        ppu = self.atlas.pixels_per_unit
        scale_x, scale_y = self.frame_scale
        return ((sx - px) / ppu * scale_x, (sx + sw - px) / ppu * scale_x,
                (py - sy - sh) / ppu * scale_y, (py - sy) / ppu * scale_y)

    @property
    def frameName(self) -> str:
        """当前帧名（没有帧时 ``None``）。"""
        names = self.framesPlayed
        if not names:
            return None
        return names[self.frameIndex]

    def applyFrame(self) -> "IAtlasAnimation":
        """按当前帧刷新 ``frame_left/right/bottom/top`` 与 ``frame_width/height``（纯 CPU）。"""
        name = self.frameName
        self.frame_name = name
        if name is None:
            self.frame_left = self.frame_right = 0.0
            self.frame_bottom = self.frame_top = 0.0
            self.frame_width = self.frame_height = 0.0
            return self
        left, right, bottom, top = self.bounds(name)
        self.frame_left, self.frame_right = left, right
        self.frame_bottom, self.frame_top = bottom, top
        self.frame_width = right - left
        self.frame_height = top - bottom
        return self

    def setScale(self, scale) -> "IAtlasAnimation":
        """换显示尺寸倍数（下一帧生效）。"""
        self.frame_scale = (float(scale[0]), float(scale[1]))
        return self.applyFrame()

    def setColor(self, color) -> "IAtlasAnimation":
        """换顶点色（``(r, g, b, a)``；``a`` 可以拿来做淡入淡出）。"""
        self.frame_color = tuple(float(v) for v in color)
        self._drawn_key = None
        return self

    def setFlipX(self, flip: bool) -> "IAtlasAnimation":
        """水平翻转（只翻 UV，顶点不动）。"""
        self.flip_x = bool(flip)
        self._drawn_key = None
        return self

    def setFlipY(self, flip: bool) -> "IAtlasAnimation":
        """垂直翻转（只翻 UV，顶点不动）。"""
        self.flip_y = bool(flip)
        self._drawn_key = None
        return self

    def setSelfDraw(self, active: bool = True) -> "IAtlasAnimation":
        """开关"动画自己画"（关掉后配合 :meth:`driveSprite` 让宿主精灵画）。"""
        self.self_draw = bool(active)
        return self

    # === UV 与贴图 ===

    def frameUV(self, name: str = None) -> tuple:
        """当前帧的 ``(u0, v0, u1, v1)``：**``v0`` = 顶边**（含 ``flip_x`` / ``flip_y``）。

        与 ``IAnimationFrame.frameUV()`` 同一套约定，所以下面画顶点时可以照抄那段
        "下边取 ``v1``、上边取 ``v0``"。
        """
        frame_name = self.frameName if name is None else name
        if frame_name is None:
            return (0.0, 0.0, 1.0, 1.0)
        u0, v0, u1, v1 = self.atlas.frameUV(frame_name)
        if self.flip_x:
            u0, u1 = u1, u0
        if self.flip_y:
            v0, v1 = v1, v0
        return (u0, v0, u1, v1)

    def spriteUV(self, flipped: bool = False, name: str = None) -> tuple:
        """给 ``Sprite2D.uv`` 用的 UV（第一个 ``v`` 对应四边形**下**边）。

        语义与 ``IAnimationFrame.spriteUV`` 完全一致：``flipped=False`` →
        ``(u0, v1, u1, v0)``；``flipped=True``（精灵的图集翻过）→ ``(u0, 1-v1, u1, 1-v0)``。
        """
        u0, v0, u1, v1 = self.frameUV(name)
        if flipped:
            return (u0, 1.0 - v1, u1, 1.0 - v0)
        return (u0, v1, u1, v0)

    def driveSprite(self, sprite, rebuild: bool = True,
                    texture=None, flipped: bool = False) -> "IAtlasAnimation":
        """把当前帧的 UV 交给一个 ``Sprite2D``（让**精灵**去画，动画只负责选帧）。

        参数语义与 ``IAnimationFrame.driveSprite`` 一模一样（``sprite is self`` 时不动贴图，
        免得把宿主的 ``texture`` 槽写成动画自己的 ``frame_texture``）。
        """
        if texture is not None:
            sprite.setTexture(texture)
        elif self.frame_texture is not None and sprite is not self:
            sprite.setTexture(self.frame_texture)
        sprite.uv = self.spriteUV(flipped)
        if rebuild:
            sprite.rebuild()
        return self

    def ensureTexture(self) -> "Texture":
        """拿到要用的贴图：现成贴图 > 图集懒上传 > 1×1 白纹理（第一次绘制才碰 GL）。"""
        if self.frame_texture is None:
            if self.atlas is not None:
                self.frame_texture = self.atlas.texture()
            else:
                self.frame_texture = Texture.white()
        return self.frame_texture

    def setTexture(self, texture) -> "IAtlasAnimation":
        """换贴图（``None`` = 回到"按图集懒上传"）。"""
        self.frame_texture = texture
        return self

    # === 钩子 ===

    def updateAnimation(self, dt: float) -> None:
        """帧号变了就刷新几何并回调 ``on_frame(动画对象, 帧号)``（脚步 / 判定挂这里）。

        注意：**每帧都会对一次帧号**（只是比较，几乎零开销），变了才重建几何 ——
        因为图集里每帧的矩形/尺寸可以不同，不能像等分精灵表那样"四角恒定"。
        """
        index = self.frameIndex
        if index == self.last_frame:
            return
        self.last_frame = index
        self.applyFrame()
        if self.on_frame is not None:
            self.on_frame(self, index)

    def renderAnimation(self, dt: float, fps: float) -> None:
        """画当前帧：绑定图集贴图 → 写顶点（帧 / 尺寸 / 位置 / 角度变了才重写）→ 一次 draw。

        顶点直接是**世界坐标**（帧的局部矩形绕 ``(x, y, z)`` 转 ``r`` 后平移）——
        与 ``IAnimationFrame`` 一致，所以两者可以互换；摆位不走 ``RenderGlobal`` 的模型
        矩阵（那几个矩阵是**整体世界坐标系**的调节，对象不该拿它们摆自己）。
        """
        if not self.self_draw or not getattr(self, "active", True):
            return
        buffer = self.frame_render_buffer
        if buffer is None:
            return
        self.applyFrame()
        u0, v0, u1, v1 = self.frameUV()
        key = (u0, v0, u1, v1, self.frame_left, self.frame_right,
               self.frame_bottom, self.frame_top, float(self.x), float(self.y),
               float(self.z), float(self.r), self.frame_color)
        if key != self._drawn_key:
            builder = buffer.buffer_builder
            builder.clear()
            color = self.frame_color
            left, right = self.frame_left, self.frame_right
            bottom, top = self.frame_bottom, self.frame_top
            angle = math.radians(self.r)
            cos_a, sin_a = (math.cos(angle), math.sin(angle)) if angle else (1.0, 0.0)
            ox, oy, oz = float(self.x), float(self.y), float(self.z)
            # 下边取 v1、上边取 v0：与图集的原样行序（v=0 是图片最上面）对齐
            corners = ((left, bottom, u0, v1), (right, bottom, u1, v1),
                       (left, top, u0, v0), (right, bottom, u1, v1),
                       (right, top, u1, v0), (left, top, u0, v0))
            for px, py, u, v in corners:
                builder.pos(ox + px * cos_a - py * sin_a,
                            oy + px * sin_a + py * cos_a, oz
                            ).col(*color).tex(u, v).end()
            self._drawn_key = key
        self._ensureBufferFormat()          # 材质被换过的话，顶点格式要跟着换
        render_global = RenderGlobal.instance
        # 顶点已经是世界坐标：不碰那几个全局矩阵，只把光照 / 环境等的 uniform 同步过去
        render_global.updateGlobalUniforms()
        self.ensureTexture().bind(GL_TEXTURE0)
        self._applyMaterial(render_global.using_shader)
        buffer.setState(blend_mode=self.blend_mode)
        buffer.draw()            # 顶点没变时 build() 自己会跳过上传
        self.drawn_frames += 1

    def _applyMaterial(self, shader) -> None:
        """绑定法线 / 高光贴图并上传材质参数（没材质 / 没 shader 时一个 GL 调用都不发）。"""
        if shader is None or self.material is None:
            return
        if shader is not self._shader:
            # 换了 program（shader 重载等）：材质缓存作废，保证这一帧真的上传一遍
            self.material.invalidate()
            self._shader = shader
        self.material.apply(shader, rotation=self.r)

    def releaseAnimation(self) -> None:
        """释放 VBO/VAO（贴图由 ``TextureCache`` / 使用者自己管，和 ``Sprite2D`` 一致）。"""
        if self.frame_render_buffer is not None:
            self.frame_render_buffer.release()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        action = self.frame_sequence.name if self.frame_sequence else "-"
        return ("<IAtlasAnimation%s 动作=%r 帧 %d/%d %r uv=(%.3f, %.3f, %.3f, %.3f) "
                "at (%.2f, %.2f, %.2f) r=%g>"
                % (label, action, self.frameIndex, self.frameCount, self.frameName,
                   *self.frameUV(), self.x, self.y, self.z, self.r))
