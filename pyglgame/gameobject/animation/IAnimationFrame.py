"""帧动画（接口）：把一张精灵表按 ``行 × 列`` 切成帧，随时间切 UV。

用法一：**当世界里的对象**（接口写在左边，多继承）
----------------------------------------------

.. code-block:: python

    from pyglgame.gameobject.Sprite2D import Sprite2D
    from pyglgame.gameobject.animation import IAnimationFrame

    class Player(IAnimationFrame, Sprite2D):
        def __init__(self, sheet, x=0.0, y=0.0):
            # 动画参数自己吃，x / y 这类**宿主参数**原样透传给 Sprite2D
            super().__init__(rows=2, columns=4, frame_count=8,
                             resourceLocation=sheet, time=0.8, scale=(2.0, 2.0),
                             x=x, y=y)
            self.rebuild()

用法二：**当对象的零件**（持有）
------------------------------

::

    torch = IAnimationFrame(rows=1, columns=6, resourceLocation="res/img/1-n.png")
    torch.update(dt, tps)        # 时间轴推进
    torch.render(dt, fps)        # 画当前帧（不带宿主也能用）

用法三：**只选帧，让别人画**（组合 Sprite2D 的材质 / 光照）
-------------------------------------------------------

:meth:`IAnimationFrame.driveSprite` 把当前帧的 UV（和贴图）交给一个 ``Sprite2D``，
由精灵负责绘制 —— 这样动画可以白嫖精灵的材质 / 法线贴图 / 光影：

.. code-block:: python

    self.anim.setSelfDraw(False)       # 动画自己不画了
    ...
    self.anim.driveSprite(self)        # 精灵按动画的当前帧重建 UV

与旧 ``AnimationFrame`` 的差异（改了名字，**故意不重用**）
------------------------------------------------------

| 旧名字 | 新名字 | 为什么改 |
| --- | --- | --- |
| `texture` | `frame_texture` | 不抢 ``Sprite2D.texture``（组合时两者是不同东西） |
| `render_buffer` | `frame_render_buffer` | 不抢 ``Sprite2D.render_buffer`` |
| `width` / `height`（**像素**帧宽高） | `frame_width` / `frame_height` | 不抢 ``Sprite2D.width/height``（世界单位） |
| `l` / `r` / `t` / `b`（四角） | `frame_left` / `frame_right` / `frame_top` / `frame_bottom` | ``r`` / ``t`` 是 ``GameObject`` 的字段，复用会把宿主冲掉 |
| `time` / `max_time` | `animation_time` / `duration`（旧名保留为属性） | 与基类统一 |
| `uv`（加了又删） | `frameUV()` / `spriteUV()` | 属性会阴影掉 ``Sprite2D.uv``，坚决不加 |

``setFlipX`` / ``setFlipY`` / ``reset()`` / ``vertical_first`` / ``scale`` /
``rows`` / ``columns`` / ``frame_count`` 语义完全不变，老代码照旧能用。

几何与变换
---------

- 顶点是**以原点为中心的本地坐标**，位置 / 旋转由**宿主的模型矩阵**提供
  （``x`` / ``y`` / ``z`` / ``r``）—— 旧实现把 ``x``/``y`` 直接烘进顶点，
  结果会被上一个物体残留的 rotate/scale 带跑；
- ``scale`` 是"每帧的显示尺寸倍数"（顶点尺寸 = 帧像素数 × ``scale``）；
- 构造**不碰 GL**：贴图与 VBO 都推迟到第一次绘制（``Image`` 只读盘解析，
  所以帧宽高在构造时就准）。
"""

import math

import glm
from OpenGL.GL import GL_TEXTURE0, GL_TRIANGLES

from ...render.BufferBuilder import COL, POS, TEX
from ...render.Image import Image
from ...render.RenderBuffer import RenderBuffer
from ...render.Texture import Texture
from ...RenderGlobal import RenderGlobal
from .IAnimationBase import IAnimationBase


class IAnimationFrame(IAnimationBase):
    """精灵表帧动画（见模块文档）。"""

    def __init__(self, rows: int = 1, columns: int = 1,
                 frame_count: int = None, resourceLocation=None,
                 time: float = -1.0, scale=(1.0, 1.0),
                 vertical_first: bool = False, *,
                 frame_texture=None, image=None, size=None,
                 color=(1.0, 1.0, 1.0, 1.0), blend_mode: str = None,
                 speed: float = 1.0, loop: bool = True, autoplay: bool = True,
                 self_draw: bool = True, name: str = None, **kwargs) -> None:
        """
        :param rows: 精灵表行数
        :param columns: 精灵表列数
        :param frame_count: 实际帧数（默认 ``rows × columns``，上限也是它）
        :param resourceLocation: 精灵表资源（``ResourceLocation`` 或路径字符串）
        :param time: **一轮播放的秒数**；``<=0`` 时按旧约定 = ``frame_count`` 秒
                     （即 1 秒 1 帧）
        :param scale: 每帧的显示尺寸倍数 ``(sx, sy)``
        :param vertical_first: 逐列排帧（引擎旧参数，保留原语义）
        :param frame_texture: 现成的**帧贴图**（给了就不去上传 ``image`` / 资源）
        :param image: 现成的 :class:`~pyglgame.render.Image.Image`（内存里的精灵表）
        :param size: 精灵表**总**像素尺寸 ``(w, h)``（没素材时给出即可算帧宽高）
        :param color: 顶点色（整体染色 / 透明度）
        :param blend_mode: 批次混合模式（``"alpha"`` / ``"add"`` …，见 ``RenderState``）
        :param speed: 播放速度倍数（负值 = 倒放）
        :param loop: 是否循环
        :param autoplay: 建好是否立即播放（旧行为）
        :param self_draw: ``False`` = 动画自己**不画**，只维护当前帧的 UV
                          （配合 :meth:`driveSprite` 让宿主来画）
        :param name: 调试用名字
        :param kwargs: **其余关键字参数原样透传给宿主**，例如组合 ``Sprite2D`` 时写
                       ``super().__init__(rows=..., x=1.0, width=2.0)`` —— ``x`` /
                       ``width`` / ``material`` / ``transparent`` 都会落到精灵那边
                       （``texture`` 也是：那是**精灵的**贴图槽；动画自己的叫
                       ``frame_texture``，见模块文档）
        """
        super().__init__(**kwargs)
        self.name = name
        self.rows = max(1, int(rows))
        self.columns = max(1, int(columns))
        self.vertical_first = bool(vertical_first)
        self.image = image if image is not None else self._loadImage(resourceLocation)
        sheet_width, sheet_height = self._resolveSheetSize(size)
        self.frame_width = sheet_width / self.columns
        self.frame_height = sheet_height / self.rows
        self.s_columns = 1.0 / self.columns
        self.s_rows = 1.0 / self.rows
        total = self.rows * self.columns
        count = total if frame_count is None else min(int(frame_count), total)
        self.frame_count = max(1, count)
        self.frame_texture = frame_texture
        self.frame_render_buffer = RenderBuffer()
        self.frame_render_buffer.createBuffer(GL_TRIANGLES, POS | COL | TEX)
        self.color = tuple(float(v) for v in color)
        self.blend_mode = blend_mode
        self.self_draw = bool(self_draw)
        self.scale = (float(scale[0]), float(scale[1]))
        self.flip_x = False
        self.flip_y = False
        # 旧约定：time <= 0 → 整轮 frame_count 秒
        self.duration = float(self.frame_count) if float(time) <= 0.0 else float(time)
        self.animation_speed = float(speed)
        self.animation_loop = bool(loop)
        self.playing = bool(autoplay)
        #: 帧切换回调（``None`` 或 ``callable(动画对象, 帧号)``）
        self.on_frame = None
        self.last_frame = -1
        #: 累计画了多少帧（调试 / 测试用）
        self.drawn_frames = 0
        self._drawn_key = None
        self.applyScale()
        self.refreshAnimation()

    # === 构造辅助 ===

    @staticmethod
    def _loadImage(resourceLocation):
        """``ResourceLocation`` / 路径字符串 → :class:`~pyglgame.render.Image.Image`。"""
        if resourceLocation is None:
            return None
        if isinstance(resourceLocation, str):
            from ...ResourceLocation import ResourceLocation
            resourceLocation = ResourceLocation(resourceLocation)
        return Image(resourceLocation)

    def _resolveSheetSize(self, size) -> tuple:
        """精灵表总尺寸：显式 ``size`` > ``image`` 的像素尺寸 > 每帧 1×1 世界单位。"""
        if size is not None:
            return (float(size[0]), float(size[1]))
        if self.image is not None:
            return (float(self.image.width), float(self.image.height))
        return (float(self.columns), float(self.rows))

    # === 尺寸 / 贴图 ===

    def applyScale(self) -> "IAnimationFrame":
        """按 ``frame_width/height × scale`` 重算四角（顶点是本地坐标，中心在原点）。

        ⚠ 四角叫 ``frame_left/right/top/bottom`` 而不是 ``l/r/t/b``：``r`` 和
        ``t`` 是 ``GameObject`` 的字段（旋转 / 另一个欧拉角），复用它们会在组合时
        把宿主的值冲掉（踩过：frame 动画把宿主的 ``r`` 写成了边界值）。
        """
        half_w = self.frame_width * 0.5 * self.scale[0]
        half_h = self.frame_height * 0.5 * self.scale[1]
        self.frame_left, self.frame_right = -half_w, half_w
        self.frame_bottom, self.frame_top = -half_h, half_h
        return self

    def setScale(self, scale) -> "IAnimationFrame":
        """换每帧的显示尺寸倍数（会重新算四角）。"""
        self.scale = (float(scale[0]), float(scale[1]))
        return self.applyScale()

    def ensureTexture(self):
        """拿到要用的贴图：现成贴图 > ``image`` 上传 > 1×1 白纹理。

        ``image`` 的上传是**懒的**（只在第一次真的绘制时发生），所以
        ``IAnimationFrame(...)`` 可以写在模块顶层 / 单测里（那时 GL 上下文还没起来）。
        """
        if self.frame_texture is None:
            if self.image is not None:
                self.frame_texture = self.image.getTexture()
            else:
                self.frame_texture = Texture.white()
        return self.frame_texture

    def setTexture(self, texture) -> "IAnimationFrame":
        """换贴图（``None`` = 回到"按 ``image`` 懒上传"）。"""
        self.frame_texture = texture
        return self

    def setImage(self, image) -> "IAnimationFrame":
        """换精灵表（``Image`` 或路径）；帧宽高与贴图跟着重算。"""
        if isinstance(image, str):
            from ...ResourceLocation import ResourceLocation
            image = Image(ResourceLocation(image))
        self.image = image
        width, height = self._resolveSheetSize(None)
        self.frame_width = width / self.columns
        self.frame_height = height / self.rows
        self.frame_texture = None
        self._drawn_key = None
        return self.applyScale()

    def setFlipX(self, flip: bool) -> "IAnimationFrame":
        """水平翻转（只翻 UV，顶点不动）。"""
        self.flip_x = bool(flip)
        return self

    def setFlipY(self, flip: bool) -> "IAnimationFrame":
        """垂直翻转（只翻 UV，顶点不动）。"""
        self.flip_y = bool(flip)
        return self

    def setSelfDraw(self, active: bool = True) -> "IAnimationFrame":
        """开关"动画自己画"（关掉之后配合 :meth:`driveSprite` 让宿主来画）。"""
        self.self_draw = bool(active)
        return self

    # === 帧 / UV ===

    @property
    def frame_time(self) -> float:
        """单帧时长（秒）。"""
        return float(self.duration) / float(self.frame_count)

    @property
    def frameIndex(self) -> int:
        """当前帧号（0 起，对 ``frame_count`` 取模）。"""
        if self.frame_count <= 0 or float(self.duration) <= 0.0:
            return 0
        index = int(float(self.animation_time) / self.frame_time)
        return index % self.frame_count

    def setFrame(self, index: int) -> "IAnimationFrame":
        """直接跳到某一帧（会随之移动时间轴）。"""
        index = int(index) % self.frame_count
        return self.seek(index * self.frame_time)

    def frameUV(self) -> tuple:
        """当前帧在图集里的 ``(u0, v0, u1, v1)``（含翻转 / ``vertical_first``）。"""
        index = self.frameIndex
        if self.vertical_first:
            row = index % self.rows
            col = index // self.rows
        else:
            col = index % self.columns
            row = index // self.columns
        u0 = col * self.s_columns
        u1 = u0 + self.s_columns
        v0 = row * self.s_rows
        v1 = v0 + self.s_rows
        if self.flip_x:
            u0, u1 = u1, u0
        if self.flip_y:
            v0, v1 = v1, v0
        return (u0, v0, u1, v1)

    # === 钩子 ===

    def updateAnimation(self, dt: float) -> None:
        """帧号变了就回调 ``on_frame(动画对象, 帧号)``（脚步 / 挥砍判定挂这里）。"""
        index = self.frameIndex
        if index == self.last_frame:
            return
        self.last_frame = index
        if self.on_frame is not None:
            self.on_frame(self, index)

    def spriteUV(self, flipped: bool = False) -> tuple:
        """给 ``Sprite2D.uv`` 用的 UV（:meth:`driveSprite` 内部用它）。

        两个约定不一样，必须换一次：

        - :meth:`frameUV`：``v0`` = 帧的**上**边（与 ``Image`` 原样行序一致，
          自己画的时候"四边形下边 ↔ ``v1``"）；
        - ``Sprite2D.uv``：第一个 ``v`` 对应四边形**下**边。

        :param flipped: 精灵那边用的是不是 ``FLIP_TOP_BOTTOM`` 翻过的图集
                        （``Sprite2D`` 的推荐做法）。

                        - ``False``（``Image`` 原样）：``(u0, v1, u1, v0)``
                        - ``True``（翻过了）：``(u0, 1-v1, u1, 1-v0)``
        """
        u0, v0, u1, v1 = self.frameUV()
        if flipped:
            return (u0, 1.0 - v1, u1, 1.0 - v0)
        return (u0, v1, u1, v0)

    def driveSprite(self, sprite, rebuild: bool = True,
                    texture=None, flipped: bool = False) -> "IAnimationFrame":
        """把当前帧的 UV 交给一个 ``Sprite2D``（让**精灵**去画，动画只负责选帧）。

        :param sprite: 有 ``uv`` / ``rebuild`` 的对象（``Sprite2D``）
        :param rebuild: 写完 UV 是否顺手 ``rebuild()``（尺寸没变时可以不建）
        :param texture: 要额外塞给精灵的贴图（``None`` = 只在"精灵不是自己"且动画持有
                        贴图时才同步）
        :param flipped: 见 :meth:`spriteUV`

        ⚠ ``sprite is self``（多继承用法：动画和精灵是同一个对象）时**不去碰贴图** ——
        那时 ``self.setTexture`` 会解析到动画自己那个 ``frame_texture`` 槽，
        精灵的贴图由它自己（``sprite.texture``）管。
        """
        if texture is not None:
            sprite.setTexture(texture)
        elif self.frame_texture is not None and sprite is not self:
            sprite.setTexture(self.frame_texture)
        sprite.uv = self.spriteUV(flipped)
        if rebuild:
            sprite.rebuild()
        return self

    # === 绘制 ===

    def renderAnimation(self, dt: float, fps: float) -> None:
        """画当前帧：绑定贴图 → 写顶点（帧号 / 位置 / 角度变了才重写）→ 一次 draw call。

        顶点直接是**世界坐标**（帧的局部矩形绕 ``(x, y, z)`` 转 ``r`` 后平移）——
        摆位不走 ``RenderGlobal`` 的模型矩阵：那几个矩阵是**整体世界坐标系**的调节，
        对象不该拿它们摆自己，也不该覆盖它们。没有变化时**不重传顶点**。
        """
        if not self.self_draw or not getattr(self, "active", True):
            return
        buffer = self.frame_render_buffer
        if buffer is None:
            return
        u0, v0, u1, v1 = self.frameUV()
        key = (u0, v0, u1, v1, self.frame_left, self.frame_right,
               self.frame_bottom, self.frame_top, float(self.x), float(self.y),
               float(self.z), float(self.r), self.color)
        if key != self._drawn_key:
            builder = buffer.buffer_builder
            builder.clear()
            color = self.color
            left, right = self.frame_left, self.frame_right
            bottom, top = self.frame_bottom, self.frame_top
            angle = math.radians(self.r)
            cos_a, sin_a = (math.cos(angle), math.sin(angle)) if angle else (1.0, 0.0)
            ox, oy, oz = float(self.x), float(self.y), float(self.z)
            # 下边取 v1、上边取 v0：与 Image 的原样行序（v=0 是图的最上面）对齐
            corners = ((left, bottom, u0, v1), (right, bottom, u1, v1),
                       (left, top, u0, v0), (right, bottom, u1, v1),
                       (right, top, u1, v0), (left, top, u0, v0))
            for px, py, u, v in corners:
                builder.pos(ox + px * cos_a - py * sin_a,
                            oy + px * sin_a + py * cos_a, oz
                            ).col(*color).tex(u, v).end()
            self._drawn_key = key
        render_global = RenderGlobal.instance
        # 顶点已经是世界坐标：不碰那几个全局矩阵，只把光照 / 环境等的 uniform 同步过去
        render_global.updateGlobalUniforms()
        self.ensureTexture().bind(GL_TEXTURE0)
        buffer.setState(blend_mode=self.blend_mode)
        buffer.draw()            # 顶点没变时 build() 会自己跳过上传
        self.drawn_frames += 1

    def releaseAnimation(self) -> None:
        """释放 VBO/VAO（贴图由 ``TextureCache`` / 使用者自己管，和 ``Sprite2D`` 一致）。"""
        if self.frame_render_buffer is not None:
            self.frame_render_buffer.release()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return ("<IAnimationFrame%s %d/%d 帧 uv=(%.3f, %.3f, %.3f, %.3f) "
                "at (%.2f, %.2f, %.2f) r=%g>" % (
                    label, self.frameIndex, self.frame_count,
                    *self.frameUV(), self.x, self.y, self.z, self.r))


#: 旧名字（``pyglgame.gameobject.animation.AnimationFrame.AnimationFrame`` 继续可用）
AnimationFrame = IAnimationFrame
