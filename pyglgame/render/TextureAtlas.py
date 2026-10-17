"""图集（Texture Atlas）：一张 PNG + 一堆**任意矩形**帧。

为什么不复用 ``IAnimationFrame``
--------------------------------

:class:`~pyglgame.gameobject.animation.IAnimationFrame.IAnimationFrame` 把一张精灵表按
``rows × columns`` **等分**切开 —— 那是"自己画的均匀网格"。而打包器（LibreSprite /
Aseprite / TexturePacker）给出的是**每帧自己的矩形**，还可能把帧 trim 过。本模块就是
那个"任意矩形"的数据层 + **唯一一处 UV 换算**。

坐标与方向的约定（最容易出错的地方，所以写死在这里）
--------------------------------------------------

1. **文档里的 ``rect``**：左上原点、**Y 向下**（打包器的原样输出）；
2. **引擎纹理坐标**：``glTexImage2D`` 收到的第一行数据对应 ``v = 0`` —— 也就是
   **``v = 0`` 是图片顶部**，所以 ``v0 = rect.y / H``（小的 ``v`` 在**上**）；
3. :meth:`TextureAtlas.frameUV` 返回 ``(u0, v0, u1, v1)`` 且 **``v0`` = 帧的顶边**，
   与 ``IAnimationFrame.frameUV()`` 的语义**完全一致**（自己画时"四边形下边 ↔ v1"）；
4. 给 ``Sprite2D.uv`` 用的换算是 :meth:`TextureAtlas.spriteUV`（与
   ``IAnimationFrame.spriteUV`` 同一套两种翻转约定）；
5. **世界坐标 Y 向上**：帧的轴心（``pivot``，像素、相对帧左上角、Y 向下）把矩形摆好，
   :meth:`TextureAtlas.frameBounds` 直接给出 ``(left, right, bottom, top)``。

trim 还原（不还原 → 动作抖动）
------------------------------

打包器把帧的透明边裁掉后，``rect`` 变小了，必须在**原帧尺寸**（``source_size``）里
把内容块摆回 ``source_rect``（= ``spriteSourceSize``）的位置。:meth:`frameBounds` 就是
干这个的：``左上角在 (sx, sy)`` × ``轴心在 (px, py)`` → 世界矩形。
**所以只要走 frameBounds，trim 与不 trim 的代码路径是同一条。**

用法
----

.. code-block:: python

    from pyglgame.importers import aseprite
    from pyglgame.render.TextureAtlas import TextureAtlas

    atlas = TextureAtlas.fromAseprite("res/atlas/hero.json")     # 一步到位
    atlas = TextureAtlas.fromDict(aseprite.fromFile("res/atlas/hero.json"))
    atlas = TextureAtlas(                                # 也可以手写
        image="res/atlas/hero.png", image_size=(128, 32),
        frames={"idle_0": {"rect": [0, 0, 32, 32]}},
        pixels_per_unit=32.0)

    atlas.frameUV("idle_0")         # (u0, v0, u1, v1)，v0 = 顶边
    atlas.frameBounds("idle_0")     # (left, right, bottom, top)，世界单位
    atlas.frameCorners("idle_0")    # 6 个 (x, y, u, v)，直接喂 BufferBuilder
    atlas.texture()                 # 懒上传（第一次调用才碰 GL）

法线 / 高光贴图的**自动配对**
-----------------------------

构造时会在颜色图集旁边找 ``<名字>_normal`` / ``<名字>_n``（法线）与 ``<名字>_lightmap`` /
``<名字>_l``（高光）—— 见 :mod:`pyglgame.render.MaterialMaps`::

    atlas = TextureAtlas.fromAseprite("res/atlas/chr.json")   # 颜色 chr.png
    atlas.normal_map_path      # → res/atlas/chr_normal.png（旁边真有才认）
    atlas.specular_map_path    # → res/atlas/chr_specular.png ← 后缀不在表里，所以是 ""

    material = atlas.material(shininess=48.0)   # Material2D（贴图是懒上传的）
    hero = Hero(..., material=material)         # 或者干脆不传：IAtlasAnimation 会自己用

显式传 ``normal_map=`` / ``specular_map=`` 优先；``auto_material=False`` 关掉查盘。

本模块**构造与 UV 计算完全不碰 GL**（贴图是懒上传的），所以可以写在模块顶层、也能单测。
"""

from OpenGL.GL import GL_CLAMP_TO_EDGE, GL_REPEAT

from ..resources.Resources import Resources
from . import MaterialMaps
from .Texture import LINEAR, NEAREST, Texture

#: 引擎图集文档的格式版本（``fromDict`` 只认这个）
FORMAT_VERSION = 1

#: 默认"1 世界单位 = 多少像素"
DEFAULT_PPU = 32.0


def resolveImagePath(base_dir, image) -> str:
    """把图集文档里的 ``image`` 解析成**真能读到的那条路径**。

    文档里的 ``image`` 有两种常见写法：

    1. **相对图集 JSON 目录**（``"chr.png"``）—— LibreSprite 的原始 ``meta.image``
       就是这样，要用 ``base_dir`` 拼一下；
    2. **已经解析过的资源路径**（``"res/atlas/chr.png"``）—— 导入器
       （``aseprite.fromFile``）与 :meth:`~pyglgame.scene.SceneDocument.SceneDocument.buildAtlas`
       交出来的就是这种，**不能再拼** ``base_dir``，否则会得到
       ``res/atlas/res/atlas/chr.png`` 这种重复路径（直到绘制时加载贴图才炸）。

    所以这里按"哪个真的存在"来挑：先试 ``base_dir/image``，再试 ``image`` 原样；
    都不存在时，带挂载前缀（``res/``）的原样返回（报错信息才和用户写的一致），
    其余仍回退到 ``base_dir/image``（保持"相对路径"的旧语义）。
    """
    image = str(image)
    if not base_dir or Resources.isRealPath(image):
        return image
    joined = Resources.joinPath(base_dir, image)
    for candidate in (joined, image):
        if Resources.exists(candidate) and not Resources.isDir(candidate):
            return candidate
    if Resources.isLogicalPath(image, Resources.DEFAULT_PREFIX):
        return image
    return joined


class TextureAtlas:
    """一张图集贴图 + 若干帧矩形（见模块文档）。"""

    def __init__(self, image=None, image_size=None, frames=None, animations=None,
                 pixels_per_unit: float = DEFAULT_PPU, order=None, name: str = None,
                 filter: int = NEAREST, mipmap: bool = False,
                 wrap_mode: int = GL_CLAMP_TO_EDGE, source: str = None,
                 normal_map=None, specular_map=None, auto_material: bool = True) -> None:
        #: 图集 PNG 的路径（相对 ``res/``；``None`` = 用现成贴图，不读盘）
        self.image_path = image
        #: ``(w, h)`` 像素；``(0, 0)`` 时 :meth:`ensureImageSize` 会去读图片
        self.image_size = tuple(int(v) for v in (image_size or (0, 0)))
        #: 帧名 → 帧描述（``rect`` / ``source_size`` / ``source_rect`` / ``pivot`` / ``duration_ms``）
        self.frames: dict = {str(k): dict(v or {}) for k, v in (frames or {}).items()}
        #: 帧的**顺序**（动画按索引引用帧时用；缺省 = ``frames`` 的插入顺序）
        self.order: list = list(order) if order else list(self.frames.keys())
        #: 动作：名字 → ``{frames, fps, loop, direction}``
        self.animations: dict = {str(k): dict(v or {})
                                 for k, v in (animations or {}).items()}
        #: 1 世界单位 = 多少像素
        self.pixels_per_unit = float(pixels_per_unit) or DEFAULT_PPU
        self.name = name
        #: 来源标注（``"libresprite/aseprite"`` / 手写 …），只用于排查
        self.source = source
        #: 采样参数（图集必须 clamp，否则相邻帧会串色）
        self.filter = filter
        self.mipmap = bool(mipmap)
        self.wrap_mode = wrap_mode
        #: 法线贴图路径：显式给的优先，否则在 ``image_path`` 旁边自动找（见 MaterialMaps）
        self.normal_map_path = normal_map or None
        #: 高光贴图路径（同上）
        self.specular_map_path = specular_map or None
        if auto_material and self.image_path:
            found = MaterialMaps.materialMapsFor(self.image_path)
            self.normal_map_path = self.normal_map_path or found.get("normal_map")
            self.specular_map_path = self.specular_map_path or found.get("specular_map")
        self._texture = None
        self._image = None
        for name_ in self.order:
            if name_ not in self.frames:
                raise ValueError("order 里的帧不存在: %r" % (name_,))

    # === 基本读数 ===

    def __len__(self) -> int:
        return len(self.frames)

    def __contains__(self, name) -> bool:
        return str(name) in self.frames

    def __iter__(self):
        return iter(self.order)

    @property
    def names(self) -> list:
        """帧名列表（按 ``order``）。"""
        return list(self.order)

    @property
    def animationNames(self) -> list:
        return list(self.animations.keys())

    def hasFrame(self, name) -> bool:
        return str(name) in self.frames

    def frameIndexOf(self, name) -> int:
        """帧名 → 顺序索引（找不到抛 ``KeyError``）。"""
        return self.order.index(str(name))

    def frameNameAt(self, index: int) -> str:
        """顺序索引 → 帧名（取模，负数也认）。"""
        if not self.order:
            raise IndexError("图集里没有任何帧")
        return self.order[int(index) % len(self.order)]

    def frame(self, name) -> dict:
        """帧描述 dict（内部引用，不要改）。"""
        key = str(name)
        if key not in self.frames:
            raise KeyError("图集里没有这一帧: %r（有 %d 帧）" % (name, len(self.frames)))
        return self.frames[key]

    # === 帧几何（世界单位）===

    def frameRect(self, name) -> tuple:
        """帧在图集里的像素矩形 ``(x, y, w, h)``（左上原点、Y 向下）。"""
        rect = self.frame(name).get("rect") or (0, 0, 0, 0)
        return (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))

    def frameSourceSize(self, name) -> tuple:
        """帧的**原帧**像素尺寸 ``(W, H)``（trim 之前；缺字段用 ``rect`` 兜底）。"""
        size = self.frame(name).get("source_size")
        if not size:
            rect = self.frameRect(name)
            return (rect[2], rect[3])
        return (int(size[0]), int(size[1]))

    def frameSourceRect(self, name) -> tuple:
        """**内容块**在原帧里的像素矩形 ``(sx, sy, sw, sh)``；缺省 = 整帧（没 trim）。"""
        rect = self.frame(name).get("source_rect")
        if not rect:
            width, height = self.frameSourceSize(name)
            return (0, 0, width, height)
        return (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))

    def framePivot(self, name) -> tuple:
        """轴心 ``(px, py)``（像素，相对帧左上角、Y 向下）；缺省 = 脚底中心。"""
        pivot = self.frame(name).get("pivot")
        if not pivot:
            width, height = self.frameSourceSize(name)
            return (width * 0.5, float(height))
        return (float(pivot[0]), float(pivot[1]))

    def frameDuration(self, name) -> float:
        """帧时长（**秒**；文档里存的是毫秒）。"""
        try:
            return max(float(self.frame(name).get("duration_ms", 100.0)), 0.0) / 1000.0
        except (TypeError, ValueError):
            return 0.1

    def frameSize(self, name, scale=(1.0, 1.0)) -> tuple:
        """**整帧**的世界尺寸 ``(w, h)``（trim 之前，按 ``pixels_per_unit`` 换算）。"""
        width, height = self.frameSourceSize(name)
        return (width / self.pixels_per_unit * float(scale[0]),
                height / self.pixels_per_unit * float(scale[1]))

    def frameBounds(self, name, scale=(1.0, 1.0)) -> tuple:
        """**内容块**的世界矩形 ``(left, right, bottom, top)``（trim 已还原、pivot 已对齐）。

        推导（帧坐标 = 左上原点、Y 向下；世界 = 左下原点、Y 向上）：

        .. code-block:: text

            X = (fx - px) / PPU
            Y = (py - fy) / PPU          ← 两边的"向下"相消，得到向上的世界 Y

        内容块 ``fx ∈ [sx, sx+sw]``、``fy ∈ [sy, sy+sh]`` 代进去就是下面四行。
        不 trim（``source_rect == 整帧``）时它退化成"整帧按 pivot 对齐" —— **同一条路径**。
        """
        sx, sy, sw, sh = self.frameSourceRect(name)
        px, py = self.framePivot(name)
        ppu = self.pixels_per_unit
        scale_x, scale_y = float(scale[0]), float(scale[1])
        left = (sx - px) / ppu * scale_x
        right = (sx + sw - px) / ppu * scale_x
        top = (py - sy) / ppu * scale_y
        bottom = (py - sy - sh) / ppu * scale_y
        return (left, right, bottom, top)

    def frameFullBounds(self, name, scale=(1.0, 1.0)) -> tuple:
        """**整帧**的世界矩形 ``(left, right, bottom, top)``（含被裁掉的透明边）。

        调试对齐用（例如给"这一帧的站位"画个框）：它与 :meth:`frameBounds` 的差别就是
        trim 掉的那些边。
        """
        width, height = self.frameSourceSize(name)
        px, py = self.framePivot(name)
        ppu = self.pixels_per_unit
        scale_x, scale_y = float(scale[0]), float(scale[1])
        return (-px / ppu * scale_x, (width - px) / ppu * scale_x,
                (py - height) / ppu * scale_y, py / ppu * scale_y)

    # === UV（唯一的换算处）===

    def ensureImageSize(self) -> tuple:
        """确保知道图集像素尺寸：显式 ``image_size`` > 读图片（``PIL``，不碰 GL）。"""
        if self.image_size[0] > 0 and self.image_size[1] > 0:
            return self.image_size
        image = self.image()
        if image is not None:
            self.image_size = (int(image.width), int(image.height))
        return self.image_size

    def image(self):
        """懒读盘：返回 :class:`~pyglgame.render.Image.Image`（没有路径时 ``None``）。

        只解码、**不碰 GL**（``Image`` 的构造就是 PIL 打开 + 转 numpy）。
        """
        if self._image is None and self.image_path:
            from ..ResourceLocation import ResourceLocation
            from .Image import Image
            self._image = Image(ResourceLocation(self.image_path))
        return self._image

    def frameUV(self, name) -> tuple:
        """帧的 ``(u0, v0, u1, v1)``：**``v0`` = 帧的顶边**（与 ``IAnimationFrame`` 一致）。"""
        width, height = self.ensureImageSize()
        x, y, rect_w, rect_h = self.frameRect(name)
        if width <= 0 or height <= 0:
            return (0.0, 0.0, 1.0, 1.0)
        return (x / width, y / height, (x + rect_w) / width, (y + rect_h) / height)

    def spriteUV(self, name, flipped: bool = False) -> tuple:
        """给 ``Sprite2D.uv`` 用的 UV（第一个 ``v`` 对应四边形**下**边）。

        :param flipped: 精灵那边用的是不是 ``FLIP_TOP_BOTTOM`` 翻过的图集
            （``Sprite2D`` 的推荐做法）。语义与 ``IAnimationFrame.spriteUV`` 完全一致：
            ``False`` → ``(u0, v1, u1, v0)``；``True`` → ``(u0, 1-v1, u1, 1-v0)``。
        """
        u0, v0, u1, v1 = self.frameUV(name)
        if flipped:
            return (u0, 1.0 - v1, u1, 1.0 - v0)
        return (u0, v1, u1, v0)

    def frameCorners(self, name, scale=(1.0, 1.0)) -> tuple:
        """帧的 6 个 ``(x, y, u, v)`` 顶点（两个三角形），顺序与 ``IAnimationFrame`` 一致。

        顶点是**本地坐标**（轴心在原点）：下边取 ``v1``、上边取 ``v0``，所以直接
        ``BufferBuilder`` 按顺序写就能画对，不需要在别处再翻一次。
        """
        left, right, bottom, top = self.frameBounds(name, scale)
        u0, v0, u1, v1 = self.frameUV(name)
        return ((left, bottom, u0, v1), (right, bottom, u1, v1),
                (left, top, u0, v0), (right, bottom, u1, v1),
                (right, top, u1, v0), (left, top, u0, v0))

    # === 动作 ===

    def frameSequence(self, names, fps: float = None, loop: bool = True,
                      name: str = None, direction: str = "forward"):
        """把一串帧名现做成 :class:`~pyglgame.gameobject.animation.FrameSequence`。"""
        from ..gameobject.animation.FrameSequence import FrameSequence
        names = [str(item) for item in names]
        for item in names:
            if item not in self.frames:
                raise KeyError("动作 %r 引用了图集里没有的帧: %r" % (name, item))
        return FrameSequence(name=name, frames=names,
                             fps=10.0 if fps is None else float(fps),
                             loop=bool(loop), direction=direction)

    def animation(self, name="default"):
        """取一个动作（返回 ``FrameSequence``）；名字不存在时抛 ``KeyError``。"""
        key = str(name)
        data = self.animations.get(key)
        if data is None:
            raise KeyError("图集里没有这个动作: %r（有 %r）" % (name, self.animationNames))
        return self.frameSequence(data.get("frames") or (), fps=data.get("fps"),
                                  loop=data.get("loop", True), name=key,
                                  direction=data.get("direction", "forward"))

    def tryAnimation(self, name="default"):
        """:meth:`animation` 的宽容版：没有这个动作时返回 ``None``。"""
        try:
            return self.animation(name)
        except KeyError:
            return None

    # === 贴图（懒上传，第一次调用才碰 GL）===

    def texture(self) -> "Texture":
        """图集贴图；第一次调用才上传（同一文件走 ``TextureCache``，不会重复上传）。

        没有 ``image`` 时返回 1×1 白纹理（与 ``Sprite2D`` 的行为一致：宁可采样一片白，
        也不要采样到上一趟 pass 的残留纹理）。
        """
        if self._texture is None:
            if not self.image_path:
                self._texture = Texture.white()
            else:
                from .TextureCache import loadTexture
                self._texture = loadTexture(self.image_path, filter=self.filter,
                                            mipmap=self.mipmap,
                                            wrap_mode=self.wrap_mode)
        return self._texture

    def setTexture(self, texture) -> "TextureAtlas":
        """直接给一张现成贴图（就不会再按路径上传；``None`` = 回到懒加载）。"""
        self._texture = texture
        return self

    def release(self) -> None:
        """丢掉自己持有的引用（**不**释放缓存里的纹理 —— 那是 ``TextureCache`` 的活）。"""
        self._texture = None
        self._image = None

    # === 材质（法线 / 高光贴图的自动配对）===

    @property
    def hasMaterialMaps(self) -> bool:
        """配到了法线或高光贴图（显式给的也算）。"""
        return bool(self.normal_map_path or self.specular_map_path)

    def materialMaps(self) -> dict:
        """``{"normal_map": 路径, "specular_map": 路径}``（**只含真有的键**）。"""
        found = {}
        if self.normal_map_path:
            found["normal_map"] = self.normal_map_path
        if self.specular_map_path:
            found["specular_map"] = self.specular_map_path
        return found

    def material(self, **params):
        """按配到的法线 / 高光贴图建一个 :class:`~pyglgame.render.Lighting2D.Material2D`。

        - **一张都没有时返回 ``None``**（调用方自己决定要不要建一个空材质）；
        - 贴图是 :class:`~pyglgame.render.LazyTexture.LazyTexture`（第一次绘制才上传，
          所以这行代码可以写在模块顶层 / 没有 GL 上下文的地方）；
        - 每次调用都**新建**一个材质：材质是可变的状态袋（``shininess`` / ``strength``
          都写在它身上），两个对象共用一份会互相影响；底层贴图走 ``TextureCache``，
          不会重复上传。

        :param params: 原样转给 ``Material2D``（``shininess`` / ``normal_strength`` /
            ``specular_mode`` …）
        """
        return MaterialMaps.materialFor(normal_map=self.normal_map_path,
                                        specular_map=self.specular_map_path,
                                        auto=False, **params)

    # === 序列化（引擎自有格式）===

    def toDict(self) -> dict:
        """导出成可写进 JSON 的 dict（与 :meth:`fromDict` 往返一致）。"""
        data = {
            "format_version": FORMAT_VERSION,
            "image": self.image_path or "",
            "image_size": [int(self.image_size[0]), int(self.image_size[1])],
            "pixels_per_unit": float(self.pixels_per_unit),
            "order": list(self.order),
            "frames": {name: dict(self.frames[name]) for name in self.frames},
            "animations": {key: dict(value) for key, value in self.animations.items()},
        }
        if self.name:
            data["name"] = self.name
        if self.source:
            data["source"] = self.source
        if self.filter != NEAREST:
            data["filter"] = "linear"
        if self.mipmap:
            data["mipmap"] = True
        # 法线 / 高光贴图：配到了就一起存下来（``fromDict`` 那边认，往返一致）
        if self.normal_map_path:
            data["normal_map"] = self.normal_map_path
        if self.specular_map_path:
            data["specular_map"] = self.specular_map_path
        return data

    @classmethod
    def fromDict(cls, data: dict, base_dir: str = None,
                 auto_material: bool = True) -> "TextureAtlas":
        """从引擎图集文档建图集（``format_version`` 比引擎新时直接报错，不猜）。

        :param auto_material: 文档里没写 ``normal_map`` / ``specular_map`` 时，是否在
            图片旁边自动找（见 :mod:`pyglgame.render.MaterialMaps`）
        """
        if not isinstance(data, dict):
            raise ValueError("图集文档必须是 dict，收到 %r" % (type(data).__name__,))
        version = int(data.get("format_version", FORMAT_VERSION) or FORMAT_VERSION)
        if version > FORMAT_VERSION:
            raise ValueError("图集文档版本 %d 比引擎支持的 %d 新，请更新引擎或降版本导出"
                             % (version, FORMAT_VERSION))
        image = data.get("image") or None
        if image and base_dir:
            # 注意：image 可能已经是解析过的资源路径（``res/atlas/chr.png``），
            # 直接 joinPath 会拼成 ``res/atlas/res/atlas/chr.png`` —— 见 resolveImagePath
            image = resolveImagePath(base_dir, image)
        filter_name = str(data.get("filter", "nearest")).lower()
        return cls(image=image,
                   image_size=data.get("image_size"),
                   frames=data.get("frames"),
                   animations=data.get("animations"),
                   pixels_per_unit=float(data.get("pixels_per_unit", DEFAULT_PPU)),
                   order=data.get("order"),
                   name=data.get("name"),
                   filter=LINEAR if filter_name == "linear" else NEAREST,
                   mipmap=bool(data.get("mipmap", False)),
                   source=data.get("source"),
                   normal_map=data.get("normal_map"),
                   specular_map=data.get("specular_map"),
                   auto_material=auto_material)

    @classmethod
    def fromAseprite(cls, data, base_dir: str = None, auto_material: bool = True,
                     **kwargs) -> "TextureAtlas":
        """LibreSprite / Aseprite 的 JSON（dict 或文件路径）→ 图集，一步到位。

        ::

            atlas = TextureAtlas.fromAseprite("res/atlas/hero.json")
            atlas = TextureAtlas.fromAseprite(raw_json, pixels_per_unit=16.0)

        ``res/atlas/hero.png`` 旁边的 ``hero_n.png`` / ``hero_l.png`` 会被自动认出来
        （见 :mod:`pyglgame.render.MaterialMaps`；``auto_material=False`` 关掉）。
        """
        from ..importers import aseprite as aseprite_importer
        if isinstance(data, str):
            document = aseprite_importer.fromFile(data, **kwargs)
            if base_dir is None:
                base_dir = Resources.dirnamePath(data) or None
        else:
            document = aseprite_importer.toAtlas(data, **kwargs)
        return cls.fromDict(document, base_dir=base_dir, auto_material=auto_material)

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        maps = (" 材质=" + "/".join(sorted(self.materialMaps()))
                if self.hasMaterialMaps else "")
        return ("<TextureAtlas%s %d 帧 / %d 动作 %sx%s ppu=%g%s%s>"
                % (label, len(self.frames), len(self.animations),
                   self.image_size[0], self.image_size[1], self.pixels_per_unit,
                   "" if not self.image_path else " " + str(self.image_path), maps))
