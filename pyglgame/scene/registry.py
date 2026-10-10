"""类名注册表：场景文档里的 ``type`` → 真正的构造器。

场景文档里"对象"只是一个带 ``type`` 的条目（Tiled 的 ``class`` / ``type``、LibreSprite
的 tag 名），它怎么变成引擎对象？答案就是这张表：

.. code-block:: python

    REGISTRY = {
        "Sprite2D": Sprite2D,
        "TileMapLayer": TileMapLayer,
        "light2D": light2D,        # render/Light.py 的快捷构造
        ...
    }

约定（与 ``docs/资源导入管线.md`` 一致）
-------------------------------------

- ``properties`` 的键 = 构造器的**关键字参数名** → :func:`build` 只是
  ``REGISTRY[type](**kwargs)``，不需要为每个类写映射代码；
- **未知 ``type``**：抛 :class:`UnknownTypeError`（由调用方决定跳过还是中断）；
- **未知属性**：警告一次并**丢弃**（外部工具 / AIGC 常常多写字段）；
- **类型不对**（字符串给到 ``float`` 参数）：**不吞**，让构造器自己抛 —— 早失败胜于画面诡异；
- 注册表里的构造器都必须满足引擎的既有约定：**构造不碰 GL**（``Sprite2D`` /
  ``TileMapLayer`` / ``light2D`` 都是这样，所以"文档 → 对象"能离线跑）。

自定义类：``register("Boss", Boss)`` 之后场景文档里就能写 ``"type": "Boss"``。
"""

import difflib
import inspect
import math
from typing import TYPE_CHECKING

from ..gameobject.GameObject import GameObject
from ..gameobject.Sprite2D import Sprite2D
from ..gameobject.Text import Text
from ..gameobject.TileMapLayer import TileMapLayer
from ..render.LazyTexture import lazyTexture
from ..render.Light import light2D, spot2D, sun2D
from ..xyazhServer.ConsoleMessage import ConsoleMessage

if TYPE_CHECKING:
    from .SceneDocument import SceneDocument

#: 名字 → 构造器（``callable(**kwargs) -> GameObject``）
REGISTRY: dict = {}

#: 文档里**不是**构造参数、由导入器 / 运行时自己消费的键
#:
#: 这些键是**场景格式的通用字段**（几何 / 元数据），不是"某个类的参数" —— 每个类只取
#: 自己认识的那些（``Sprite2D`` 取 ``width`` / ``height``，``light2D`` 取不到就丢掉），
#: 所以它们**不进"未知属性"警告**。真正拼错的属性名照样会被警告出来。
RESERVED_KEYS = frozenset({
    "type", "properties", "shape", "points", "source_id", "atlas", "rig", "clip",
    "animation", "frames", "tile_size", "opacity",
    # ``template``：Tiled 的"这个对象来自模板文件（``*.tx``）"引用 —— 引擎读不到模板内容
    # 本身，但它是**外部工具的元数据**，不该被当成"写错的属性"警告一遍
    "template",
    "x", "y", "z", "r", "width", "height", "name",
})

_PARAMETER_CACHE: dict = {}


class UnknownTypeError(KeyError):
    """场景文档里出现了注册表里没有的 ``type``。"""


def register(name: str, factory=None, override: bool = False):
    """注册一个类名（``register("Boss", Boss)`` 或当装饰器 ``@register("Boss")``）。

    :param override: 重名时是否覆盖（默认 ``False``，重名直接抛错 —— 免得两个类抢一个名字）
    """
    def _register(function):
        key = str(name)
        if key in REGISTRY and not override:
            raise ValueError("类名 %r 已经注册过了（%r）；要覆盖请传 override=True"
                             % (key, REGISTRY[key]))
        REGISTRY[key] = function
        _PARAMETER_CACHE.pop(key, None)
        return function
    if factory is None:
        return _register
    return _register(factory)


def unregister(name: str) -> bool:
    """注销一个类名，返回是否真的删掉了。"""
    _PARAMETER_CACHE.pop(str(name), None)
    return REGISTRY.pop(str(name), None) is not None


def resolve(name: str):
    """取构造器；没有这个类名时返回 ``None``。"""
    return REGISTRY.get(str(name))


def names() -> list:
    """已注册的全部类名（排过序，方便打印）。"""
    return sorted(REGISTRY)


def parametersFor(name: str):
    """构造器的关键字参数名集合；``None`` = 它接受任意 kwargs（有 ``**kwargs``）。"""
    key = str(name)
    if key in _PARAMETER_CACHE:
        return _PARAMETER_CACHE[key]
    factory = REGISTRY.get(key)
    if factory is None:
        return None
    # ⚠ 对**类**要看 ``__init__`` 而不是类的签名：像 ``Sprite2D`` 这种
    # ``__new__(cls, *args, **kwargs)`` 的写法会让类签名的 ``**kwargs`` 把
    # "接受任意关键字" 误判出来（那样属性校验就完全不生效了 —— 踩过）。
    target = factory
    if inspect.isclass(factory):
        target = getattr(factory, "__init__", factory)
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return None
    parameters = set()
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            _PARAMETER_CACHE[key] = None
            return None
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY):
            parameters.add(parameter.name)
    _PARAMETER_CACHE[key] = parameters
    return parameters


def parameterNames(name: str):
    """工厂的**具名**参数集合（忽略 ``**kwargs``）；取不到签名时返回 ``None``。

    与 :func:`parametersFor` 的区别：那个用 ``None`` 表示"接受任意关键字"（用于**属性过滤**），
    这个专门用来判断"某个 key 能不能注入"—— ``world`` / ``textures`` / ``document`` 这类
    上下文只该给"签名里明确写了"的工厂（``body2D`` 有 ``**kwargs``，但它确实要 ``world``）。
    """
    factory = REGISTRY.get(str(name))
    if factory is None:
        return None
    target = factory
    if inspect.isclass(factory):
        target = getattr(factory, "__init__", factory)
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return None
    return {parameter.name for parameter in signature.parameters.values()
            if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                  inspect.Parameter.KEYWORD_ONLY)}


def propertyHints(unknown, allowed) -> str:
    """拼错的属性名猜一个"是不是想写"（``friction`` → ``friction``）。

    返回可以直接拼进警告尾巴的字符串（没有像的就返回 ``""``）。属性名拼错是**最常见**的
    一类问题，而"静默丢掉"是最坑的表现形式 —— 所以警告里带上猜测。
    """
    hints = []
    for key in sorted(unknown or ()):
        close = difflib.get_close_matches(str(key), sorted(allowed or ()), n=1,
                                          cutoff=0.6)
        if close:
            hints.append("%s → %s?" % (key, close[0]))
    return "（是不是想写：%s）" % "、".join(hints) if hints else ""


def resolveTexturePath(value, textures: dict = None, document=None) -> str:
    """文档里的贴图引用（名字 / 路径 / 现成贴图）→ 真实文件路径。

    - ``str``：先查 ``{名字: 路径}``（这里没查着就当**路径**，按"场景目录 → 项目根 →
      ``res``"依次找，见 :meth:`SceneDocument.resolveAsset`）；
    - ``LazyTexture`` / 别的贴图对象：取它的 ``path``；
    - 其余（``None`` / 现成 ``Texture``）原样返回 —— 调用方自己判断真假。
    """
    if isinstance(value, str):
        found = (textures or {}).get(value)
        if found is not None:
            return found
        return document.resolveAsset(value) if document is not None else value
    path = getattr(value, "path", None)
    return path if isinstance(path, str) else value


def colorImagePaths(options: dict, textures: dict = None, document=None) -> list:
    """条目里可能当"颜色贴图"用的路径，**按优先级**：

    ``texture`` → ``frame_texture``（精灵 / 瓦片层的覆盖贴图）→ ``atlas``（图集）→
    ``tileset``（瓦片图块集）。这一串就是"材质贴图该跟谁配对"的答案。
    """
    paths = []
    for key in ("texture", "frame_texture"):
        path = resolveTexturePath(options.get(key), textures, document)
        if isinstance(path, str) and path:
            paths.append(path)
    for key in ("atlas", "tileset"):
        path = getattr(options.get(key), "image_path", None)
        if isinstance(path, str) and path:
            paths.append(path)
    return paths


def autoMaterialOptions(name: str, options: dict, textures: dict = None,
                        document=None) -> dict:
    """场景条目：没显式写法线 / 高光贴图时，按**颜色贴图**自动补上（懒上传）。

    配对规则（``_normal`` / ``_n`` / ``_lightmap`` / ``_specular`` / ``_l``）见
    ``pyglgame/render/MaterialMaps.py`` —— 也就是"``hero.png`` 旁边有 ``hero_l.png``
    就自动当高光图"这条约定。

    三种"以文档为准、不自动补"的情况：

    1. 目标构造器的**签名里没有** ``normal_map`` / ``specular_map``（例如 ``light2D`` /
       ``GameObject`` / ``Body2D``）—— 用 :func:`parameterNames` 看"明确写了"的那些，
       ``**kwargs`` 不算（否则它们会一路漏到别的构造器上）；
    2. 条目给了 ``material=``（显式材质说了算）；
    3. 条目**写了那两个键**：写路径 = 用它；写 ``null`` = "旁边有也不要"。

    颜色贴图按 :func:`colorImagePaths` 的顺序找：第一张旁边配到的先填，缺的键再往后找 ——
    所以"瓦片层用 ``texture`` 覆盖了贴图"这种写法也能各自配到自己的配对图。
    """
    accepts = parameterNames(name)
    missing = [key for key in ("normal_map", "specular_map") if key not in options]
    if not accepts or not missing or not (set(missing) & accepts):
        return options
    if options.get("material") is not None:
        return options
    from ..render import MaterialMaps

    for image in colorImagePaths(options, textures, document):
        found = MaterialMaps.materialMapsFor(image)
        for key in list(missing):
            path = found.get(key) if key in accepts else None
            if path:
                options[key] = lazyTexture(path)
                missing.remove(key)
        if not missing:
            break
    return options


def build(name: str, kwargs: dict, textures: dict = None, document=None,
          strict: bool = False, context: dict = None):
    """按 ``name`` 造一个对象：``REGISTRY[name](**kwargs)``（带属性校验与贴图解析）。

    :param kwargs: 构造参数（通常是文档里的 ``properties`` + 几何字段）
    :param textures: ``{名字: 路径}``（文档里的贴图表），命中时换成懒上传贴图
    :param strict: 未知属性是"抛错"（``True``）还是"警告后丢弃"（``False``，默认）
    :param context: 运行时准备好的"共享上下文"，例如 ``{"world": Physics2D(...)}`` ——
        只有工厂签名里**明确有**这个参数时才会注入（``Body2D`` 就需要它）
    :raises UnknownTypeError: ``name`` 不在注册表里
    """
    factory = REGISTRY.get(str(name))
    if factory is None:
        raise UnknownTypeError("没有注册过的类名 %r（已注册: %s）"
                               % (name, ", ".join(names())))
    options = dict(kwargs or {})
    options.pop("type", None)
    # ``name`` 是每个条目都可能有的**元数据**（对象名），不算"未知属性"：
    # 构造器收就传下去（``Sprite2D`` / ``TileMapLayer`` / ``AtlasSprite`` 都收），
    # 不收（``light2D`` 这种工厂）就丢掉 —— 否则 strict 模式会把一个正常场景判成错。
    entry_name = options.pop("name", None)
    # ``properties`` 是"自定义属性容器"：展开成构造参数（文档里的显式字段优先）
    properties = options.pop("properties", None)
    if isinstance(properties, dict):
        merged = dict(properties)
        merged.update(options)
        options = merged
    textures = textures or {}
    # 贴图引用：字符串 → 懒上传贴图（构造不碰 GL，第一次绘制才上传）。
    # ``normal_map`` / ``specular_map`` 也在内：材质贴图同样可以按路径写。
    # 名字先在 ``textures`` 表里查；查不到就按"场景目录 → 项目根 → res"依次解析
    for key in ("texture", "frame_texture", "normal_map", "specular_map"):
        value = options.get(key)
        if isinstance(value, str):
            options[key] = lazyTexture(resolveTexturePath(value, textures, document))
    # 自动材质：条目没写法线 / 高光时，在颜色贴图旁边找 ``_normal`` / ``_n`` /
    # ``_lightmap`` / ``_l``（见 :func:`autoMaterialOptions` 与 render/MaterialMaps.py）
    options = autoMaterialOptions(name, options, textures, document)
    allowed = parametersFor(name)
    # 上下文注入：``world``（物理世界）、``textures`` / ``document`` 这类
    # "运行时准备好、要交给工厂"的东西。判断能不能注入用 :func:`parameterNames`
    # （看**具名**参数）—— 不能用 ``allowed``：它用 ``None`` 表示"接受任意关键字"，
    # 而 ``body2D`` 正是"有 **kwargs 但确实要 world"的那种（踩过）。
    inject_names = parameterNames(name)
    if inject_names:
        inject = {"textures": textures, "document": document}
        if context:
            inject.update(context)
        for key, value in inject.items():
            if value is not None and key not in options and key in inject_names:
                options[key] = value
    if allowed is not None:
        unknown = [key for key in options
                   if key not in allowed and key not in RESERVED_KEYS]
        if unknown:
            message = ("类 %r 不认识这些属性（已丢弃）: %s；它接受 %s%s"
                       % (name, ", ".join(sorted(unknown)), ", ".join(sorted(allowed)),
                          propertyHints(unknown, allowed)))
            if strict:
                raise TypeError(message)
            ConsoleMessage.printWarning(message)
        options = {key: value for key, value in options.items()
                   if key in allowed}
    if entry_name is not None and (allowed is None or "name" in allowed):
        options["name"] = entry_name
    return factory(**options)


def createFrom(entry: dict, textures: dict = None, document=None,
               strict: bool = False, context: dict = None):
    """文档里的一个条目 → 对象（``type`` 必填）。"""
    if not isinstance(entry, dict):
        raise UnknownTypeError("场景条目必须是 dict，收到 %r" % (type(entry).__name__,))
    name = entry.get("type")
    if not name:
        raise UnknownTypeError("场景条目没有 'type': %r" % (entry,))
    return build(str(name), entry, textures=textures, document=document,
                 strict=strict, context=context)


# === 内置注册（引擎现有的、可以直接"摆到场景里"的类）===

def atlasSprite(atlas=None, animation=None, fps: float = None, scale=(1.0, 1.0),
                color=(1.0, 1.0, 1.0, 1.0), blend_mode: str = None, speed: float = 1.0,
                loop: bool = True, autoplay: bool = True, pivot=None, name: str = None,
                x: float = 0.0, y: float = 0.0, z: float = 0.0, r: float = 0.0,
                transparent: bool = True, material=None,
                normal_map=None, specular_map=None, normal_strength: float = 1.0,
                specular_color=(1.0, 1.0, 1.0), specular_strength: float = 1.0,
                shininess: float = 32.0, specular_contrast: float = 1.0,
                specular_mode: str = "highlight", receive_shadow: bool = True,
                lighting_mode: str = "full", **kwargs):
    """场景里的"图集精灵"：``IAtlasAnimation`` + ``GameObject``（**动画自己画**）。

    给"场景文档里既要贴图、又要播帧动画"的场合用：

    .. code-block:: json

        {"type": "AtlasSprite", "atlas": "chr", "animation": "idle",
         "x": 0.0, "y": -1.5,
         "properties": {"normal_map": "res/atlas/chr_normal.png",
                        "specular_map": "res/atlas/chr_specular.png",
                        "shininess": 48.0}}

    - ``atlas``：:class:`~pyglgame.render.TextureAtlas.TextureAtlas` 或它的文档 dict
      （``SceneRuntime`` 会把文档里的名字先解析成对象）；
    - ``animation``：动作名 / 帧名列表；
    - **``material`` 或 ``normal_map`` / ``specular_map``**：想吃 2D 光照就用它们
      （值给路径字符串也行，``registry.build`` 会包成懒上传贴图）。
      ⚠ 法线 / 高光贴图必须是**和图集同布局的图集**（帧矩形一一对应），
      生成办法见 ``docs/2D帧动画与场景导入.md`` 的"给图集加法线 / 高光"；
    - 位置走 ``GameObject`` 的 ``x`` / ``y`` / ``z`` / ``r``，顶点是 pivot 对齐的
      本地坐标（所以"脚底在原点"这类锚点自动成立）。
    """
    from ..gameobject.animation import IAtlasAnimation

    if isinstance(atlas, dict):
        from ..render.TextureAtlas import TextureAtlas
        atlas = TextureAtlas.fromDict(atlas)
    if atlas is None:
        raise ValueError("AtlasSprite 需要 atlas（图集文档 dict 或图集名）")
    if material is None and (normal_map is not None or specular_map is not None
                             or str(lighting_mode).strip().lower() not in
                             ("full", "default", "all", "on")):
        from ..render.Lighting2D import Material2D
        material = Material2D(normal_map=normal_map, specular_map=specular_map,
                              normal_strength=normal_strength,
                              specular_color=specular_color,
                              specular_strength=specular_strength,
                              shininess=shininess,
                              specular_contrast=specular_contrast,
                              specular_mode=specular_mode,
                              receive_shadow=receive_shadow,
                              lighting_mode=lighting_mode)

    class AtlasSprite(IAtlasAnimation, GameObject):
        """``IAtlasAnimation`` + ``GameObject``（见 :func:`atlasSprite`）。"""

        def __new__(cls, *args, **kwargs):
            # ⚠ ``GameObject.__new__`` 只认 ``is_camera``：构造参数必须在这里吃掉，
            # 否则 ``AtlasSprite(atlas=…)`` 会在 ``__new__`` 上炸（与 ``Sprite2D`` 同理）
            return super().__new__(cls)

    sprite = AtlasSprite(atlas=atlas, animation=animation, fps=fps, scale=scale,
                         color=color, blend_mode=blend_mode, speed=speed, loop=loop,
                         autoplay=autoplay, pivot=pivot, material=material, name=name)
    # 宿主参数构造后再设：``GameObject.__init__`` 不吃关键字参数（引擎的既有约定）
    sprite.x, sprite.y, sprite.z, sprite.r = float(x), float(y), float(z), float(r)
    sprite.transparent = bool(transparent)
    return sprite


#: 形状描述里可以出现的**材质**键（``shapes`` 的每一项都能覆盖刚体的缺省值）
SHAPE_MATERIAL_KEYS = ("density", "friction", "restitution", "sensor",
                       "category", "mask", "group")

#: 支持的形状类型
SHAPE_KINDS = ("box", "circle", "polygon", "chain", "edge")

#: 外观的**材质参数**：写在 ``Body2D`` 条目这一层（或 ``sprite`` 里）都能生效 ——
#: 它们最终都并进外观 ``Sprite2D`` 的材质（``lighting_mode`` / ``receive_shadow`` /
#: 法线高光贴图 …）。Tiled 里给**图层**写 ``lighting_mode`` 是另一条路（见 TileMapLayer）。
_MATERIAL_PARAM_KEYS = ("normal_map", "specular_map", "normal_strength",
                        "specular_color", "specular_strength", "specular_contrast",
                        "shininess", "specular_mode", "receive_shadow",
                        "lighting_mode")


def shapeList(shapes=None, kind: str = None, **defaults) -> list:
    """把 ``shapes`` 列表 + "单形状简写" 统一成形状描述列表（纯数据，好测）。

    - ``shapes``：``[{"kind": "box", "width": 0.7, "height": 1.4}, …]``；
    - 单形状简写：直接写 ``kind="box" width=… height=…``（等价于只有一个形状的列表）；
    - 两者都没写 → 给一个 **1×1 的方块**（比"建个没有碰撞箱的空刚体"更符合直觉）。
    """
    result = []
    if shapes:
        if isinstance(shapes, dict):
            shapes = [shapes]
        for spec in shapes:
            result.append(dict(spec or {}))
    has_inline = bool(kind) or any(defaults.get(key) is not None
                                   for key in ("width", "height", "radius", "points"))
    if has_inline:
        spec = {"kind": kind or "box"}
        for key, value in defaults.items():
            if value is not None:
                spec[key] = value
        result.append(spec)
    if not result:
        result.append({"kind": "box", "width": 1.0, "height": 1.0})
    return result


def addShape(body, spec: dict):
    """按描述给 ``Body2D`` 加一个形状，返回 ``Fixture2D``。

    ``spec["kind"]``（也认 ``shape``）：``box`` / ``circle`` / ``polygon`` / ``chain`` /
    ``edge``；每一项都能带自己的材质（``density`` / ``friction`` / ``restitution`` /
    ``sensor`` / ``category`` / ``mask`` / ``group``）与 ``offset`` / ``angle``。
    """
    spec = dict(spec or {})
    shape_kind = str(spec.get("kind") or spec.get("shape") or "box").lower()
    material = {key: spec[key] for key in SHAPE_MATERIAL_KEYS if key in spec}
    offset = spec.get("offset") or (0.0, 0.0)
    angle = float(spec.get("angle") or 0.0)
    if shape_kind in ("box", "rect", "rectangle"):
        return body.addBox(float(spec.get("width", 1.0)),
                           float(spec.get("height", 1.0)),
                           offset=offset, angle=angle, **material)
    if shape_kind in ("circle", "ball"):
        return body.addCircle(float(spec.get("radius", 0.5)),
                              offset=offset, **material)
    if shape_kind in ("polygon", "poly"):
        return body.addPolygon(spec.get("points") or [], **material)
    if shape_kind == "chain":
        return body.addChain(spec.get("points") or [],
                             loop=bool(spec.get("loop")), **material)
    if shape_kind == "edge":
        start = spec.get("start") or spec.get("from")
        end = spec.get("end") or spec.get("to")
        if start is None or end is None:
            raise ValueError("edge 形状需要 start / end 两点：%r" % (spec,))
        return body.addEdge(start, end, **material)
    raise ValueError("不认识的形状类型 %r（支持 %s）"
                     % (shape_kind, "、".join(SHAPE_KINDS)))


def _firstBoxSize(shape_specs) -> tuple:
    """从形状描述里取第一个 box 的尺寸（外观 / 遮挡体没写尺寸时用它兜底）。"""
    for spec in shape_specs or ():
        kind = str(spec.get("kind") or spec.get("shape") or "box").lower()
        if kind in ("box", "rect", "rectangle"):
            return (float(spec.get("width", 1.0)), float(spec.get("height", 1.0)))
    return (1.0, 1.0)


def bodySpriteObject(shape_specs, texture=None, sprite=None, textures=None,
                     document=None, default_z: float = 0.4):
    """造"跟着刚体走"的**外观**对象（给了 ``texture`` 才是 ``Sprite2D``，否则 ``None``）。

    ``texture`` 可以是贴图表里的名字、相对路径、或现成的 ``Texture`` / ``LazyTexture``；
    ``sprite`` 可以覆盖尺寸 / ``z`` / ``color`` / ``uv`` / ``transparent``；
    尺寸缺省时按**第一个 box 形状**的宽高来（碰撞箱多大，精灵就多大）。
    """
    from ..gameobject.Sprite2D import Sprite2D

    spec = dict(sprite or {})
    if texture is not None:
        spec.setdefault("texture", texture)
    if not spec:
        return None
    default_width, default_height = _firstBoxSize(shape_specs)
    key = spec.pop("texture", None)
    texture_object = None
    color_path = None
    if key is not None:
        if isinstance(key, str):
            color_path = (textures or {}).get(key)
            if color_path is None:
                color_path = document.resolveAsset(key) if document is not None else key
            texture_object = lazyTexture(color_path)
        else:
            texture_object = key                     # 已经是 Texture / LazyTexture
            color_path = getattr(key, "path", None)
    material = spec.pop("material", None)
    material_params = {key: spec.pop(key) for key in _MATERIAL_PARAM_KEYS
                       if key in spec}
    if material is None:
        from ..render import MaterialMaps
        from ..render.Lighting2D import Material2D

        # 自动材质：颜色贴图旁边有 ``_normal`` / ``_lightmap`` 就顺手挂上；
        # 显式写的贴图 / 光照模式（``lighting_mode``）也一起建 —— 不能只挑贴图，
        # 否则 ``lighting_mode="none"`` 会被 ``material=`` 这条路径悄悄吃掉
        maps = MaterialMaps.materialMapsFor(
            color_path,
            normal_map=material_params.pop("normal_map", None),
            specular_map=material_params.pop("specular_map", None))
        if maps or material_params:
            for key in ("normal_map", "specular_map"):
                if key in maps:
                    maps[key] = MaterialMaps.asTexture(maps[key])
            material = Material2D(**maps, **material_params)
    return Sprite2D(texture_object,
                    material=material,
                    width=float(spec.pop("width", default_width)),
                    height=float(spec.pop("height", default_height)),
                    z=float(spec.pop("z", default_z)),
                    color=tuple(spec.pop("color", (1.0, 1.0, 1.0, 1.0))),
                    uv=tuple(spec.pop("uv", (0.0, 0.0, 1.0, 1.0))),
                    transparent=bool(spec.pop("transparent", False)),
                    name=spec.pop("name", None),
                    **spec)


def attachAppearance(body, shape_specs, texture=None, sprite=None, caster=None,
                     textures=None, document=None, offset_y: float = None,
                     caster_offset_y: float = None, follow_rotation: bool = None,
                     name: str = None):
    """把"外观 + 阴影遮罩体"挂到刚体上（见 :func:`body2D` 的 ``texture`` / ``caster``）。

    - **有外观** → 返回 :class:`~pyglgame.gameobject.BodySprite.BodySprite`
      （每帧把物理位姿抄给精灵与遮挡体）；
    - **没外观** → 原样返回刚体（地形 / 传感器一个字节都不多）。
    """
    sprite_object = bodySpriteObject(shape_specs, texture=texture, sprite=sprite,
                                     textures=textures, document=document)
    if sprite_object is None:
        return body
    caster_object = None
    if caster:
        cfg = dict(caster) if isinstance(caster, dict) else {}
        width, height = _firstBoxSize(shape_specs)
        caster_object = shadowCaster2D(shape=cfg.get("kind", "rect"),
                                       width=cfg.get("width", width),
                                       height=cfg.get("height", height),
                                       radius=cfg.get("radius"),
                                       segments=cfg.get("segments", 16),
                                       points=cfg.get("points"),
                                       inset=cfg.get("inset", 0.02),
                                       z=cfg.get("z", 0.0),
                                       length=cfg.get("length"),
                                       cast_shadow=cfg.get("cast_shadow", True),
                                       outline=cfg.get("outline", False),
                                       name="%s_shadow" % name if name else None)
    from ..gameobject.BodySprite import BodySprite

    return BodySprite(body, sprite_object, caster=caster_object,
                      offset_y=0.0 if offset_y is None else float(offset_y),
                      caster_offset_y=caster_offset_y,
                      follow_rotation=True if follow_rotation is None else follow_rotation,
                      name=name)


def body2D(world=None, body_type: str = "dynamic", x: float = 0.0, y: float = 0.0,
           z: float = 0.0, r: float = 0.0, shapes=None,
           kind: str = None, width: float = None, height: float = None,
           radius: float = None, points=None, loop: bool = False,
           offset=(0.0, 0.0), shape_angle: float = 0.0,
           density: float = None, friction: float = None, restitution: float = None,
           sensor: bool = None, category: int = None, mask: int = None,
           group: int = None, fixed_rotation: bool = False, bullet: bool = False,
           gravity_scale: float = 1.0, linear_damping: float = 0.0,
           angular_damping: float = 0.0, awake: bool = True, name: str = None,
           texture=None, sprite=None, caster=None, sprite_offset_y: float = None,
           caster_offset_y: float = None, follow_rotation: bool = None,
           textures=None, document=None, **kwargs):
    """场景里的**碰撞箱 / 刚体**：``Body2D``（Box2D）+ 一个或多个形状。

    横板 ACT 最常用的三件事都在这里：**地面 / 平台是 static**、**角色是 dynamic**、
    **重力**由世界的 ``physics`` 段给（``{"gravity": [0.0, -22.0]}``）。

    .. code-block:: json

        {"type": "Body2D", "name": "ground", "body_type": "static", "y": -2.0,
         "shapes": [{"kind": "box", "width": 40.0, "height": 0.5, "friction": 0.8}]}

        {"type": "Body2D", "name": "player", "body_type": "dynamic", "x": 0.0, "y": 2.0,
         "fixed_rotation": true,          // 横板角色不翻滚
         "shapes": [{"kind": "box", "width": 0.7, "height": 1.4, "friction": 0.2}]}

    也可以只写一个形状（简写）：``"kind": "box", "width": 1.0, "height": 0.5``；
    什么都不写就是 **1×1 的方块**。``r`` 是**度**（引擎约定），内部转成 Box2D 的弧度。

    **带外观 / 阴影遮罩体**（横板 ACT 最常用）：

    .. code-block:: json

        {"type": "Body2D", "name": "player", "x": 0.0, "y": 0.6, "fixed_rotation": true,
         "shapes": [{"kind": "box", "width": 0.7, "height": 1.4}],
         "texture": "tiles",                              // 贴图表里的名字 / 路径 / 现成纹理
         "sprite": {"z": 0.5, "uv": [0.0, 0.5, 0.25, 1.0]},
         "caster": {"inset": 0.02, "length": 80.0}}       // 顺手挂一个阴影遮罩体

    这时返回的是 :class:`~pyglgame.gameobject.BodySprite.BodySprite`
    （精灵与遮挡体**每帧跟着刚体走**，角度从弧度转成度）；**不写外观就是纯碰撞箱**
    （返回 ``Body2D``）。外观尺寸缺省 = 第一个 box 形状的宽高（碰撞箱多大、精灵多大）。

    ⚠ 需要 ``world``：场景文档里加 ``"physics": {...}``（``SceneRuntime`` 会把它注入进来），
    或者自己 ``Physics2D(...)`` 之后用代码建。Box2D 后端只有 **Python 3.10** 能加载
    （``pyglgame/libs/Box2D``），别的解释器上建世界会抛 :class:`PhysicsUnavailable`。
    """
    from ..physics.Body2D import Body2D

    # 外观的**材质参数**写在 Body2D 这一层也能生效（``lighting_mode`` / ``receive_shadow`` /
    # 法线高光贴图 …）：统一并进外观规格里，免得它们变成"不认识这些属性"被丢掉
    forwarded = {key: kwargs.pop(key) for key in _MATERIAL_PARAM_KEYS if key in kwargs}
    if forwarded:
        sprite = dict(sprite) if isinstance(sprite, dict) else {}
        for key, value in forwarded.items():
            sprite.setdefault(key, value)

    if kwargs:
        # ``body2D`` 带 ``**kwargs``（构造器集合散得比较开），但没人接的东西**静默丢掉**
        # 是最坑的 —— ``friction`` / ``bod_type`` 这类拼错会让人以为"参数生效了"。
        # ⚠ 先排除场景格式的保留键（``shape`` / ``source_id`` / ``template`` 这些是导入器
        #   自己填的元数据，不是"写错的属性"）
        leftovers = [key for key in kwargs if key not in RESERVED_KEYS]
        if leftovers:
            ConsoleMessage.printWarning(
                "Body2D 不认识这些属性（已丢弃）: %s%s"
                % (", ".join(sorted(leftovers)),
                   propertyHints(leftovers, parameterNames("Body2D") or ())))
    if world is None:
        raise ValueError(
            "Body2D 需要物理世界：给场景文档加一个 physics 段"
            '（例如 "physics": {"gravity": [0.0, -22.0]}），'
            "或者自己 Physics2D(...) 之后用代码建 Body2D")
    options = {"body_type": str(body_type), "x": float(x), "y": float(y),
               "z": float(z),
               # ⚠ 场景里角度写的是**度**（引擎约定），Box2D 要弧度
               "angle": math.radians(float(r)),
               "fixed_rotation": bool(fixed_rotation), "bullet": bool(bullet),
               "gravity_scale": float(gravity_scale),
               "linear_damping": float(linear_damping),
               "angular_damping": float(angular_damping), "awake": bool(awake),
               "name": name}
    for key, value in (("density", density), ("friction", friction),
                       ("restitution", restitution), ("sensor", sensor),
                       ("category", category), ("mask", mask), ("group", group)):
        if value is not None:
            options[key] = value
    body = Body2D(world, **options)
    specs = shapeList(shapes, kind, width=width, height=height, radius=radius,
                      points=points, loop=loop, offset=offset,
                      angle=shape_angle, density=density, friction=friction,
                      restitution=restitution, sensor=sensor, category=category,
                      mask=mask, group=group)
    for spec in specs:
        addShape(body, spec)
    # 可选外观：给了 ``texture`` / ``sprite`` 就顺手挂一个"跟着物理体走"的精灵
    #（有外观时返回 :class:`~pyglgame.gameobject.BodySprite.BodySprite`；
    #  纯地形/纯传感器不传它，返回的还是 ``Body2D`` —— 一个字节都不多）
    return attachAppearance(body, specs, texture=texture, sprite=sprite, caster=caster,
                            textures=textures, document=document,
                            offset_y=sprite_offset_y, caster_offset_y=caster_offset_y,
                            follow_rotation=follow_rotation, name=name)


def shadowCaster2D(shape: str = "rect", width: float = None, height: float = None,
                   radius: float = None, segments: int = 16, points=None,
                   inset: float = 0.0, x: float = 0.0, y: float = 0.0, z: float = 0.0,
                   r: float = 0.0, length: float = None, cast_shadow: bool = True,
                   outline: bool = False, name: str = None, **kwargs):
    """场景里的**阴影遮罩体**（:class:`~pyglgame.gameobject.ShadowCaster2D.ShadowCaster2D`）：
    它挡住光，让**接收层**（背景 / 地面）在它的背光侧变暗。

    .. code-block:: json

        {"type": "ShadowCaster2D", "name": "rock_shadow", "shape": "rect",
         "width": 1.0, "height": 1.0, "x": 0.0, "y": 0.0, "z": 0.2}
        {"type": "ShadowCaster2D", "shape": "circle", "radius": 0.6,
         "x": 2.0, "y": 1.0, "length": 60.0}

    - ``shape``：``"rect"``（默认，用 ``width`` / ``height``，``inset`` 往内缩一点）/
      ``"circle"``（``radius`` / ``segments``）/ ``"polygon"``（``points``）；
    - ``length``：沿光方向扫出的长度（给够场景尺度，给不足阴影会"断"在半路）；
    - 需要相机 ``use_shadow2d``，并且场景里有 ``cast_shadow=True`` 的光源
      （``light2D`` / ``sun2D`` / ``spot2D`` 都行）；遮挡体自己通常给一个中间的 ``z``
      （背景与物体之间），并且**接收阴影的精灵**要 ``receive_shadow`` 保持默认 ``True``。
    - 想让遮挡体"跟着某个精灵走"，文档里不好表达引用 —— 用代码
      ``ShadowCaster2D.fromSprite(sprite)`` 或每帧同步 ``x`` / ``y``（``Game12.py`` 里有例子）。
    """
    from ..gameobject.ShadowCaster2D import ShadowCaster2D

    options = {"x": float(x), "y": float(y), "z": float(z), "r": float(r),
               "cast_shadow": bool(cast_shadow), "name": name}
    if length is not None:
        options["length"] = float(length)
    shape_kind = str(shape or "rect").lower()
    if points:
        caster = ShadowCaster2D(points=points, **options)
    elif shape_kind in ("circle", "ball"):
        caster = ShadowCaster2D.circle(float(radius if radius is not None else 0.5),
                                       segments=int(segments), **options)
    else:
        caster = ShadowCaster2D.rect(float(width if width is not None else 1.0),
                                     float(height if height is not None else 1.0),
                                     inset=float(inset or 0.0), **options)
    if outline:
        caster.setOutline(True)
    return caster


def registerBuiltins() -> dict:
    """注册引擎自带的类（重复调用幂等）。"""
    from ..particle.ParticleEmitter import ParticleEmitter

    builtins = {
        "GameObject": GameObject,
        "Sprite2D": Sprite2D,
        "Text": Text,
        "TileMapLayer": TileMapLayer,
        "AtlasSprite": atlasSprite,
        "ShadowCaster2D": shadowCaster2D,
        "Body2D": body2D,
        "ParticleEmitter": ParticleEmitter,
        "light2D": light2D,
        "sun2D": sun2D,
        "spot2D": spot2D,
    }
    for key, factory in builtins.items():
        if key not in REGISTRY:
            REGISTRY[key] = factory
            _PARAMETER_CACHE.pop(key, None)
    return REGISTRY


registerBuiltins()
