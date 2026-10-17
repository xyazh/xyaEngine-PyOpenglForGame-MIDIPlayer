"""Tiled 的 JSON 导出 → 引擎场景文档（纯数据）。

为什么是"Tiled 的 JSON"而不是 TMX
----------------------------------

TMX 是 XML，虽然标准库能啃，但 Tiled 的 **JSON 导出**与"字典 → 对象"的映射更贴，
导入器更短、更好测。导出方式：``文件 → 导出为 → JSON``（或 ``tiled --export-map``）。

本模块负责把 Tiled 的"地图"翻成引擎的场景文档，**不碰 GL、不建 GameObject**：

.. code-block:: python

    from pyglgame.importers import tiled
    document = tiled.fromFile("res/scene/level1.json")     # Tiled 的 JSON
    scene = SceneDocument.fromDict(document)               # 引擎场景文档
    scene.instantiate()                                    # 建对象（这一步才碰 GL）

坐标与单位的换算（唯一一次，别在别处再翻）
----------------------------------------

============================  ==================================================
Tiled                         引擎
============================  ==================================================
左上原点、**Y 向下**、像素      左下原点、**Y 向上**、世界单位
``x`` / ``y`` 是矩形**左上角**  位置是对象**中心**
``rotation`` 顺时针为正         ``r`` 绕 Z 逆时针为正 → ``r = -rotation``
============================  ==================================================

地图的**几何中心**映射到世界原点 ``(0, 0)``（这样配
``MSimple2D.setBySize(w/PPU, h/PPU)`` 就是"整张地图铺满屏幕"）::

    x_world = (px - map_width_px / 2) / PPU
    y_world = (map_height_px / 2 - py) / PPU

支持 / 不支持
-------------

支持：``orthogonal`` 地图、``tilelayer``（数组 / CSV / base64±zlib|gzip）、
``objectgroup``（矩形 / 点 / 图块对象、``type`` 与 ``class`` 两代字段、自定义属性）、
``imagelayer``、内嵌图块集、瓦片的 **水平/垂直翻转**标志、
**对象模板**（``template`` 指向 ``*.tx``，注意那是 **XML** —— 见 :func:`loadTemplate`）。

明确不支持（会**报错**而不是默默画错）：``infinite`` 地图、非 ``orthogonal`` 朝向、
外部 ``.tsx`` 图块集、``zstd`` 压缩、对角翻转（旋转 90° 的瓦片）。

唯一的例外是**"没有 image 且 tilecount = 0"的空图块集**（Tiled 的"图像集合"式图块集、
或工程里删剩的空壳）：它一个 gid 都不占，所以只是**警告并跳过** ——
否则整张地图会因为一个用不到的残留条目打不开。占着 gid 的图像集合式图块集仍然报错
（认错图块比打不开更糟）。
"""

import base64
import json
import os
import zlib

from ..xyazhServer.ConsoleMessage import ConsoleMessage

#: 引擎场景文档的格式版本
FORMAT_VERSION = 1

#: 默认"1 世界单位 = 多少像素"
DEFAULT_PPU = 32.0

#: Tiled 瓦片值的高位标志（掩掉之后才是真正的 gid）
FLIPPED_HORIZONTALLY = 0x80000000
FLIPPED_VERTICALLY = 0x40000000
FLIPPED_DIAGONALLY = 0x20000000
ROTATED_HEXAGONAL_120 = 0x10000000
FLAG_MASK = (FLIPPED_HORIZONTALLY | FLIPPED_VERTICALLY | FLIPPED_DIAGONALLY
             | ROTATED_HEXAGONAL_120)


class TiledImportError(ValueError):
    """Tiled 文件里出现了引擎明确不支持的用法（宁可报错，也不要画错）。"""


# === 瓦片值与标志 ===

def tileFlags(value: int) -> tuple:
    """拆一个 Tiled 瓦片值 → ``(gid, flip_x, flip_y, rotate)``。

    Tiled 把"翻转"塞在 gid 的**高位**：不掩掉就会拿到一个天文数字当图集索引
    （这是最经典的坑，见 ``docs/资源导入管线.md``）。
    ``rotate`` 只对六边形地图的对角翻转有意义，正交地图里恒为 ``False``。
    """
    value = int(value)
    return (value & ~FLAG_MASK,
            bool(value & FLIPPED_HORIZONTALLY),
            bool(value & FLIPPED_VERTICALLY),
            bool(value & FLIPPED_DIAGONALLY))


# === Tiled 模板（``*.tx``，XML）===

def polygonPoints(text) -> list:
    """``"0,0 32,0 32,32"`` → ``[{"x": 0.0, "y": 0.0}, ...]``（Tiled JSON 的形状）。"""
    numbers = str(text or "").replace(",", " ").split()
    points = []
    for index in range(0, len(numbers) - 1, 2):
        try:
            points.append({"x": float(numbers[index]), "y": float(numbers[index + 1])})
        except ValueError:
            return points
    return points


def propertyValue(item) -> object:
    """Tiled 的 ``<property>`` 元素 → Python 值。

    XML 里**什么都是字符串**（``value="0.8"``），所以要按 ``type`` 转回来；
    不转的话 ``friction`` 会变成字符串、``height`` 也进不了 ``glm.vec3``（真踩过）。
    """
    value = item.get("value")
    if value is None:
        return None
    kind = str(item.get("type") or "").strip().lower()
    try:
        if kind == "int":
            return int(float(value))
        if kind == "float":
            return float(value)
    except ValueError:
        return value
    if kind == "bool":
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if kind == "color":
        return parseColor(value)
    if kind == "file":
        return str(value)
    return str(value)


def elementToObject(element) -> dict:
    """模板里的 ``<object>`` 元素 → 与 Tiled 的 **JSON 对象同形**的 dict（``toObject`` 直接吃）。"""
    result = {}
    for key in ("name", "type", "class"):
        if element.get(key):
            result[key] = element.get(key)
    for key in ("x", "y", "width", "height", "rotation"):
        value = element.get(key)
        if value is not None:
            result[key] = float(value)
    if element.get("gid"):
        result["gid"] = int(element.get("gid"))
    if element.get("visible") is not None:
        result["visible"] = str(element.get("visible")).strip().lower() \
            in ("1", "true", "yes", "on")
    if element.find("ellipse") is not None:
        result["ellipse"] = True
    if element.find("point") is not None:
        result["point"] = True
    for key in ("polygon", "polyline"):
        child = element.find(key)
        if child is not None:
            result[key] = polygonPoints(child.get("points"))
    properties = {}
    holder = element.find("properties")
    for item in (holder.findall("property") if holder is not None else []):
        name = item.get("name")
        if name:
            properties[str(name)] = propertyValue(item)
    if properties:
        result["properties"] = properties
    return result


def loadTemplate(reference: str, base_dir: str = "", cache: dict = None) -> dict:
    """读一个 Tiled 模板（``*.tx``，**XML**）→ ``{"object": {...}, "tileset": bool}``。

    - 只认标准库 ``xml.etree``（模板是 XML，不是 JSON —— 这点和地图文件不一样）；
    - 读不到 / 解析失败：**警告一次并返回 ``None``**（对象按导出 JSON 里有的属性来建，
      不会让整张地图打不开）；
    - ``cache`` 是调用方给的 ``{路径: 结果}``，同一份模板只读一次。
    """
    if not reference:
        return None
    from ..resources.Resources import Resources

    path = str(reference)
    if base_dir and not Resources.isRealPath(path):
        path = Resources.joinPath(base_dir, path)
    if cache is not None and path in cache:
        return cache[path]
    import xml.etree.ElementTree as ElementTree

    from ..ResourceLocation import ResourceLocation

    spec = None
    try:
        # 走资源层读字节（目录形态 / 资源包形态都行）再解析 —— 不能直接
        # ElementTree.parse(path)：封包之后那个路径在磁盘上并不存在
        root = ElementTree.fromstring(ResourceLocation(path).toBytes())
    except (OSError, ElementTree.ParseError) as error:
        ConsoleMessage.printWarning(
            "Tiled 模板 %r 读不出来（%s）：这一条对象的属性只能按导出的 JSON 来"
            "（模板文件通常要和地图放在一起）" % (reference, error))
    else:
        element = root.find("object")
        if element is None:
            ConsoleMessage.printWarning("Tiled 模板 %r 里没有 <object>，已忽略" % (reference,))
        else:
            spec = {"object": elementToObject(element),
                    "tileset": root.find("tileset") is not None}
            if spec["tileset"]:
                ConsoleMessage.printWarning(
                    "Tiled 模板 %r 里引用了 tileset：导出的地图 JSON 里没有这份图块集，"
                    "它的图块 UV 可能对不上" % (reference,))
    if cache is not None:
        cache[path] = spec
    return spec


def templateObject(source: dict, base_dir: str = "", cache: dict = None) -> dict:
    """Tiled **模板实例** → 合并后的对象 dict（``{"template": "ground2.tx", ...}``）。

    Tiled 导出 JSON 时**只写模板引用、不写模板内容**，所以 ``body_type`` / ``type`` /
    灯的参数全在 ``.tx`` 里 —— 不读模板就只剩一个"空对象"（踩过：平台退成 dynamic 往下掉、
    4 盏灯变成没类型的空 ``GameObject``）。

    合并规则与 Tiled 一致：**实例自己的字段优先**，``properties`` 按名字逐个覆盖；
    ``template`` 引用会留在结果里（``registry.RESERVED_KEYS`` 认它，只当来源标注）。
    """
    source = dict(source or {})
    reference = source.get("template")
    if not reference:
        return source
    template = loadTemplate(reference, base_dir=base_dir, cache=cache)
    if template is None:
        return source
    spec = dict(template.get("object") or {})
    merged = {key: value for key, value in spec.items() if key != "properties"}
    merged.update({key: value for key, value in source.items()
                   if key not in ("template", "properties")})
    # ⚠ 模板那侧已经转好类型了（``propertyValue``），别再过一遍 ``propertyDict`` ——
    #   它会把"值字典"当成"对象字典"去读 ``properties`` 键（踩过：属性静默全丢）
    properties = dict(spec.get("properties") or {})
    properties.update(propertyDict(source))
    if properties:
        merged["properties"] = properties
    merged["template"] = str(reference)
    return merged



def decodeLayerData(layer: dict) -> list:
    """``tilelayer.data`` → 一维 gid 列表（**原样**，标志没掩）。

    支持三种编码：JSON 数组（默认）、``csv``、``base64``（可选 zlib / gzip 压缩）。
    """
    data = layer.get("data")
    encoding = str(layer.get("encoding") or "").lower()
    compression = str(layer.get("compression") or "").lower()
    if isinstance(data, list):
        return [int(value) for value in data]
    if not isinstance(data, str):
        raise TiledImportError("tilelayer %r 的 data 既不是数组也不是字符串"
                               % (layer.get("name"),))
    if encoding == "csv":
        return [int(item.strip()) for item in data.split(",") if item.strip()]
    if encoding == "base64":
        raw = base64.b64decode(data)
        if compression == "zlib":
            raw = zlib.decompress(raw)
        elif compression == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        elif compression:
            raise TiledImportError("不支持的压缩方式 %r（支持 zlib / gzip）" % (compression,))
        # 小端 int32 序列
        count = len(raw) // 4
        return list(int.from_bytes(raw[index * 4:index * 4 + 4], "little")
                    for index in range(count))
    raise TiledImportError("tilelayer %r 的 encoding 不认识: %r"
                           % (layer.get("name"), layer.get("encoding")))


# === 坐标与单位（地图几何中心 = 世界原点）===

def mapPixelSize(data: dict) -> tuple:
    """地图的像素尺寸 ``(w, h)``。"""
    width = int(data.get("width") or 0) * int(data.get("tilewidth") or 0)
    height = int(data.get("height") or 0) * int(data.get("tileheight") or 0)
    return (width, height)


def toWorldPoint(pixel_size: tuple, px, py, ppu: float = DEFAULT_PPU) -> tuple:
    """Tiled 的像素点（左上原点、Y 向下）→ 世界坐标（居中、Y 向上）。"""
    width, height = float(pixel_size[0]), float(pixel_size[1])
    return ((float(px) - width * 0.5) / ppu, (height * 0.5 - float(py)) / ppu)


def toWorldCenter(pixel_size: tuple, x, y, width, height,
                  ppu: float = DEFAULT_PPU) -> tuple:
    """Tiled 的矩形（左上角 + 宽高）→ 世界**中心**坐标。"""
    return toWorldPoint(pixel_size, float(x) + float(width) * 0.5,
                        float(y) + float(height) * 0.5, ppu)


# === 图块集 ===

def tilesetInfo(tileset: dict, key: str = None) -> dict:
    """内嵌图块集 → 引擎的图块集描述（含每格像素尺寸 / 列数 / margin / spacing）。

    ⚠ 外部 ``.tsx`` 与"图像集合（collection of images）"式图块集不支持：前者要读 XML，
    后者与"一张图 + 等分网格"的模型不同。请在 Tiled 里**嵌入**图块集再导出。
    """
    if not isinstance(tileset, dict):
        raise TiledImportError("图块集必须是 dict，收到 %r" % (type(tileset).__name__,))
    if tileset.get("source"):
        raise TiledImportError(
            "外部图块集（.tsx）还不支持：请在 Tiled 里把图块集**嵌入地图**（图块集 → 嵌入）后重新导出 JSON")
    image = tileset.get("image")
    if not image:
        raise TiledImportError("图块集 %r 没有 image（图像集合式图块集还不支持）"
                               % (tileset.get("name"),))
    tile_width = int(tileset.get("tilewidth") or 0)
    tile_height = int(tileset.get("tileheight") or 0)
    image_width = int(tileset.get("imagewidth") or 0)
    image_height = int(tileset.get("imageheight") or 0)
    margin = int(tileset.get("margin") or 0)
    spacing = int(tileset.get("spacing") or 0)
    columns = int(tileset.get("columns") or 0)
    if columns <= 0 and tile_width > 0 and image_width > 0:
        # 老版本 / 手写 JSON 可能没写 columns：按 Tiled 的公式推一遍
        columns = max((image_width - 2 * margin + spacing) // (tile_width + spacing), 1)
    rows = 0
    if tile_height > 0 and image_height > 0:
        rows = max((image_height - 2 * margin + spacing) // (tile_height + spacing), 0)
    offset = tileset.get("tileoffset") or {}
    name = str(tileset.get("name") or key or "tileset")
    return {
        "name": name,
        "texture": key or name,
        "image": str(image),
        "first_gid": int(tileset.get("firstgid") or 1),
        "tile_size": [tile_width, tile_height],
        "image_size": [image_width, image_height],
        "columns": columns,
        "rows": rows,
        "tile_count": int(tileset.get("tilecount") or columns * rows),
        "margin": margin,
        "spacing": spacing,
        "offset": [float(offset.get("x", 0)), float(offset.get("y", 0))],
        "properties": propertyDict(tileset),
    }


def tileUV(tileset: dict, gid: int) -> tuple:
    """gid → 图集 UV ``(u0, v0, u1, v1)``，``v0`` = **顶边**（与 ``TextureAtlas`` 同约定）。"""
    local = int(gid) - int(tileset["first_gid"])
    columns = max(int(tileset["columns"]), 1)
    column = local % columns
    row = local // columns
    tile_width, tile_height = tileset["tile_size"]
    image_width = max(int(tileset["image_size"][0]), 1)
    image_height = max(int(tileset["image_size"][1]), 1)
    x = int(tileset["margin"]) + column * (tile_width + int(tileset["spacing"]))
    y = int(tileset["margin"]) + row * (tile_height + int(tileset["spacing"]))
    return (x / image_width, y / image_height,
            (x + tile_width) / image_width, (y + tile_height) / image_height)


def tilesetFor(tilesets: list, gid: int) -> dict:
    """按 gid 找它属于哪个图块集（``firstgid`` 从大到小比）。"""
    chosen = None
    for tileset in tilesets:
        if int(gid) >= int(tileset["first_gid"]):
            if chosen is None or int(tileset["first_gid"]) >= int(chosen["first_gid"]):
                chosen = tileset
    return chosen


# === 属性 ===

def parseColor(value):
    """Tiled 的颜色属性 ``#RRGGBB`` / ``#AARRGGBB``（8 位时 alpha 在前） → ``[r, g, b, a]``（0~1 的 **list**）。

    不是颜色就原样返回。Tiled 把颜色存成字符串，而引擎的 ``color`` 参数要序列
    （``light2D(color=...)`` / 顶点色都是 0~1 的浮点），所以在**导入器**这一层转掉。

    ⚠ 返回 ``list`` 而不是 ``tuple``：场景文档要能被 ``json.dump`` 出来再读回去
    （tuple 会被 JSON 变成 list，往返就不相等了 —— 踩过）。
    """
    if not isinstance(value, str) or not value.startswith("#"):
        return value
    text = value[1:]
    if len(text) not in (6, 8):
        return value
    try:
        if len(text) == 8:
            # Tiled / Qt 的 8 位颜色是 ``#AARRGGBB``（**alpha 在前**）：
            # Qt 的 ``QColor::name(QColor::HexArgb)``，Tiled 导出的颜色属性永远是 8 位，
            # 不透明时前两位就是 ``ff``。按 ``#RRGGBBAA`` 读会把颜色拆错：
            # 绿色 ``#ff00ff00``（alpha=ff, RGB=00ff00）会变成 R=ff B=ff 的**品红**。
            alpha, red, green, blue = (int(text[index:index + 2], 16) / 255.0
                                       for index in (0, 2, 4, 6))
            return [red, green, blue, alpha]
        red, green, blue = (int(text[index:index + 2], 16) / 255.0
                            for index in (0, 2, 4))
    except ValueError:
        return value
    return [red, green, blue, 1.0]


def propertyDict(source) -> dict:
    """Tiled 的 ``properties`` 列表 → 普通 dict（也接受**已经是 dict** 的输入）。

    自定义属性就是引擎构造器的 kwargs，所以这里只做两件事：

    - "列表 → 字典"；
    - ``type == "color"`` 的属性转成 ``(r, g, b, a)``（见 :func:`parseColor`）。

    Tiled 已经把 ``int`` / ``float`` / ``bool`` / ``string`` 反序列化好了，其余原样传。
    """
    if source is None:
        return {}
    properties = source.get("properties") if isinstance(source, dict) else source
    if isinstance(properties, dict):
        return {key: parseColor(value) for key, value in properties.items()}
    result = {}
    for item in properties or []:
        if not isinstance(item, dict) or "name" not in item:
            continue
        value = item.get("value")
        if str(item.get("type", "")).lower() == "color" or (
                isinstance(value, str) and value.startswith("#")):
            value = parseColor(value)
        result[str(item["name"])] = value
    return result


# === 对象 ===

def toObject(source: dict, pixel_size: tuple, ppu: float, tilesets: list,
             default_type: str = "GameObject") -> dict:
    """Tiled 的一个对象（矩形 / 点 / 图块 / 椭圆 / 多边形）→ 引擎对象描述。

    ``type`` 与 ``class`` 都读（Tiled 1.9 起把 ``type`` 改名成 ``class``）；
    自定义属性铺在最前面，**几何字段（x/y/width/height/r）最终由导入器算** ——
    因为坐标换算是导入器的职责，不该被属性里的手写值悄悄顶掉。
    """
    properties = propertyDict(source)
    object_type = source.get("class") or source.get("type") or default_type or ""
    result = {"type": str(object_type)}
    if source.get("name"):
        result["name"] = str(source["name"])
    if source.get("id") is not None:
        result["source_id"] = int(source["id"])
    if source.get("template"):
        #: Tiled 的"这个对象来自模板文件（``*.tx``）"引用。只是**元数据**：导出 JSON 里
        #: 没有模板内容，引擎读不到属性；但留着它，出问题时一眼就能看出"这块是从模板来的"
        #: （``registry.RESERVED_KEYS`` 里认这个键，不会被当成"写错的属性"警告）
        result["template"] = str(source["template"])
    result.update(properties)                      # 作者自定义属性（构造器 kwargs）

    x = float(source.get("x") or 0.0)
    y = float(source.get("y") or 0.0)
    width = float(source.get("width") or 0.0)
    height = float(source.get("height") or 0.0)
    gid = int(source.get("gid") or 0)
    if gid:
        gid, flip_x, flip_y, _ = tileFlags(gid)
        tileset = tilesetFor(tilesets, gid)
        if tileset is None:
            raise TiledImportError("对象 %r 用的图块（gid=%d）不属于任何图块集"
                                   % (source.get("name"), gid))
        tile_width, tile_height = tileset["tile_size"]
        u0, v0, u1, v1 = tileUV(tileset, gid)
        center_x, center_y = toWorldCenter(pixel_size, x, y, tile_width, tile_height, ppu)
        result.update({"x": center_x, "y": center_y,
                       "texture": tileset["texture"],
                       # ⚠ Sprite2D 的 uv 里第一个 v 对应四边形**下**边 → 把顶底换一下
                       "uv": [u0, v1, u1, v0],
                       "width": tile_width / ppu, "height": tile_height / ppu})
        result["shape"] = "tile"
    elif width > 0.0 and height > 0.0:
        center_x, center_y = toWorldCenter(pixel_size, x, y, width, height, ppu)
        result.update({"x": center_x, "y": center_y,
                       "width": width / ppu, "height": height / ppu})
        result["shape"] = "ellipse" if source.get("ellipse") else "rect"
    else:
        point_x, point_y = toWorldPoint(pixel_size, x, y, ppu)
        result.update({"x": point_x, "y": point_y})
        result["shape"] = "point"
    rotation = float(source.get("rotation") or 0.0)
    if rotation:
        # Tiled 顺时针为正，引擎的 r 是绕 Z **逆时针**为正 → 取负
        result["r"] = -rotation
    polygon = source.get("polygon") or source.get("polyline")
    if polygon:
        result["points"] = [[float(point.get("x", 0.0)) / ppu,
                             -float(point.get("y", 0.0)) / ppu] for point in polygon]
        result["shape"] = "polygon" if source.get("polygon") else "polyline"
    # ⚠ ``width`` / ``height`` / ``r`` / ``z`` 的语义**因类而异**：
    # 对 ``Sprite2D`` 是"精灵尺寸 / 旋转 / 深度"，对 ``light2D`` 却是"作用范围？
    # 不 —— ``height`` 是**光的离面高度**、``z`` 是光的位置高度。所以作者在
    # properties 里显式写过的这几个键**优先**，几何只在没写时兜底。
    # （位置 ``x`` / ``y`` 反过来：那是 Tiled 里拖出来的，以几何为准。）
    for key in ("width", "height", "r", "z", "shape"):
        if key in properties:
            result[key] = properties[key]
    return result


# === 图层 ===

def tileLayerDict(layer: dict, pixel_size: tuple, ppu: float, tilesets: list,
                  z: float = 0.0, cell_size=None) -> dict:
    """``tilelayer`` → 引擎的 ``TileMapLayer`` 描述（整层一个 VBO 的那个）。

    :param cell_size: **地图的格子像素尺寸** ``(tilewidth, tileheight)``。Tiled 的瓦片层
        位置永远是按**地图格子**排的，而图块可以比格子大（128×128 的苔藓、32×96 的灯）——
        所以"格子"与"图块"要分开：``tile_size`` 用格子、图块自己的尺寸进
        ``tile_draw_size``（引擎按**格子左下角**画，与 Tiled 的大图块对齐一致）。
        不传时退回老行为（用图块集的尺寸当格子），方便单独调这个函数的老代码。
    
    ⚠ 一层只用**一个**图块集（引擎的瓦片层就是"一张图 + 等分网格"）：层里出现别的
    图块集的 gid 时会警告一次，并按第一个非空 gid 所属的图块集算 UV。
    """
    raw = decodeLayerData(layer)
    columns = int(layer.get("width") or 0)
    rows = int(layer.get("height") or 0)
    if columns <= 0 or rows <= 0:
        raise TiledImportError("tilelayer %r 缺 width/height" % (layer.get("name"),))
    tiles = []
    flips = {}
    rotated = False
    chosen = None
    for index, value in enumerate(raw):
        gid, flip_x, flip_y, rotate = tileFlags(value)
        rotated = rotated or rotate
        tiles.append(gid)
        if flip_x or flip_y:
            flips[index] = [flip_x, flip_y]
        if gid and chosen is None:
            chosen = tilesetFor(tilesets, gid)
    if chosen is None:
        chosen = tilesets[0] if tilesets else None
    if chosen is None:
        raise TiledImportError("tilelayer %r 为非空，但地图里没有图块集" % (layer.get("name"),))
    for index, value in enumerate(tiles):
        if not value:
            continue
        owner = tilesetFor(tilesets, value)
        if owner is not None and owner is not chosen:
            ConsoleMessage.printWarning(
                "tilelayer %r 里混用了多个图块集（%s / %s）：一层只按 %s 算 UV，"
                "请把它们拆成多个图层。"
                % (layer.get("name"), chosen["name"], owner["name"], chosen["name"]))
            break
    if rotated:
        ConsoleMessage.printWarning(
            "tilelayer %r 里有对角翻转（旋转 90°）的瓦片：引擎的四边形 UV 不做旋转，"
            "这几格会画成未旋转的样子。" % (layer.get("name"),))
    tile_width, tile_height = chosen["tile_size"]
    # 格子（位置）用地图的尺寸；图块比格子大/小时，图自己的尺寸进 ``tile_draw_size``
    if cell_size:
        cell_width, cell_height = float(cell_size[0]), float(cell_size[1])
    else:
        cell_width, cell_height = float(tile_width), float(tile_height)
    opacity = float(layer.get("opacity", 1.0) or 0.0)
    result = {
        "type": "TileMapLayer",
        "name": str(layer.get("name") or ""),
        "tileset": chosen["texture"],
        "columns": columns,
        "rows": rows,
        "tiles": tiles,
        "flips": flips,
        "tile_size": [cell_width / ppu, cell_height / ppu],
        "x": 0.0, "y": 0.0,
        # 图层顺序 = 绘制顺序：z 越小越靠后（引擎 2D 的 z 越大越靠近相机）
        "z": float(z),
        "opacity": opacity,
    }
    if (cell_width, cell_height) != (float(tile_width), float(tile_height)):
        # 大图块：按格子左下角画出去（Tiled 的默认对齐），尺寸在这里写死
        result["tile_draw_size"] = [tile_width / ppu, tile_height / ppu]
    # 图层自定义属性 = 构造参数（与对象层同一套约定）：在 Tiled 的瓦片层上写
    # ``normal_map`` / ``specular_map`` 就是**地形材质**（法线 / 高光，两者都可选），
    # ``shininess`` / ``normal_strength`` / ``receive_shadow`` 这些也照样认。
    # 下面这些键是导入器自己算出来的，不允许被属性盖掉（不然几何就乱了）。
    computed = ("type", "name", "tileset", "tiles", "columns", "rows", "flips",
                "tile_size", "tile_draw_size", "opacity")
    for key, value in propertyDict(layer).items():
        if key not in computed:
            result[key] = value
    if layer.get("offsetx") or layer.get("offsety"):
        offset_x, offset_y = toWorldPoint(pixel_size, layer.get("offsetx", 0),
                                          layer.get("offsety", 0), ppu)
        result["x"] = offset_x
        result["y"] = offset_y
    if layer.get("startx") or layer.get("starty"):
        # 固定尺寸地图里 startx/starty 就是层内容相对地图左上角的偏移
        start_x, start_y = toWorldPoint(pixel_size, layer.get("startx", 0),
                                        layer.get("starty", 0), ppu)
        result["x"] = start_x
        result["y"] = start_y
    return result


def imageLayerDict(layer: dict, pixel_size: tuple, ppu: float, textures: dict,
                   base_dir: str = "", z: float = 0.0) -> dict:
    """``imagelayer`` → 一个 ``Sprite2D``（整张图当背景 / 前景用）。"""
    image = layer.get("image")
    if not image:
        return None
    key = textureKey(layer.get("name") or "image", textures)
    textures[key] = resolvePath(image, base_dir)
    width = float(layer.get("imagewidth") or 0.0)
    height = float(layer.get("imageheight") or 0.0)
    center_x, center_y = toWorldCenter(pixel_size, layer.get("x", 0),
                                       layer.get("y", 0), width, height, ppu)
    result = {"type": "Sprite2D", "name": str(layer.get("name") or ""),
              "texture": key, "x": center_x, "y": center_y,
              "width": width / ppu, "height": height / ppu,
              "z": float(z), "shape": "image"}
    opacity = float(layer.get("opacity", 1.0) or 0.0)
    if opacity < 1.0:
        result["color"] = [1.0, 1.0, 1.0, opacity]
    return result


def textureKey(name, textures: dict) -> str:
    """给 textures 表生成一个不重名的键（``ground`` / ``ground2`` / …）。"""
    key = str(name or "texture")
    if key not in textures:
        return key
    index = 2
    while ("%s%d" % (key, index)) in textures:
        index += 1
    return "%s%d" % (key, index)


def resolvePath(path, base_dir: str = "") -> str:
    """把 Tiled 里的相对文件名解析成**资源路径**（``base_dir`` = JSON 所在目录）。

    ``base_dir`` 可以是逻辑路径（``res/scene``），结果也按逻辑路径走 ——
    目录形态与资源包形态都能读。
    """
    if not path:
        return ""
    if not base_dir:
        return str(path)
    from ..resources.Resources import Resources

    if Resources.isRealPath(str(path)):
        return str(path)
    return Resources.joinPath(base_dir, str(path))


def physicsFromMap(data: dict, objects=None) -> dict:
    """Tiled 的地图属性 → 引擎的 ``physics`` 段（``None`` = 这个地图不用物理）。

    在 Tiled 里写"地图 → 属性"（英文键名，因为就是引擎参数名）：

    ==================  ==========  ==================================================
    ``physics``         bool        显式开关（``true`` = 建物理世界）
    ``gravity``         string      ``"0,-26"``（逗号分隔，比写两个属性方便）
    ``gravity_x``       float       分开写也行（``gravity_y`` 同理）
    ``gravity_y``       float       缺省 ``-9.8``
    ``fixed_step``      float       物理固定步长（默认 1/60）
    ``max_steps``       int         单帧最多补几步（默认 5）
    ``do_sleep``        bool        允许刚体休眠（默认 true）
    ==================  ==========  ==================================================

    ⚠ **就算没写 ``physics``，只要地图里有 ``Body2D`` 对象也自动建世界** —— 否则那些
    碰撞箱会因为"没有物理世界"而全部建不出来（对作者来说很反直觉）。
    """
    properties = propertyDict(data)
    has_bodies = any(str((item or {}).get("type") or "").strip() == "Body2D"
                     for item in (objects or []))
    # 只要作者表达了"我要物理"就算：放了 Body2D、或写了任何物理相关属性
    physics_keys = ("gravity", "gravity_x", "gravity_y", "fixed_step",
                    "max_steps", "do_sleep")
    wants = has_bodies or any(key in properties for key in physics_keys)
    explicit = properties.get("physics")
    if explicit is not None:
        if isinstance(explicit, str):
            explicit = explicit.strip().lower() in ("1", "true", "yes", "on")
        if not explicit:
            return None                       # 作者**显式**关掉了（比如临时调试）
    elif not wants:
        return None                           # 什么都没表达 → 普通 2D 场景，不建物理世界
    spec = {}
    gravity = properties.get("gravity")
    if isinstance(gravity, (list, tuple)) and len(gravity) >= 2:
        spec["gravity"] = [float(gravity[0]), float(gravity[1])]
    elif isinstance(gravity, str) and "," in gravity:
        parts = [item.strip() for item in gravity.split(",")]
        spec["gravity"] = [float(parts[0]), float(parts[1])]
    elif "gravity_x" in properties or "gravity_y" in properties:
        spec["gravity"] = [float(properties.get("gravity_x", 0.0)),
                           float(properties.get("gravity_y", -9.8))]
    for key in ("fixed_step", "max_steps", "do_sleep", "name"):
        if key in properties:
            spec[key] = properties[key]
    return spec


# === 主入口 ===

def toScene(data: dict, base_dir: str = "", pixels_per_unit: float = DEFAULT_PPU,
            name: str = None, default_object_type: str = "GameObject",
            include_hidden: bool = False, with_camera: bool = True,
            atlases: dict = None) -> dict:
    """Tiled 的地图 JSON → **引擎场景文档**（可喂给 ``SceneDocument.fromDict``）。

    :param data: ``json.loads`` 之后的地图 JSON
    :param base_dir: 地图 JSON 所在目录（用来把 ``image`` 解析成真实路径）
    :param pixels_per_unit: 1 世界单位 = 多少像素（Tiled 的 ``tilewidth`` 就是像素）
    :param default_object_type: 对象没写 ``type`` / ``class`` 时用什么类
    :param include_hidden: 是否连"隐藏的图层 / 对象"一起导入（默认跳过，和 Tiled 观感一致）
    :param with_camera: 是否生成"整张地图铺满屏幕"的默认 2D 相机
    :param atlases: ``{图集名: LibreSprite 的 JSON 路径或图集文档}``。
        Tiled 自己**没有**"图集（帧动画）"这个概念，所以想在 Tiled 里摆
        ``AtlasSprite``（带帧动画的角色）就得用这个参数把图集表补进场景文档 ——
        对象上写 ``type = AtlasSprite`` + 自定义属性 ``atlas = hero`` / ``animation = idle`` 即可。
    """
    if not isinstance(data, dict):
        raise TiledImportError("地图必须是 dict，收到 %r" % (type(data).__name__,))
    if data.get("infinite"):
        raise TiledImportError(
            "无限地图（infinite）还不支持：请在 Tiled 里把地图设成固定尺寸再导出")
    orientation = str(data.get("orientation") or "orthogonal")
    if orientation != "orthogonal":
        raise TiledImportError("只支持正交（orthogonal）地图，收到 %r" % (orientation,))
    pixel_size = mapPixelSize(data)
    if pixel_size[0] <= 0 or pixel_size[1] <= 0:
        raise TiledImportError(
            "地图尺寸算出来是 0：需要 width / height / tilewidth / tileheight 四个字段")
    #: 地图的**格子**尺寸（像素）：瓦片层的位置按它排，图块本身可以比它大（见
    #: :func:`tileLayerDict` 的 ``cell_size``）
    map_cell_size = (int(data.get("tilewidth") or 0), int(data.get("tileheight") or 0))

    textures = {}
    tilesets = []
    #: 被跳过的条目数（图层 / 对象 / 空图块集）—— 统计信息里看得见
    skipped = 0
    for index, raw in enumerate(data.get("tilesets") or []):
        raw = dict(raw or {})
        key = textureKey(raw.get("name") or ("tileset%d" % index), textures)
        # Tiled 的"图像集合（collection of images）"式 / 空图块集没有 ``image``：
        # ``tilecount`` 是 0 的话它**一个瓦片都不占**（只是工程里的残留），跳过比
        # 让整张地图打不开合理；占着 gid 的那种仍然由 ``tilesetInfo`` 报错（认错更糟）
        if not raw.get("image") and int(raw.get("tilecount") or 0) <= 0:
            skipped += 1
            ConsoleMessage.printWarning(
                "Tiled 图块集 %r 没有 image 且 tilecount = 0（图像集合式 / 空图块集）："
                "已跳过 —— 它不占任何 gid；在 Tiled 里删掉它更干净"
                % (raw.get("name") if raw.get("name") is not None else index,))
            continue
        info = tilesetInfo(raw, key=key)
        textures[key] = resolvePath(info["image"], base_dir)
        tilesets.append(info)

    layers = []
    objects = []
    #: 模板缓存（``{路径: 解析结果}``）：同一份 ``.tx`` 只读一次
    templates = {}
    for index, layer in enumerate(data.get("layers") or []):
        layer = dict(layer or {})
        kind = str(layer.get("type") or "")
        # 图层顺序 → z：最下面那层 z 最小（引擎 2D 的 z 越大越靠近相机）
        z = -1.0 + index * 0.1
        if layer.get("visible") is False and not include_hidden:
            # 隐藏图层**整层**丢掉：Tiled 里"隐藏"只是显示开关，但引擎不当它是数据开关，
            # 所以这里必须吵一句 —— 否则"碰撞箱全没了"会变成一个无声的谜（踩过）
            skipped += 1
            ConsoleMessage.printWarning(
                "Tiled 图层 %r（%s）标记为隐藏（visible = false）：已整层跳过。"
                "如果里面放的是碰撞箱这类**数据**，要么在 Tiled 里把眼睛点亮，"
                "要么 fromTiled(..., include_hidden=True)"
                % (layer.get("name"), kind or "?"))
            continue
        if kind == "tilelayer":
            layers.append(tileLayerDict(layer, pixel_size, pixels_per_unit,
                                        tilesets, z=z, cell_size=map_cell_size))
        elif kind == "objectgroup":
            for raw_object in layer.get("objects") or []:
                # 模板实例（``template``）先把模板里的字段并进来，再看可见性 / 转坐标
                raw_object = templateObject(dict(raw_object or {}), base_dir=base_dir,
                                            cache=templates)
                if raw_object.get("visible") is False and not include_hidden:
                    skipped += 1
                    ConsoleMessage.printWarning(
                        "Tiled 对象 %r 标记为隐藏（visible = false）：已跳过"
                        "（要保留请 fromTiled(..., include_hidden=True)）"
                        % (raw_object.get("name") or raw_object.get("type"),))
                    continue
                objects.append(toObject(raw_object, pixel_size, pixels_per_unit,
                                        tilesets, default_type=default_object_type))
        elif kind == "imagelayer":
            sprite = imageLayerDict(layer, pixel_size, pixels_per_unit, textures,
                                    base_dir=base_dir, z=z)
            if sprite is None:
                skipped += 1
            else:
                objects.append(sprite)
        else:
            skipped += 1
            ConsoleMessage.printWarning(
                "Tiled 图层类型 %r（%r）还没支持，已跳过" % (kind, layer.get("name")))

    document = {
        "format_version": FORMAT_VERSION,
        "source": "tiled",
        "name": str(name or data.get("name")
                    or (os.path.basename(base_dir.rstrip("\\/")) if base_dir else "")
                    or "tiled"),
        "pixels_per_unit": float(pixels_per_unit),
        "map": {"width": int(data.get("width") or 0),
                "height": int(data.get("height") or 0),
                "tile_size": [int(data.get("tilewidth") or 0),
                              int(data.get("tileheight") or 0)],
                "pixel_size": [pixel_size[0], pixel_size[1]],
                "orientation": orientation},
        "textures": textures,
        "tilesets": tilesets,
        "layers": layers,
        "objects": objects,
        "stats": {"layers": len(layers), "objects": len(objects), "skipped": skipped},
    }
    if atlases:
        # Tiled 没有"图集"概念：由调用方把图集表补进来（见参数说明）
        document["atlases"] = {str(key): value for key, value in atlases.items()}
    # 物理：Tiled 的"地图属性"（gravity="0,-26" 之类）→ 引擎的 physics 段；
    # 地图里有 Body2D 对象时也会自动建世界（否则碰撞箱全都建不出来）
    physics = physicsFromMap(data, objects=objects)
    if physics is not None:
        document["physics"] = physics
    if with_camera:
        document["camera"] = {
            "projection": "MSimple2D",
            # z = 30：2D 里"z 越大越靠近相机"，相机要站在场景前方（见 SceneRuntime.buildCamera）
            "position": [0.0, 0.0, 30.0],
            "size": [pixel_size[0] / pixels_per_unit,
                     pixel_size[1] / pixels_per_unit],
            "use_lighting": True,
        }
    return document


def fromFile(path: str, **kwargs) -> dict:
    """读盘一步到位：``*.json``（Tiled 导出）→ 引擎场景文档。

        document = tiled.fromFile("res/scene/level1.json")
    """
    from ..resources.Resources import Resources
    from ..ResourceLocation import ResourceLocation

    text = ResourceLocation(path).tryToString()
    data = json.loads(text) if text.strip() else None
    if not isinstance(data, dict):
        raise TiledImportError("不是合法的 Tiled JSON: %s" % path)
    if "base_dir" not in kwargs:
        kwargs["base_dir"] = Resources.dirnamePath(path)
    return toScene(data, **kwargs)
