"""瓦片层（``TileMapLayer``）：把 Tiled 的一整层瓦片**合并成一个 VBO**。

为什么不做成"一格一个 GameObject"
----------------------------------

``RenderLayer`` 每帧遍历**全部**对象（``update`` + 两趟 ``render``），一张 100×60 的地图
就是 6000 个对象 —— 光遍历就够呛。这里的做法是**整层一次 draw call**：
一个对象、一个 ``RenderBuffer``、每格 6 个顶点（空瓦片一个顶点都不写）。

它跟谁配套
----------

- :class:`~pyglgame.render.TileSet.TileSet`：一张图 + 等分网格（``gid`` → UV）；
- :class:`~pyglgame.importers.tiled`：把 Tiled 的 ``tilelayer`` 翻成这个对象的构造参数；
- :class:`~pyglgame.scene.SceneRuntime`：按场景文档把它建出来。

坐标与几何
----------

- 顶点直接是**世界坐标**：层的**几何中心在原点**的那套局部坐标先按 ``r`` 绕
  ``(x, y, z)`` 转、再平移过去（不碰 ``RenderGlobal`` 的模型矩阵 —— 那几个矩阵是
  整体世界坐标系的调节）—— 于是"地图居中于世界原点"这件事在导入器里算过一次之后，
  这里就不用再管；改 ``x`` / ``y`` / ``r`` 之后 :meth:`rebuild`（``render()`` 会自动发现）；
- 每格的**左下角** ``= (col * tw - W/2, H/2 - (row + 1) * th)``：位置按**格子**
  （``tile_size``）排，图块按自己的尺寸（:meth:`tileDrawSize`，默认 = 格子）从这个角
  往右上画出去 —— 与 Tiled 的大图块对齐（Bottom Left）一致；图块正好等于格子时，
  它就退化成"每格居中"（老行为）；
- UV 走 ``TileSet``（``v0`` = 顶边），画的时候**下边取 ``v1``、上边取 ``v0``**；
- 每格的翻转（Tiled 的 gid 高位标志）只交换 UV，顶点不动。

::

    from pyglgame.gameobject.TileMapLayer import TileMapLayer
    from pyglgame.render.TileSet import TileSet

    tileset = TileSet(image="res/img/tiles.png", image_size=(256, 128),
                      tile_size=(32, 32), columns=8, first_gid=1)
    layer = TileMapLayer(tileset=tileset, columns=10, rows=5,
                         tiles=[1, 2, 3, 0, ...], tile_size=(1.0, 1.0), z=-1.0)

构造**不碰 GL**（贴图与 VBO 都推迟到第一次 ``render``）。

可选材质（法线 / 高光贴图）
--------------------------

地形也是一层"表面"，所以同样能吃 2D 光照：给 ``normal_map`` / ``specular_map``
（**与图块集同布局**的图 —— 帧矩形一一对应，否则光照会跟着格子"漂"，
见 ``docs/2D光照与阴影.md``）就参与光照，顶点格式自动带上 ``N2D`` / ``S2D`` 两个位
（那两个位**不产生新顶点属性**，只是换 ``formatType``）：

::

    layer = TileMapLayer(tileset=tileset, columns=60, rows=1, tiles=[...],
                         tile_size=(1.0, 1.0),
                         normal_map="res/scene/tiles_normal.png",      # 可选
                         specular_map="res/scene/tiles_specular.png",  # 可选
                         shininess=24.0, normal_strength=0.8)

    layer.setNormalMap(other_texture, strength=1.5)   # 运行时换 / 摘（``None`` = 摘掉）
    layer.setSpecularMap(None)

地形通常是"背景层"：``receive_shadow`` 保持默认 ``True``（**接收**阴影）。
"""

import math

import glm
from OpenGL.GL import GL_TEXTURE0, GL_TRIANGLES

from ..render.BufferBuilder import COL, POS, TEX
from ..render.Lighting2D import Material2D, lightingModeName
from ..render.RenderBuffer import RenderBuffer
from ..render.Texture import Texture
from ..render.TileSet import TileSet
from ..RenderGlobal import RenderGlobal
from .GameObject import GameObject

#: 空瓦片（Tiled 用 0；引擎也认 -1，方便手写）
EMPTY_GIDS = (0, -1)


class TileMapLayer(GameObject):
    """瓦片层（见模块文档）。"""

    def __new__(cls, *args, **kwargs):
        # ⚠ ``GameObject.__new__`` 只认 ``is_camera``：构造参数必须在这里吃掉，
        # 否则 ``TileMapLayer(tileset=…)`` 会在 ``__new__`` 上炸（与 ``Sprite2D`` 同理）
        return super().__new__(cls)

    def __init__(self, tileset=None, tiles=None, columns: int = 0, rows: int = 0,
                 tile_size=None, x: float = 0.0, y: float = 0.0, z: float = 0.0,
                 r: float = 0.0, color=(1.0, 1.0, 1.0, 1.0), opacity: float = 1.0,
                 flips=None, texture=None, transparent: bool = False,
                 pixels_per_unit: float = 32.0,
                 material: Material2D = None, normal_map=None, specular_map=None,
                 normal_strength: float = 1.0, specular_strength: float = 1.0,
                 specular_color=(1.0, 1.0, 1.0), specular_contrast: float = 1.0,
                 shininess: float = 32.0, specular_mode: str = "highlight",
                 receive_shadow: bool = True, tile_draw_size=None,
                 lighting_mode: str = "full", name: str = None) -> None:
        super().__init__()
        self.name = name
        if isinstance(tileset, dict):
            tileset = TileSet.fromDict(tileset)
        #: 图块集（``TileSet``）
        self.tileset = tileset
        #: 一维 gid 列表（行优先；``0`` / ``-1`` = 空）
        self.tiles = [int(value) for value in (tiles or [])]
        self.columns = int(columns)
        self.rows = int(rows)
        #: ``{格索引: [flip_x, flip_y]}``（只有真的翻转的格子才在里面）
        self.flips = {int(key): [bool(value[0]), bool(value[1])]
                      for key, value in (flips or {}).items()}
        self.pixels_per_unit = float(pixels_per_unit) or 32.0
        if tile_size is None:
            tile_size = (tileset.worldTileSize(self.pixels_per_unit) if tileset
                         else (1.0, 1.0))
        #: 每格的**世界**尺寸（= 地图的"单元格"，位置就按它排）
        self.tile_size = (float(tile_size[0]), float(tile_size[1]))
        #: 每格**画多大**（``None`` = 与 ``tile_size`` 相同）。Tiled 里图块可以比格子大
        #: （苔藓 128px、灯 32×96），那时位置仍按格子算、图按自己的尺寸从**格子左下角**
        #: 画出去 —— 见 :meth:`tileDrawSize`
        self.tile_draw_size = None
        if tile_draw_size is not None:
            self.setTileDrawSize(tile_draw_size, rebuild=False)
        self.opacity = float(opacity)
        #: 整层顶点色（``alpha`` 再乘 ``opacity``）
        self.color = tuple(float(v) for v in color)
        self.transparent = bool(transparent)
        self.x, self.y, self.z, self.r = float(x), float(y), float(z), float(r)
        #: 贴图覆盖（``None`` = 用 ``tileset.texture()``）
        self.texture = texture
        #: 材质（永不 ``None``）：给了 ``material`` 就用它，否则按下面几个参数现造一个
        self.material = material if material is not None else Material2D(
            normal_map=normal_map, specular_map=specular_map,
            normal_strength=normal_strength, specular_strength=specular_strength,
            specular_color=specular_color, specular_contrast=specular_contrast,
            shininess=shininess, specular_mode=specular_mode,
            receive_shadow=receive_shadow, lighting_mode=lighting_mode)
        self.render_buffer = RenderBuffer()
        #: 当前顶点缓冲格式（``None`` = 还没建；格式随材质变，见 :meth:`_ensureBufferFormat`）
        self._flags = None
        self._shader = None
        #: 内容修订号（``setTiles`` / ``setTile`` / ``markDirty`` 会 +1）
        self._revision = 0
        self._built_revision = -1
        #: 上一次烘进顶点的那组位置 / 角度（直接改 x / y / r 也能被发现）
        self._built_transform = None
        self._built_key = None
        self.vertexCount = 0
        self.rebuild()

    # === 尺寸 ===

    @property
    def width(self) -> float:
        """整层的世界宽度（列数 × 格宽）。"""
        return self.columns * self.tile_size[0]

    @property
    def height(self) -> float:
        """整层的世界高度（行数 × 格高）。"""
        return self.rows * self.tile_size[1]

    def tileDrawSize(self) -> tuple:
        """**一格画多大**（世界单位）：默认 = :attr:`tile_size`（格子尺寸）。

        Tiled 的瓦片层里图块可以比格子大（也可以小）：位置仍然按**格子**排，图则按自己的
        尺寸从**格子左下角**画出去（Tiled 的默认 Object Alignment 就是 Bottom Left，
        瓦片层同理）。所以 128×128 的苔藓瓦片放在 ``(列 0, 行 3)`` 正好铺满它右边 4 列、
        上边 4 行 —— 这就是"格子尺寸 ≠ 图块尺寸"这件事，由 :attr:`tile_draw_size` 表示。
        """
        return self.tile_draw_size or self.tile_size

    def setTileDrawSize(self, size=None, rebuild: bool = True) -> "TileMapLayer":
        """设置"每格画多大"（``None`` = 回到与 :attr:`tile_size` 相同），并重建几何。"""
        self.tile_draw_size = None if size is None else (float(size[0]), float(size[1]))
        return self.rebuild() if rebuild else self

    def indexOf(self, column: int, row: int) -> int:
        """``(列, 行)`` → 一维索引（越界抛 ``IndexError``）。"""
        column, row = int(column), int(row)
        if column < 0 or row < 0 or column >= self.columns or row >= self.rows:
            raise IndexError("瓦片坐标越界: (%d, %d)，层是 %dx%d"
                             % (column, row, self.columns, self.rows))
        return row * self.columns + column

    def tileAt(self, column: int, row: int) -> int:
        """``(列, 行)`` → gid（空位返回 0）。"""
        index = self.indexOf(column, row)
        return self.tiles[index] if index < len(self.tiles) else 0

    def flipAt(self, index: int) -> tuple:
        """格索引 → ``(flip_x, flip_y)``。"""
        value = self.flips.get(int(index))
        if not value:
            return (False, False)
        return (bool(value[0]), bool(value[1]))

    def isEmptyTile(self, gid) -> bool:
        return int(gid) in EMPTY_GIDS

    # === 内容 ===

    def setTiles(self, tiles, columns: int = None, rows: int = None,
                 flips=None) -> "TileMapLayer":
        """换整层的瓦片数据（会重建几何）。"""
        self.tiles = [int(value) for value in (tiles or [])]
        if columns is not None:
            self.columns = int(columns)
        if rows is not None:
            self.rows = int(rows)
        if flips is not None:
            self.flips = {int(key): [bool(value[0]), bool(value[1])]
                          for key, value in flips.items()}
        return self.rebuild()

    def setTile(self, column: int, row: int, gid: int,
                flip_x: bool = False, flip_y: bool = False) -> "TileMapLayer":
        """改一格的 gid（会重建几何 —— 编辑器里改完记得只重建一次）。"""
        index = self.indexOf(column, row)
        while len(self.tiles) <= index:
            self.tiles.append(0)
        self.tiles[index] = int(gid)
        if flip_x or flip_y:
            self.flips[index] = [bool(flip_x), bool(flip_y)]
        else:
            self.flips.pop(index, None)
        return self.rebuild()

    def setTileset(self, tileset, tile_size=None) -> "TileMapLayer":
        """换图块集（``dict`` 也认）并重建。"""
        if isinstance(tileset, dict):
            tileset = TileSet.fromDict(tileset)
        self.tileset = tileset
        if tile_size is not None:
            self.tile_size = (float(tile_size[0]), float(tile_size[1]))
        return self.rebuild()

    # === 材质（法线 / 高光贴图）===

    @property
    def formatFlags(self) -> int:
        """顶点格式：``POS | COL | TEX`` + 材质要求的 ``N2D`` / ``S2D`` 位。

        ⚠ 那两个位**不产生新的顶点属性**（见 ``docs/2D光照与阴影.md``）：它们只是告诉
        片元着色器 ``tex3`` / ``tex4`` 是 2D 法线 / 高光贴图。换格式靠**重建
        ``RenderBuffer``**（见 :meth:`_ensureBufferFormat`）—— 顶点行数一个都不变。
        """
        return POS | COL | TEX | self.material.formatFlags()

    def _ensureBufferFormat(self) -> None:
        """材质开关变了（法线 / 高光贴图挂上或摘掉）→ 用新格式重建顶点缓冲。"""
        flags = self.formatFlags
        if flags != self._flags or self.render_buffer.buffer_builder is None:
            self.render_buffer.createBuffer(GL_TRIANGLES, flags)
            self._flags = flags

    def setMaterial(self, material) -> "TileMapLayer":
        """换材质（``None`` = 回到"没有法线 / 高光"）；顶点格式跟着自动调整。

        ⚠ 必须走 :meth:`rebuild` 而不是只调 ``_ensureBufferFormat``：换格式会**新建**一个
        顶点缓冲（空的新缓冲），只换格式不重写顶点的话整层会直接画不出来（踩过）。
        """
        self.material = material if material is not None else Material2D()
        self.material.invalidate()
        return self.rebuild()

    def setNormalMap(self, texture, strength: float = None) -> "TileMapLayer":
        """只换法线贴图（``None`` = 摘掉）。``texture`` 要给**与图块集同布局**的法线图。"""
        self.material.normal_map = texture
        if strength is not None:
            self.material.normal_strength = float(strength)
        self.material.invalidate()
        return self.rebuild()

    def setSpecularMap(self, texture, strength: float = None) -> "TileMapLayer":
        """只换高光贴图（``None`` = 摘掉）。``texture`` 同样要与图块集同布局。"""
        self.material.specular_map = texture
        if strength is not None:
            self.material.specular_strength = float(strength)
        self.material.invalidate()
        return self.rebuild()

    # === 光照模式（按图层开关光照）===

    def setLightingMode(self, mode) -> "TileMapLayer":
        """改**光照模式**：``"full"`` / ``"ambient"``（只吃环境光）/ ``"none"``（完全不受光照）。

        走顶点格式位（``AMB2D`` / ``UNLIT2D``），所以格式变了要重建顶点缓冲 —— 这个方法
        自己会做（与 ``setNormalMap`` / ``setSpecularMap`` 同一套约定）。
        """
        self.material.lighting_mode = lightingModeName(mode)
        self.material.invalidate()
        return self.rebuild()

    def lightingMode(self) -> str:
        """当前光照模式（``"full"`` / ``"ambient"`` / ``"none"``）。"""
        return self.material.lighting_mode

    # === 几何 ===

    def markDirty(self) -> "TileMapLayer":
        """标记"内容变了，需要重建顶点"（直接改了 ``tiles`` / ``flips`` 时调这个）。"""
        self._revision += 1
        return self

    def rebuild(self) -> "TileMapLayer":
        """（重新）生成 CPU 侧顶点：每格 6 个顶点，**空瓦片一个顶点都不写**。

        顶点直接算成**世界坐标**（整层绕 ``(x, y, z)`` 转 ``r`` 之后平移）—— 位置不走
        ``RenderGlobal`` 的模型矩阵：那几个矩阵是**整体世界坐标系**的调节，对象不该
        拿它们摆自己，也不该覆盖它们。

        ⚠ 不碰 GL：真正的上传推迟到第一次 :meth:`render`（与 ``Sprite2D`` 一致）。
        """
        self._ensureBufferFormat()
        buffer = self.render_buffer
        builder = buffer.buffer_builder
        builder.clear()
        drawn = 0
        if (self.tileset is not None and self.tiles and self.columns > 0
                and self.rows > 0):
            cell_width, cell_height = self.tile_size
            draw_width, draw_height = self.tileDrawSize()
            layer_width, layer_height = self.width, self.height
            color = (self.color[0], self.color[1], self.color[2],
                     self.color[3] * self.opacity)
            angle = math.radians(self.r)
            cos_a, sin_a = (math.cos(angle), math.sin(angle)) if angle else (1.0, 0.0)
            ox, oy, oz = float(self.x), float(self.y), float(self.z)
            for index, gid in enumerate(self.tiles):
                row = index // self.columns
                if row >= self.rows:
                    break
                if self.isEmptyTile(gid):
                    continue
                column = index % self.columns
                # 层的几何中心在原点：第一格左上角 = (-W/2, +H/2)
                # 单元格的**左下角**（层的几何中心在原点）：位置按格子排、图按自己的尺寸
                # 从这个角画出去 —— 与 Tiled 的大图块对齐方式（Bottom Left）一致；
                # 图块正好等于格子时，这几行退化成"每格居中"（老行为，一个字节都不差）
                left = column * cell_width - layer_width * 0.5
                bottom = layer_height * 0.5 - (row + 1) * cell_height
                right = left + draw_width
                top = bottom + draw_height
                flip_x, flip_y = self.flipAt(index)
                u0, v0, u1, v1 = self.tileset.uvFor(gid, flip_x, flip_y)
                # 下边取 v1、上边取 v0（与 TextureAtlas / IAnimationFrame 同一套约定）
                corners = ((left, bottom, u0, v1), (right, bottom, u1, v1),
                           (left, top, u0, v0), (right, bottom, u1, v1),
                           (right, top, u1, v0), (left, top, u0, v0))
                for px, py, u, v in corners:
                    builder.pos(ox + px * cos_a - py * sin_a,
                                oy + px * sin_a + py * cos_a, oz
                                ).col(*color).tex(u, v).end()
                drawn += 1
        #: 这一层实际画了多少格（调试 / 测试用）
        self.vertexCount = drawn
        self._built_revision = self._revision
        self._built_transform = self.transformSignature()
        return self

    def transformSignature(self) -> tuple:
        """层的位置 / 角度（它们烘进顶点，变了就得重建）。"""
        return (round(float(self.x), 6), round(float(self.y), 6),
                round(float(self.z), 6), round(float(self.r), 6))

    # === 渲染 ===

    def render(self, dt: float, fps: float) -> None:
        if not self.active:
            return
        if (self._built_revision != self._revision
                or self._built_transform != self.transformSignature()):
            self.rebuild()
        render_global = RenderGlobal.instance
        texture = self.texture
        if texture is None:
            texture = (self.tileset.texture() if self.tileset is not None
                       else Texture.white())
        texture.bind(GL_TEXTURE0)
        shader = render_global.using_shader
        if shader is not self._shader:
            # 换了 program（shader 重载等）：材质缓存作废，保证这一帧真的上传一遍
            self.material.invalidate()
            self._shader = shader
        self.material.apply(shader, rotation=self.r)     # 法线 / 高光（没材质时内部会跳过）
        # 顶点已经是世界坐标：不碰那几个全局矩阵，只把光照 / 环境等的 uniform 同步过去
        render_global.updateGlobalUniforms()
        self.render_buffer.build()          # 顶点没变时会自己跳过上传
        if self.render_buffer.vertex_count:
            self.render_buffer.draw(False)
        super().render(dt, fps)

    def onRemoved(self) -> None:
        """注销时释放 VBO/VAO（贴图由 ``TextureCache`` / 使用者自己管）。"""
        if self.render_buffer is not None:
            self.render_buffer.release()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return ("<TileMapLayer%s %dx%d 格 %.2fx%.2f 单位 画出 %d 格 at "
                "(%.2f, %.2f, %.2f) z=%g %s>"
                % (label, self.columns, self.rows, self.tile_size[0], self.tile_size[1],
                   self.vertexCount, self.x, self.y, self.z, self.z, self.material))
