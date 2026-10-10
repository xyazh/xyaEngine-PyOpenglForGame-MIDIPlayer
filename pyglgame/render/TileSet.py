"""图块集（TileSet）：**一张图 + 等分网格**，给瓦片地图（``TileMapLayer``）用。

与 :class:`~pyglgame.render.TextureAtlas.TextureAtlas` 的分工
----------------------------------------------------------

====================================  ====================================
``TileSet``（本模块）                   ``TextureAtlas``
====================================  ====================================
每格尺寸相同、按行列**等分**           每格是**任意矩形**（打包器给的）
Tiled 的 ``tileset`` / 瓦片地图         LibreSprite 的图集 / 帧动画
用 ``gid``（从 ``first_gid`` 起算）     用帧名
====================================  ====================================

两者的 UV 约定**一致**：``v0`` = 瓦片的**顶边**（图片第一行对应 ``v = 0``），
所以"下边取 ``v1``、上边取 ``v0``"这条画法可以照抄。

``margin`` / ``spacing`` 是 Tiled 图块集的真实字段（外边距与格间距），
UV 公式与 Tiled 自己的一致：``x = margin + col * (tile_w + spacing)``。

本模块是**纯数据**（构造与 UV 计算不碰 GL），贴图和 ``TextureAtlas`` 一样懒加载。
"""

from OpenGL.GL import GL_CLAMP_TO_EDGE

from .Texture import LINEAR, NEAREST, Texture


class TileSet:
    """等分网格图块集（见模块文档）。"""

    def __init__(self, image=None, image_size=(0, 0), tile_size=(32, 32), columns: int = 0,
                 rows: int = 0, first_gid: int = 1, margin: int = 0, spacing: int = 0,
                 offset=(0.0, 0.0), tile_count: int = None, name: str = None,
                 properties=None, filter: int = NEAREST, mipmap: bool = False,
                 wrap_mode: int = GL_CLAMP_TO_EDGE) -> None:
        #: 图块集图片路径（``None`` = 用现成贴图）
        self.image_path = image
        self.image_size = tuple(int(v) for v in (image_size or (0, 0)))
        #: 每格像素尺寸 ``(w, h)``
        self.tile_size = (int(tile_size[0]), int(tile_size[1]))
        self.columns = int(columns)
        self.rows = int(rows)
        #: 本图块集第一个瓦片的 gid（Tiled 的 ``firstgid``）
        self.first_gid = int(first_gid)
        #: 图像外边距 / 格间距（像素）
        self.margin = int(margin)
        self.spacing = int(spacing)
        #: 每格内容的微调偏移（Tiled 的 ``tileoffset``，像素）
        self.offset = (float(offset[0]), float(offset[1]))
        self.tile_count = int(tile_count) if tile_count else self.columns * self.rows
        self.name = name
        self.properties = dict(properties or {})
        self.filter = filter
        self.mipmap = bool(mipmap)
        self.wrap_mode = wrap_mode
        self._texture = None
        self._image = None

    # === 读数 ===

    def __len__(self) -> int:
        return self.tile_count

    @property
    def tile_width(self) -> int:
        return self.tile_size[0]

    @property
    def tile_height(self) -> int:
        return self.tile_size[1]

    def localIndex(self, gid) -> int:
        """``gid`` → 本图块集里的**局部索引**（不属于本图块集时返回 ``-1``）。"""
        index = int(gid) - self.first_gid
        if index < 0 or (self.tile_count and index >= self.tile_count):
            return -1
        return index

    def has(self, gid) -> bool:
        return self.localIndex(gid) >= 0

    def lastGid(self) -> int:
        return self.first_gid + max(self.tile_count, 1) - 1

    # === UV（``v0`` = 顶边，与 TextureAtlas 同约定）===

    def ensureImageSize(self) -> tuple:
        """确保知道图片像素尺寸：显式 ``image_size`` > 读图片 > 按格数推。"""
        if self.image_size[0] > 0 and self.image_size[1] > 0:
            return self.image_size
        image = self.image()
        if image is not None:
            self.image_size = (int(image.width), int(image.height))
        elif self.columns > 0 and self.rows > 0:
            self.image_size = (self.columns * self.tile_size[0],
                               self.rows * self.tile_size[1])
        return self.image_size

    def image(self):
        """懒读盘（不碰 GL）；没有路径时返回 ``None``。"""
        if self._image is None and self.image_path:
            from ..ResourceLocation import ResourceLocation
            from .Image import Image
            self._image = Image(ResourceLocation(self.image_path))
        return self._image

    def tileUV(self, gid) -> tuple:
        """``gid`` → 图集 UV ``(u0, v0, u1, v1)``（``v0`` = 顶边）。"""
        index = self.localIndex(gid)
        if index < 0:
            return (0.0, 0.0, 0.0, 0.0)
        image_width, image_height = self.ensureImageSize()
        if image_width <= 0 or image_height <= 0:
            return (0.0, 0.0, 1.0, 1.0)
        columns = max(self.columns, 1)
        column = index % columns
        row = index // columns
        x = self.margin + column * (self.tile_width + self.spacing)
        y = self.margin + row * (self.tile_height + self.spacing)
        return (x / image_width, y / image_height,
                (x + self.tile_width) / image_width,
                (y + self.tile_height) / image_height)

    def uvFor(self, gid, flip_x: bool = False, flip_y: bool = False) -> tuple:
        """带翻转的 UV（交换两个边界就是翻转，顶点不用动）。"""
        u0, v0, u1, v1 = self.tileUV(gid)
        if flip_x:
            u0, u1 = u1, u0
        if flip_y:
            v0, v1 = v1, v0
        return (u0, v0, u1, v1)

    # === 世界尺寸 ===

    def worldTileSize(self, pixels_per_unit: float = 32.0) -> tuple:
        """每格的世界尺寸 ``(w, h)``。"""
        return (self.tile_width / pixels_per_unit, self.tile_height / pixels_per_unit)

    # === 贴图（懒上传）===

    def texture(self) -> "Texture":
        """图块集贴图；第一次调用才上传（同一文件走 ``TextureCache``）。"""
        if self._texture is None:
            if not self.image_path:
                self._texture = Texture.white()
            else:
                from .TextureCache import loadTexture
                self._texture = loadTexture(self.image_path, filter=self.filter,
                                            mipmap=self.mipmap,
                                            wrap_mode=self.wrap_mode)
        return self._texture

    def setTexture(self, texture) -> "TileSet":
        self._texture = texture
        return self

    def release(self) -> None:
        """丢掉自己持有的引用（不释放缓存里的纹理）。"""
        self._texture = None
        self._image = None

    # === 序列化（与 importers.tiled 的输出对齐）===

    def toDict(self) -> dict:
        """导出成 dict（形状与 ``importers.tiled.tilesetInfo`` 的输出一致）。"""
        data = {
            "name": self.name or "tileset",
            "texture": self.name or "tileset",
            "image": self.image_path or "",
            "first_gid": self.first_gid,
            "tile_size": [self.tile_width, self.tile_height],
            "image_size": [int(self.image_size[0]), int(self.image_size[1])],
            "columns": self.columns,
            "rows": self.rows,
            "tile_count": self.tile_count,
            "margin": self.margin,
            "spacing": self.spacing,
            "offset": [self.offset[0], self.offset[1]],
        }
        if self.properties:
            data["properties"] = dict(self.properties)
        return data

    @classmethod
    def fromDict(cls, data: dict) -> "TileSet":
        """从 ``importers.tiled`` 的图块集 dict（或自己手写的）建图块集。"""
        if not isinstance(data, dict):
            raise ValueError("图块集必须是 dict，收到 %r" % (type(data).__name__,))
        return cls(image=data.get("image") or None,
                   image_size=data.get("image_size") or (0, 0),
                   tile_size=data.get("tile_size") or (32, 32),
                   columns=data.get("columns") or 0,
                   rows=data.get("rows") or 0,
                   first_gid=data.get("first_gid", 1),
                   margin=data.get("margin", 0),
                   spacing=data.get("spacing", 0),
                   offset=data.get("offset") or (0.0, 0.0),
                   tile_count=data.get("tile_count"),
                   name=data.get("name"),
                   properties=data.get("properties"),
                   filter=LINEAR if str(data.get("filter", "nearest")).lower() == "linear"
                   else NEAREST,
                   mipmap=bool(data.get("mipmap", False)))

    def __repr__(self) -> str:
        return ("<TileSet %r %dx%d 格 %dx%d px first_gid=%d%s>"
                % (self.name or "", self.columns, self.rows,
                   self.tile_width, self.tile_height, self.first_gid,
                   "" if not self.image_path else " " + str(self.image_path)))
