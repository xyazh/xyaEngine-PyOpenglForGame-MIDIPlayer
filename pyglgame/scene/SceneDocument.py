"""场景文档（``SceneDocument``）：引擎自有的场景格式（**唯一权威**）。

它在整条管线里的位置（见 ``docs/资源导入管线.md``）::

    Tiled / LibreSprite / 手写 JSON
        → importers.tiled / importers.aseprite      （纯数据转换）
        → SceneDocument                             （本模块：校验 + 索引 + 引用解析）
        → SceneRuntime                              （建对象；第一次绘制才碰 GL）

文档长什么样（``*.scene.json``）::

    {
      "format_version": 1,
      "name": "level1",
      "pixels_per_unit": 32,
      "textures": {"tiles": "res/scene/level1_tiles.png"},
      "tilesets": [{"name": "tiles", "texture": "tiles", "image": "...",
                    "tile_size": [32, 32], "columns": 8, "first_gid": 1}],
      "atlases":  {"hero": "res/atlas/hero.json"},        // 也可内联一份图集文档
      "camera":   {"projection": "MSimple2D", "size": [20, 15], "use_lighting": true},
      "layers":   [{"type": "TileMapLayer", "tileset": "tiles", "columns": 20,
                    "rows": 15, "tiles": [...], "tile_size": [1.0, 1.0], "z": -1.0}],
      "objects":  [{"type": "Sprite2D", "texture": "tiles", "x": 0.0, "y": 0.0,
                    "width": 1.0, "height": 1.0}]
    }

本模块**只读数据、只算路径与索引，不碰 GL、不建对象**（所以可以离线校验、也能单测）。
"""

import copy
import json
import os

from .. import resources
from ..render.TextureAtlas import TextureAtlas
from ..render.TileSet import TileSet
from . import registry

#: 引擎场景文档的格式版本（导入器输出的也是这个）
FORMAT_VERSION = 1

#: 默认"1 世界单位 = 多少像素"
DEFAULT_PPU = 32.0


def _isFile(path) -> bool:
    """这个资源路径是不是一个**真实存在的文件**（目录不算）。

    给 :meth:`SceneDocument.resolve` 判\"要不要拼 ``base_dir``\"用 ——
    走 :mod:`pyglgame.resources`，所以目录形态与 ``res.xypack`` 资源包形态都吃。
    """
    return resources.exists(path) and not resources.isDir(path)


def _looksLikeAtlasFile(path: str) -> bool:
    """判断"这个 JSON 是不是图集"（给"按名字找图集 / 列出目录里的图集"用）。

    先读开头一小段（便宜），关键词不够时**再老实解析一次** ——
    LibreSprite 的 hash 导出把 ``meta``（含 ``frameTags``）放在**文件末尾**，
    只看开头 4KB 会漏判（踩过：图集就在旁边，却报"找不到"）。

    搜索目录里可能混着**场景 JSON**（``level1.json``）之类的文件，所以要能排除它们，
    免得给出误导性的提示。

    ⚠ 读文件走 :mod:`pyglgame.resources`（目录形态 / 资源包形态都行）：
    封包之后 ``path`` 在磁盘上并不存在，直接 ``open()`` 会一律判成"不是图集"。
    """
    from ..ResourceLocation import ResourceLocation

    data = ResourceLocation(path).toBytes()
    if not data:
        return False
    text = data.decode("utf-8", "ignore")
    head = text[:4096]
    if '"frames"' not in head:
        return False
    if '"meta"' in head or '"format_version"' in head:
        return True
    try:
        parsed = json.loads(text)
    except ValueError:
        return False
    return (isinstance(parsed, dict) and "frames" in parsed
            and ("meta" in parsed or "format_version" in parsed))


class SceneDocument:
    """一份场景文档（见模块文档）。"""

    FORMAT_VERSION = FORMAT_VERSION

    def __init__(self, data: dict = None, base_dir: str = "") -> None:
        self.data = dict(data or {})
        #: 相对路径的解析基准（通常是场景 JSON 所在目录）
        self.base_dir = str(base_dir or "")
        self._checkVersion()

    # === 构造 ===

    def _checkVersion(self) -> None:
        version = self.data.get("format_version", FORMAT_VERSION)
        try:
            version = int(version)
        except (TypeError, ValueError):
            raise ValueError("场景文档的 format_version 必须是整数，收到 %r" % (version,))
        if version > FORMAT_VERSION:
            raise ValueError("场景文档版本 %d 比引擎支持的 %d 新：请更新引擎，或用旧版导出"
                             % (version, FORMAT_VERSION))

    @classmethod
    def fromDict(cls, data: dict, base_dir: str = "") -> "SceneDocument":
        """从一个 dict 建文档（``data`` 为 ``None`` 时得到一份空场景）。"""
        if data is not None and not isinstance(data, dict):
            raise ValueError("场景文档必须是 dict，收到 %r" % (type(data).__name__,))
        return cls(data, base_dir=base_dir)

    @classmethod
    def fromFile(cls, path: str, base_dir: str = None) -> "SceneDocument":
        """读一个引擎自有格式的 ``*.scene.json``。"""
        from ..ResourceLocation import ResourceLocation

        text = ResourceLocation(path).tryToString()
        data = json.loads(text) if text.strip() else None
        if data is None:
            raise ValueError("场景文件是空的: %s" % path)
        if base_dir is None:
            base_dir = resources.dirnamePath(path)
        return cls.fromDict(data, base_dir=base_dir)

    @classmethod
    def fromTiled(cls, source, **kwargs) -> "SceneDocument":
        """Tiled 的地图（dict 或 ``*.json`` 路径）→ 场景文档（一步到位）。

        ::

            scene = SceneDocument.fromTiled("res/scene/level1.json")
            scene = SceneDocument.fromTiled(raw_json, pixels_per_unit=16.0)
        """
        from ..importers import tiled as tiled_importer

        if isinstance(source, str):
            document = tiled_importer.fromFile(source, **kwargs)
            base_dir = kwargs.get("base_dir") or resources.dirnamePath(source)
        else:
            document = tiled_importer.toScene(source, **kwargs)
            base_dir = kwargs.get("base_dir", "")
        return cls.fromDict(document, base_dir=base_dir)

    # === 读数 ===

    @property
    def name(self) -> str:
        return str(self.data.get("name") or "")

    @property
    def source(self) -> str:
        """来源标注（``"tiled"`` / ``"libresprite/aseprite"`` / 手写）。"""
        return str(self.data.get("source") or "")

    @property
    def pixels_per_unit(self) -> float:
        """1 世界单位 = 多少像素。"""
        try:
            value = float(self.data.get("pixels_per_unit", DEFAULT_PPU))
        except (TypeError, ValueError):
            return DEFAULT_PPU
        return value if value > 0.0 else DEFAULT_PPU

    @property
    def map(self) -> dict:
        """地图元信息（``width`` / ``height`` / ``tile_size`` / ``pixel_size`` …）。"""
        return dict(self.data.get("map") or {})

    @property
    def textures(self) -> dict:
        """贴图表：名字 → 路径。"""
        return dict(self.data.get("textures") or {})

    @property
    def atlases(self) -> dict:
        """图集表：名字 → 图集文档（dict）或 ``*.json`` 路径。"""
        return dict(self.data.get("atlases") or {})

    @property
    def tilesets(self) -> list:
        """图块集列表（每项是 dict）。"""
        return [dict(item or {}) for item in (self.data.get("tilesets") or [])]

    @property
    def layers(self) -> list:
        """瓦片层 / 图层条目（每项是 dict）。"""
        return [dict(item or {}) for item in (self.data.get("layers") or [])]

    @property
    def objects(self) -> list:
        """对象条目（每项是 dict）。"""
        return [dict(item or {}) for item in (self.data.get("objects") or [])]

    @property
    def camera(self):
        """相机描述（``None`` = 用引擎默认相机）。"""
        value = self.data.get("camera")
        return dict(value) if isinstance(value, dict) else None

    @property
    def physics(self):
        """物理世界描述（``None`` = 这个场景不建物理世界）。

        ::

            "physics": {"gravity": [0.0, -22.0],   // 横板 ACT 的"下落"
                        "fixed_step": 0.0166667,   // 固定步长（默认 1/60）
                        "max_steps": 5,            // 单帧最多补几步（防死亡螺旋）
                        "do_sleep": true}          // 允许刚体休眠（省 CPU）

        也可以写 ``"physics": true`` —— 那就是"用默认重力建一个世界"。
        重力为 ``(0, -22)`` 这种"比较大"的值是横板 ACT 的常见手感
        （真实 9.8 在像素比例下会显得很飘）。
        """
        value = self.data.get("physics")
        if value is True:
            return {}
        if isinstance(value, dict):
            return dict(value)
        return None

    @property
    def stats(self) -> dict:
        return dict(self.data.get("stats") or {})

    def toDict(self, deep: bool = True) -> dict:
        """导出成 dict（``deep=True`` 时深拷贝，改返回值不会污染文档）。"""
        return copy.deepcopy(self.data) if deep else dict(self.data)

    def clone(self) -> "SceneDocument":
        return SceneDocument(copy.deepcopy(self.data), base_dir=self.base_dir)

    # === 路径与引用解析 ===

    def resolve(self, path) -> str:
        """把文档里的相对路径解析成**资源路径**（相对 ``base_dir``）。

        ``base_dir`` 与结果都可以是逻辑路径（``res/scene/…``）—— 目录形态与
        资源包形态都能被 :class:`~pyglgame.ResourceLocation.ResourceLocation` 读。

        ⚠ 这里会**避免重复拼接**：导入器（``tiled.toScene`` / ``aseprite.fromFile``）
        交出来的字段（``textures`` / ``tilesets[].image`` …）已经按同一个
        ``base_dir`` 解析过一次；再拼一次就成了
        ``res/scene/res/scene/level1_tiles.png`` —— 构造时不报错，**绘制时加载贴图才炸**。
        所以按"哪个真的存在"来挑：

        1. ``base_dir/image`` 存在（文档里写相对路径的常规情况）→ 用它；
        2. ``image`` 原样存在（已经解析过 / 引擎自有文档里写全路径）→ 用它；
        3. 都不存在 → 仍返回 ``base_dir/image``（保持旧语义与报错信息）。
        """
        if not path:
            return ""
        path = str(path)
        if not self.base_dir or resources.isRealPath(path):
            return path
        joined = resources.joinPath(self.base_dir, path)
        if _isFile(path) and not _isFile(joined):
            return path
        return joined

    def resolvedTextures(self) -> dict:
        """``{名字: 资源路径}``（贴图名 → 能直接读的路径/逻辑路径）。"""
        return {name: self.resolve(path) for name, path in self.textures.items()}

    def texturePath(self, name) -> str:
        """按名字取贴图路径（名字不存在时**原样返回**该名字 —— 允许直接写路径）。"""
        found = self.textures.get(str(name))
        if found is None:
            return str(name or "")
        return self.resolve(found)

    def resolveAsset(self, path) -> str:
        """解析"资产路径"（贴图 / 材质贴图这类）：挑一个**真实存在**的候选。

        依次尝试，返回第一个存在的：

        1. 绝对路径 → 原样；
        2. 相对**场景文件目录**（``self.resolve``）；
        3. 原样（``res/atlas/chr_normal.png`` 这种逻辑路径，或者相对 CWD 的普通路径）；
        4. 相对 ``res``（``atlas/chr_normal.png``）；
        5. 都不是 → 原样返回（让 ``LazyTexture`` 自己去试，报错时路径就是用户写的那样）。

        与 :meth:`resolve` 的区别：``resolve`` 只管"文档里声明的引用"（贴图表 / 图块集），
        这里是给**对象属性里的路径**用的 —— 于是 JSON 里可以随手写
        ``"normal_map": "res/atlas/chr_normal.png"``，从哪个目录启动、有没有封包都能找到。
        """
        if not path:
            return ""
        text = str(path)
        if resources.isRealPath(text):
            return text
        candidates = []
        if self.base_dir:
            candidates.append(self.resolve(text))
        candidates.append(text)
        if not text.replace("\\", "/").startswith(resources.DEFAULT_PREFIX + "/"):
            candidates.append(resources.joinPath(resources.DEFAULT_PREFIX, text))
        for candidate in candidates:
            if resources.exists(candidate) and not resources.isDir(candidate):
                return candidate
        return candidates[-1]

    def tileset(self, name) -> "TileSet":
        """按名字（或 ``texture`` 键）取图块集；找不到返回 ``None``。"""
        key = str(name)
        for item in self.data.get("tilesets") or []:
            item = dict(item or {})
            if str(item.get("name")) == key or str(item.get("texture")) == key:
                tileset = TileSet.fromDict(item)
                tileset.image_path = self.resolve(item.get("image"))
                return tileset
        return None

    def tilesetsByName(self) -> dict:
        """``{名字: TileSet}``（``texture`` 键也做一次别名）。"""
        result = {}
        for item in self.data.get("tilesets") or []:
            item = dict(item or {})
            tileset = TileSet.fromDict(item)
            tileset.image_path = self.resolve(item.get("image"))
            result[str(item.get("name") or item.get("texture"))] = tileset
            if item.get("texture"):
                result[str(item["texture"])] = tileset
        return result

    def buildAtlases(self) -> dict:
        """``{名字: TextureAtlas}``。

        ``atlases`` 表里可以放：内联的图集文档（dict）、LibreSprite 的原始 JSON
        （含 ``meta`` 就会自动过一遍导入器）、或者 ``*.json`` 路径。

        **回退**：文档没登记、但**对象引用到**的名字（``"atlas": "hero"``），
        会去 :meth:`atlasSearchDirectories` 里找同名 ``.json`` ——
        所以"图集摆错文件名 / 忘了写 atlases 表"不会直接把场景判死，
        而是尽量找到并在 :meth:`missingAtlases` 里能看到结果。
        """
        result = {}
        for name, value in self.atlases.items():
            result[str(name)] = self.buildAtlas(value)
        for name in self.atlasNames():
            if name in result:
                continue
            found = self.findAtlasFile(name)
            if found:
                result[name] = self.buildAtlas(found)
        return result

    def atlasNames(self) -> set:
        """场景里**被引用**到的图集名（``objects[].atlas`` 是字符串的那些）。"""
        names = set()
        for _kind, entry in self.entries():
            value = entry.get("atlas")
            if isinstance(value, str) and value:
                names.add(value)
        return names

    def atlasSearchDirectories(self) -> list:
        """找"同名图集 JSON"时会看的目录（按顺序，去重，只留真实存在的）。

        顺序设计成"越靠近场景文件越优先"，最后兜底到引擎自带的 ``res/atlas``：

        1. 场景文件所在目录；
        2. ``<场景目录>/atlas``；
        3. ``<场景目录>/../atlas`` —— ``res/scene/`` 与 ``res/atlas/`` 这种常见布局；
        4. ``res/atlas``（**逻辑路径**：目录形态与 ``res.xypack`` 资源包形态都算）。

        返回的"目录"可能是逻辑路径（``res/atlas``）也可能是真实路径（``D:\\…``），
        下游的 :mod:`pyglgame.resources` 两者都吃。
        """
        candidates = []
        if self.base_dir:
            candidates.append(self.base_dir)
            candidates.append(resources.joinPath(self.base_dir, "atlas"))
            candidates.append(resources.joinPath(self.base_dir, os.pardir, "atlas"))
        candidates.append(resources.joinPath(resources.DEFAULT_PREFIX, "atlas"))
        seen = []
        for directory in candidates:
            if directory and directory not in seen and resources.isDir(directory):
                seen.append(directory)
        return seen

    def findAtlasFile(self, name: str):
        """按名字找图集 JSON：``hero`` → ``<搜索目录>/hero.json``（找不到返回 ``None``）。"""
        if not name:
            return None
        text = str(name)
        if resources.isRealPath(text) and os.path.isfile(text):
            return text
        base = text
        if base.lower().endswith(".json"):
            base = base[:-5]
        for directory in self.atlasSearchDirectories():
            for candidate in ("%s.json" % base, "%s.atlas.json" % base):
                path = resources.joinPath(directory, candidate)
                if (resources.exists(path) and not resources.isDir(path)
                        and _looksLikeAtlasFile(path)):
                    return path
        return None

    def missingAtlases(self) -> list:
        """被引用、但既没登记、也没能在搜索目录里找到的图集名。"""
        return sorted(name for name in self.atlasNames()
                      if name not in self.atlases and not self.findAtlasFile(name))

    def availableAtlasFiles(self) -> list:
        """搜索目录里**现成的**图集 JSON（去掉扩展名的名字，只用于报错提示）。

        "我新导出的图集怎么不生效"这类问题，十次有九次是**名字对不上**
        （LibreSprite 默认导出成 ``Sprite-0001``）：把目录里有什么直接列出来，
        一眼就能看出该改哪边。
        """
        names = []
        for directory in self.atlasSearchDirectories():
            for entry in resources.listDir(directory):
                if not entry.lower().endswith(".json"):
                    continue
                if not _looksLikeAtlasFile(resources.joinPath(directory, entry)):
                    continue
                stem = entry[:-5]
                if stem.endswith(".atlas"):
                    stem = stem[:-6]
                if stem not in names:
                    names.append(stem)
        return names

    def buildAtlas(self, value) -> "TextureAtlas":
        """把 ``atlases`` 表里的一项变成 :class:`TextureAtlas`（见 :meth:`buildAtlases`）。"""
        from ..importers import aseprite as aseprite_importer

        if isinstance(value, TextureAtlas):
            return value
        if isinstance(value, str):
            document = aseprite_importer.fromFile(self.resolveAsset(value))
            return TextureAtlas.fromDict(document, base_dir=self.base_dir)
        if isinstance(value, dict):
            looksLikeLibreSprite = ("meta" in value) or (
                "frames" in value and "format_version" not in value)
            if looksLikeLibreSprite:
                document = aseprite_importer.toAtlas(
                    value, image_path=self.resolve(aseprite_importer.imagePath(value)))
                return TextureAtlas.fromDict(document, base_dir=self.base_dir)
            return TextureAtlas.fromDict(value, base_dir=self.base_dir)
        raise ValueError("atlases 表里的值必须是图集文档 / LibreSprite JSON / 路径")

    def entries(self, withLayers: bool = True, withObjects: bool = True) -> list:
        """文档里的全部条目 ``[(kind, entry), ...]``（``kind`` = ``"layer"`` / ``"object"``）。"""
        result = []
        if withLayers:
            result.extend(("layer", entry) for entry in self.layers)
        if withObjects:
            result.extend(("object", entry) for entry in self.objects)
        return result

    def objectCount(self) -> int:
        return len(self.layers) + len(self.objects)

    # === 校验 ===

    def validate(self, checkRegistry: bool = True) -> list:
        """检查文档里明显对不上的地方，返回**问题清单**（空列表 = 没问题）。

        只做"便宜且能确定"的检查（不读贴图、不碰 GL）：

        - 瓦片层的 ``tiles`` 长度与 ``columns × rows`` 是否一致；
        - 瓦片层的 ``tileset`` 引用是否存在；
        - 对象的 ``texture`` / ``atlas`` 引用是否存在；
        - 对象的 ``type`` 是否在注册表里（``checkRegistry=False`` 可关掉）。
        """
        problems = []
        texture_names = set(self.textures)
        atlas_names = set(self.atlases)
        search_dirs = self.atlasSearchDirectories()
        found_cache = {}

        def findAtlas(name):
            if name not in found_cache:
                found_cache[name] = self.findAtlasFile(name)
            return found_cache[name]

        def checkTilesetKey(entry, where) -> None:
            key = entry.get("tileset")
            if key and str(key) not in texture_names and not self.tileset(key):
                problems.append("%s 引用了不存在的图块集 %r" % (where, key))

        for index, layer in enumerate(self.layers):
            where = "layers[%d](%s)" % (index, layer.get("name") or "?")
            checkTilesetKey(layer, where)
            tiles = layer.get("tiles") or []
            columns = int(layer.get("columns") or 0)
            rows = int(layer.get("rows") or 0)
            if columns > 0 and rows > 0 and len(tiles) != columns * rows:
                problems.append("%s 的 tiles 有 %d 个，但 %d×%d = %d"
                                % (where, len(tiles), columns, rows, columns * rows))

        for index, entry in enumerate(self.objects):
            where = "objects[%d](%s)" % (index, entry.get("name") or "?")
            texture = entry.get("texture")
            if isinstance(texture, str) and texture not in texture_names:
                problems.append("%s 引用了不存在的贴图 %r" % (where, texture))
            atlas = entry.get("atlas")
            if isinstance(atlas, str) and atlas not in atlas_names \
                    and atlas not in texture_names and not findAtlas(atlas):
                available = self.availableAtlasFiles()
                problems.append(
                    "%s 引用了图集 %r，但它既不在文档的 atlases 表里，搜索目录里也没有 "
                    "%s.json —— 搜索目录：%s；那里现成的图集是：%s。修法（任选一个）："
                    "① fromTiled/fromDict 时传 atlases={%r: 图集 JSON 路径}；"
                    "② 把图集放进上面某个目录并命名成 %s.json；"
                    "③ 在场景 JSON 里加一个 atlases 表；"
                    "④ 如果新图集是别的名字（LibreSprite 默认导出成 Sprite-0001），"
                    "就把 Tiled 里对象的 atlas 属性改成那个名字。"
                    % (where, atlas, atlas, "、".join(search_dirs) or "（无）",
                       "、".join(available) or "（空）", atlas, atlas))
            if checkRegistry:
                name = str(entry.get("type") or "")
                if not name:
                    problems.append("%s 没有 type" % where)
                elif registry.resolve(name) is None:
                    problems.append("%s 的类型 %r 没有注册" % (where, name))
        # 物理：写了 Body2D 却没有 physics 段 —— 建的时候会报"需要物理世界"
        if self.physics is None:
            for index, entry in enumerate(self.objects):
                if str(entry.get("type") or "") == "Body2D":
                    problems.append(
                        "objects[%d](%s) 是 Body2D，但文档里没有 physics 段 —— "
                        '加上 "physics": {"gravity": [0.0, -22.0]} 才会建物理世界'
                        % (index, entry.get("name") or "?"))
                    break
        return problems

    def assertValid(self, **kwargs) -> "SceneDocument":
        """:meth:`validate` 的"有问题就抛"版本（离线校验脚本用）。"""
        problems = self.validate(**kwargs)
        if problems:
            raise ValueError("场景文档有问题:\n- " + "\n- ".join(problems))
        return self

    def toFile(self, path: str, ensure_ascii: bool = False) -> str:
        """把文档写成一个 ``*.scene.json``（默认 UTF-8、不转义中文）。"""
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=ensure_ascii, indent=2)
        return path

    def __len__(self) -> int:
        return self.objectCount()

    def __repr__(self) -> str:
        return ("<SceneDocument %r %d 图层 + %d 对象 ppu=%g%s>"
                % (self.name or "未命名", len(self.layers), len(self.objects),
                   self.pixels_per_unit,
                   "" if not self.source else " 来自 " + self.source))
