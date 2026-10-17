"""LibreSprite / Aseprite 的 JSON 图集 → 引擎图集文档（纯数据）。

为什么需要这一层
----------------

LibreSprite（Aseprite 的免费分支）导出的 JSON 是**打包器视角**的：

- 帧是**任意矩形**（``frame: {x, y, w, h}``），不是等分网格 —— 所以引擎原来的
  :class:`~pyglgame.gameobject.animation.IAnimationFrame.IAnimationFrame`（按
  ``rows × columns`` 切）吃不下，需要 :class:`~pyglgame.render.TextureAtlas.TextureAtlas`；
- 帧可能被 **trim**（裁掉透明边）→ 不还原 ``spriteSourceSize`` 就会"整只角色抖动"；
- 帧时长是 **毫秒**；动作（一个角色多个动作）存在 ``meta.frameTags`` 里；
- 坐标系是**左上原点、Y 向下**。

本模块只做"外部 JSON → 引擎图集文档 dict"的翻译，**不碰 GL、不建对象**：

.. code-block:: python

    raw = json.loads(ResourceLocation("res/atlas/hero.json").tryToString())
    atlas = TextureAtlas.fromDict(aseprite.toAtlas(raw, image_path="res/atlas/hero.png"))

支持的导出格式
--------------

============================  ==========================================================
``--format json``（hash）      ``frames`` 是 ``{帧名: {...}}``（LibreSprite 默认）
``--format json-array``        ``frames`` 是 ``[{filename, frame, duration}, ...]``
============================  ==========================================================

两种都吃：:func:`normalizeFrames` 先把它们归一化成 ``[(帧名, 帧数据), ...]``。

⚠ 已知不支持（会**警告**而不是静默出错）：打包器把帧**旋转**存放（``rotated: true``）
的图集。引擎的四边形 UV 不做旋转，请在图集导出时关掉旋转。
"""

from ..xyazhServer.ConsoleMessage import ConsoleMessage

#: 引擎图集文档的格式版本（``TextureAtlas.fromDict`` 会校验）
FORMAT_VERSION = 1

#: 默认"1 世界单位 = 多少像素"（与 ``docs/资源导入管线.md`` 的约定一致）
DEFAULT_PPU = 32.0

#: 帧没写 duration 时的兜底（毫秒）
DEFAULT_DURATION_MS = 100.0

_DIRECTIONS = ("forward", "reverse", "pingpong")


# === 归一化 ===

def normalizeFrames(data: dict) -> list:
    """把两种 ``frames`` 结构统一成 ``[(帧名, 帧数据), ...]``（**保持 JSON 顺序**）。

    ``json-array`` 没有名字时会用 ``#0`` / ``#1`` … 兜底；``filename`` 里的
    ``hero 0.ase`` 这类名字原样保留（帧名只是给人看的，动画靠顺序索引）。
    """
    frames = (data or {}).get("frames") or {}
    if isinstance(frames, dict):
        return [(str(name), dict(entry or {})) for name, entry in frames.items()]
    result = []
    for index, entry in enumerate(frames):
        entry = dict(entry or {})
        name = entry.get("filename") or entry.get("name") or ("#%d" % index)
        result.append((str(name), entry))
    return result


def normalizeFrameTags(data: dict) -> list:
    """``meta.frameTags`` → ``[{name, from, to, direction}, ...]``（缺字段时补默认值）。"""
    meta = (data or {}).get("meta") or {}
    tags = meta.get("frameTags") or []
    result = []
    for tag in tags:
        tag = dict(tag or {})
        direction = str(tag.get("direction", "forward")).lower()
        result.append({
            "name": str(tag.get("name", "")),
            "from": int(tag.get("from", 0)),
            "to": int(tag.get("to", 0)),
            "direction": direction if direction in _DIRECTIONS else "forward",
        })
    return result


# === 单帧读数 ===

def frameRect(entry: dict) -> tuple:
    """帧在图集里的矩形 ``(x, y, w, h)``（左上原点、Y 向下）。"""
    rect = (entry or {}).get("frame") or {}
    return (int(rect.get("x", 0)), int(rect.get("y", 0)),
            int(rect.get("w", 0)), int(rect.get("h", 0)))


def frameSourceSize(entry: dict, rect: tuple = None) -> tuple:
    """帧的**原始尺寸** ``(W, H)``（trim 之前；缺字段时退回 ``rect`` 的宽高）。"""
    rect = rect if rect is not None else frameRect(entry)
    size = (entry or {}).get("sourceSize") or {}
    width = int(size.get("w", 0)) or int(rect[2])
    height = int(size.get("h", 0)) or int(rect[3])
    return (width, height)


def frameSourceRect(entry: dict, rect: tuple = None) -> tuple:
    """**内容块**在原始帧里的矩形 ``(sx, sy, sw, sh)``（= ``spriteSourceSize``）。

    这是 trim 还原的关键：``spriteSourceSize`` 告诉引擎"这块图本该贴在原帧的哪里"。
    没有 trim 时它就是整帧 ``(0, 0, W, H)``。
    """
    rect = rect if rect is not None else frameRect(entry)
    width, height = frameSourceSize(entry, rect)
    if not (entry or {}).get("trimmed"):
        return (0, 0, width, height)
    source = (entry or {}).get("spriteSourceSize") or {}
    sw = int(source.get("w", 0)) or rect[2]
    sh = int(source.get("h", 0)) or rect[3]
    return (int(source.get("x", 0)), int(source.get("y", 0)), sw, sh)


def frameDuration(entry: dict) -> float:
    """帧时长（**毫秒**；LibreSprite 的单位就是这个，缺省 100ms）。"""
    value = (entry or {}).get("duration")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DURATION_MS
    return value if value > 0.0 else DEFAULT_DURATION_MS


def slicePivot(data: dict) -> tuple:
    """从 ``meta.slices`` 里尽力读出 pivot（像素，相对帧左上角，Y 向下）。

    Aseprite/LibreSprite 的"轴心"存在 slice 里（``slices[].keys[].pivot``），而且
    它的坐标是**相对 slice 边界**的 —— 要加回 ``bounds.x/y`` 才是"相对帧左上角"。

    读不到（没画过轴心 / 老版本导出）返回 ``None``，由 :func:`toAtlas` 兜底成
    "脚底中心"。
    """
    meta = (data or {}).get("meta") or {}
    for slice_data in meta.get("slices") or []:
        for key in (slice_data or {}).get("keys") or []:
            pivot = (key or {}).get("pivot")
            if not pivot:
                continue
            bounds = (key or {}).get("bounds") or {}
            # slice 的 pivot 是"相对 slice 边界"的像素 → 加回边界偏移
            return (float(bounds.get("x", 0)) + float(pivot.get("x", 0)),
                    float(bounds.get("y", 0)) + float(pivot.get("y", 0)))
    return None


def imageSize(data: dict) -> tuple:
    """``meta.size`` → ``(w, h)``（缺字段返回 ``(0, 0)``）。"""
    size = ((data or {}).get("meta") or {}).get("size") or {}
    return (int(size.get("w", 0)), int(size.get("h", 0)))


def imagePath(data: dict, directory: str = "") -> str:
    """``meta.image`` → 图集 PNG 的路径（相对 ``directory`` 解析）。

    ``directory`` 可以是**逻辑路径**（``res/atlas``）—— 见
    :mod:`pyglgame.resources.Resources`，这样目录形态与资源包形态都能用。
    """
    from ..resources.Resources import Resources
    name = ((data or {}).get("meta") or {}).get("image")
    if not name:
        return ""
    return Resources.joinPath(directory, str(name)) if directory else str(name)


# === 动作（frameTags）===

def resolveTagFrames(tag: dict, frames: list) -> list:
    """一个 tag（``from`` / ``to`` 是**闭区间索引**）→ 帧名列表，含方向展开。

    - ``forward``：原样；
    - ``reverse``：整体反序；
    - ``pingpong``：来回（``a b c b``，端点不重复）—— 引擎的帧动画只有"正向 + 翻转"，
      所以方向在这里**烘**进帧序。
    """
    count = len(frames)
    if count <= 0:
        return []
    start = max(0, min(int(tag.get("from", 0)), count - 1))
    end = max(0, min(int(tag.get("to", 0)), count - 1))
    if end < start:
        start, end = end, start
    names = [frames[index][0] for index in range(start, end + 1)]
    direction = str(tag.get("direction", "forward")).lower()
    if direction == "reverse":
        names.reverse()
    elif direction == "pingpong" and len(names) > 2:
        names = names + names[-2:0:-1]
    return names


def framesFps(entries: list, names: list, default: float = 10.0) -> float:
    """按帧时长（**毫秒**）算帧率：``1000 / 平均时长``。

    ⚠ 引擎的帧动画是"等时长切帧"，所以逐帧时长不一致时这里取平均 —— 想精确还原就得
    把每帧拆成单帧动作，或者干脆让时长保持一致（推荐）。
    """
    if not names:
        return float(default)
    wanted = set(names)
    durations = [frameDuration(entry) for name, entry in entries if name in wanted]
    if not durations:
        return float(default)
    average = sum(durations) / len(durations)
    if average <= 0.0:
        return float(default)
    return round(1000.0 / average, 6)


def toAnimations(data: dict, frames: list, warn: bool = True) -> dict:
    """``meta.frameTags`` → 引擎图集文档的 ``animations``（名字 → 帧序列）。

    没有 tags 时给一个 ``default`` 动作（含全部帧）—— 这样"只是想播一组帧"的图集
    也能直接用，而不是"导进来什么都没有"；同时**警告一次**，因为"没打 tag"是
    "我新做的动画怎么只有 default / 没有 idle"这一类问题的头号原因。
    """
    animations = {}
    for tag in normalizeFrameTags(data):
        if not tag["name"]:
            continue
        names = resolveTagFrames(tag, frames)
        if not names:
            continue
        animations[tag["name"]] = {
            "frames": names,
            "fps": framesFps(frames, names),
            "loop": True,                     # pingpong 的方向已经烘进帧序
            "direction": tag["direction"],
        }
    if not animations and frames:
        names = [name for name, _ in frames]
        if warn:
            ConsoleMessage.printWarning(
                "图集里没有 frameTags（动作）：已生成一个 default 动作（含全部 %d 帧）。"
                "想要 idle / walk 这样的动作名，请在 LibreSprite / Aseprite 里用"
                " Tag（标签）面板给帧范围命名，再重新导出。" % len(frames))
        animations["default"] = {"frames": names, "fps": framesFps(frames, names),
                                 "loop": True, "direction": "forward"}
    return animations


# === 主入口 ===

def toAtlas(data: dict, image_path: str = None,
            pixels_per_unit: float = DEFAULT_PPU,
            pivot=None, pivot_unit: str = "pixels",
            warn_rotated: bool = True, warn_no_tags: bool = True) -> dict:
    """LibreSprite / Aseprite 的 JSON → **引擎图集文档**（喂给 ``TextureAtlas.fromDict``）。

    :param data: ``json.loads`` 之后的原始 JSON
    :param image_path: 图集 PNG 路径；``None`` 时用 ``meta.image``
    :param pixels_per_unit: 1 世界单位 = 多少像素（决定帧在世界里的尺寸）
    :param pivot: 帧轴心（相对**帧左上角**的像素，Y 向下）；``None`` = 先看 ``meta.slices``，
        再看"脚底中心"（``W/2, H``）—— 2D 角色最常用的锚点
    :param pivot_unit: ``"pixels"``（默认）或 ``"normalized"``（0~1，按各帧原始尺寸换算）
    :param warn_rotated: 打包器把帧转着存（``rotated: true``）时警告一次
    :param warn_no_tags: 图集里没有 ``frameTags``（没给帧打过 tag）时警告一次 ——
        这种情况只会得到一个 ``default`` 动作，"没有 idle / walk"多半就是这个原因

    每帧带 ``rect`` / ``source_size`` / ``source_rect`` / ``pivot`` / ``duration_ms``：
    ``source_rect`` 就是 ``spriteSourceSize``，**trim 还原全靠它**。
    """
    frames = normalizeFrames(data)
    width, height = imageSize(data)
    source_pivot = pivot if pivot is not None else slicePivot(data)
    pixel_pivot = None
    normalized_pivot = None
    if source_pivot is not None:
        if str(pivot_unit).lower() == "normalized":
            normalized_pivot = (float(source_pivot[0]), float(source_pivot[1]))
        else:
            pixel_pivot = (float(source_pivot[0]), float(source_pivot[1]))

    frame_docs = {}
    order = []
    rotated_warned = False
    for name, entry in frames:
        rect = frameRect(entry)
        source_size = frameSourceSize(entry, rect)
        if entry.get("rotated") and warn_rotated and not rotated_warned:
            rotated_warned = True
            ConsoleMessage.printWarning(
                "图集里有 rotated 帧（打包器把帧转着存）：引擎的四边形 UV 不做旋转，"
                "画面会错位 —— 请在导出图集时关掉旋转。")
        if normalized_pivot is not None:
            frame_pivot = (normalized_pivot[0] * source_size[0],
                           normalized_pivot[1] * source_size[1])
        elif pixel_pivot is not None:
            frame_pivot = pixel_pivot
        else:
            frame_pivot = (source_size[0] * 0.5, float(source_size[1]))    # 脚底中心
        frame_docs[name] = {
            "rect": [rect[0], rect[1], rect[2], rect[3]],
            "source_size": [source_size[0], source_size[1]],
            "source_rect": list(frameSourceRect(entry, rect)),
            "pivot": [float(frame_pivot[0]), float(frame_pivot[1])],
            "duration_ms": frameDuration(entry),
        }
        if entry.get("rotated"):
            frame_docs[name]["rotated"] = True
        order.append(name)
    return {
        "format_version": FORMAT_VERSION,
        "source": "libresprite/aseprite",
        "image": image_path or ((data or {}).get("meta") or {}).get("image") or "",
        "image_size": [width, height],
        "pixels_per_unit": float(pixels_per_unit),
        "order": order,
        "frames": frame_docs,
        "animations": toAnimations(data, frames, warn=warn_no_tags),
    }


def fromFile(path: str, **kwargs) -> dict:
    """读盘一步到位：``*.json`` → 引擎图集文档（``image`` 默认取同目录的 ``meta.image``）。

        atlas = TextureAtlas.fromDict(aseprite.fromFile("res/atlas/hero.json"))
    """
    import json
    from ..resources.Resources import Resources
    from ..ResourceLocation import ResourceLocation

    text = ResourceLocation(path).tryToString()
    data = json.loads(text) if text.strip() else None
    if not isinstance(data, dict):
        raise ValueError("不是合法的 LibreSprite/Aseprite 图集 JSON: %s" % path)
    if "image_path" not in kwargs:
        guess = imagePath(data, Resources.dirnamePath(path))
        if guess:
            kwargs["image_path"] = guess
    return toAtlas(data, **kwargs)
