"""材质贴图的**自动配对**：颜色贴图隔壁的 ``_normal`` / ``_n`` / ``_lightmap`` / ``_l``。

约定（"零配置"的那条规矩）
--------------------------

只要配对图与颜色贴图**同目录、按后缀命名**，图集与场景就不用再写任何属性 ——
引擎自己会挂上（挂的就是 2D 光照用的那两张贴图，见 ``docs/2D光照与阴影.md``）::

    res/atlas/chr.png                    颜色（图集）
    res/atlas/chr_normal.png             法线贴图  ← 自动认
    res/atlas/chr_specular.png           高光贴图  ← 名字不在后缀表里，**不认**（要显式写）
    res/scene/b1/sewer_sall_moss.png     颜色（图块集）
    res/scene/b1/sewer_sall_moss_lightmap.png   高光贴图 ← 自动认

====================  ==================================================
法线贴图 ``normal_map``    ``<名字>_normal.<ext>`` → ``<名字>_n.<ext>``
高光贴图 ``specular_map``  ``<名字>_lightmap.<ext>`` → ``<名字>_specular.<ext>``
                           → ``<名字>_l.<ext>``（``_specular`` 是多认的一个常见写法）
====================  ==================================================

规则（每一条都有单测锁着，见 ``tests/test_material_maps.py``）：

- **更具体的后缀优先**：``hero_n.png`` 与 ``hero_normal.png`` 同时在时用后者；
- 扩展名跟着颜色贴图走，也认常见的几种（``.png`` / ``.jpg`` / ``.jpeg`` / ``.bmp`` /
  ``.tga`` / ``.webp`` / ``.gif`` / ``.tif`` / ``.tiff``，大小写不敏感）；
- 精确名字都没命中时，做一次**大小写不敏感**的兜底（``HERO_N.PNG`` 也算）；
- **只查同一个目录、不递归**：配对图必须和颜色贴图放一起 —— 否则"看起来对、其实错位"
  的图反而更难查，还不如不认；
- 路径本身不存在（还没上传的 ``LazyTexture`` / 纯名字）→ 老老实实返回"没有"。

谁在用
------

=========================  ==========================================================
:class:`~pyglgame.render.TextureAtlas.TextureAtlas`
                           图集自带 ``normal_map_path`` / ``specular_map_path`` 与
                           ``material()``（图集动画自动吃光照）
``IAtlasAnimation``        没显式给 ``material`` 时用图集自带的那份
:func:`pyglgame.scene.registry.build`
                           场景条目：``texture`` / ``tileset`` / ``atlas`` 的配对图
                           自动补成 ``normal_map`` / ``specular_map``（懒上传）
=========================  ==========================================================

**显式优先**：场景里写了 ``"normal_map": "..."``（**或者写了 ``null``**）就以文档为准 ——
``null`` 就是"我知道旁边有，但这次不要"的开关；全局开关见 :func:`setAutoEnabled`。

⚠ 自动配到的贴图必须与颜色贴图**同布局**（帧矩形 / 格子一一对应）—— "同目录 + 同尺寸
命名"能成立的前提就是这个；尺寸不一致的贴图请自己写 ``normal_map`` 并自行保证 UV 对得上
（见 ``docs/2D帧动画与场景导入.md`` 的"给图集加法线 / 高光"）。

本模块**只做纯数据判断**（名字拼接 + :mod:`pyglgame.resources` 的存在性/列目录查询）：
不碰 GL、不建贴图 —— 而且**目录形态与资源包形态都吃**（封包后照样能自动配对）。
:func:`materialFor` 返回的材质里放的是
:class:`~pyglgame.render.LazyTexture.LazyTexture`（第一次绘制才上传）。
"""

import os

from .. import resources
from .LazyTexture import LazyTexture

#: 法线贴图的后缀（**长的在前**：``_normal`` 比 ``_n`` 更具体）
NORMAL_SUFFIXES = ("_normal", "_n")
#: 高光贴图的后缀。``_lightmap`` / ``_l`` 是按需求定的；``_specular`` 是**多认一个**
#: 常见写法（引擎自带的样例资源就是 ``chr_specular.png`` / ``level1_tiles_specular.png``）
SPECULAR_SUFFIXES = ("_lightmap", "_specular", "_l")

#: 场景 / 图集文档里的键名 → 后缀表（顺序 = 优先级）
MATERIAL_MAP_KEYS = {"normal_map": NORMAL_SUFFIXES, "specular_map": SPECULAR_SUFFIXES}

#: 认的图片扩展名（大小写不敏感；顺序 = 试的顺序）
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp", ".gif",
                    ".tif", ".tiff")

#: 总开关：``False`` = 全引擎关掉自动配对（只想手写 ``normal_map`` 的场合）
AUTO_ENABLED = True


def setAutoEnabled(enabled: bool) -> bool:
    """打开 / 关掉自动配对（返回设置后的值）。关掉之后一切以显式写法为准。"""
    global AUTO_ENABLED
    AUTO_ENABLED = bool(enabled)
    return AUTO_ENABLED


def autoEnabled() -> bool:
    """当前是否开着自动配对。"""
    return AUTO_ENABLED


def stripImageExtension(path) -> str:
    """去掉**认得的**图片扩展名：``res/atlas/hero.png`` → ``res/atlas/hero``。

    扩展名不在 :data:`IMAGE_EXTENSIONS` 里就原样返回（``hero.ase`` 不会被腰斩）。
    """
    text = str(path or "")
    root, extension = os.path.splitext(text)
    if extension.lower() in IMAGE_EXTENSIONS:
        return root
    return text


def candidateNames(image, suffixes=NORMAL_SUFFIXES,
                   extensions=IMAGE_EXTENSIONS) -> list:
    """颜色贴图 → 候选文件名列表（按试的顺序；保留原目录，扩展名逐个试）。"""
    stem = stripImageExtension(image)
    if not stem:
        return []
    result = []
    for suffix in suffixes:
        for extension in extensions:
            result.append(stem + suffix + extension)
    return result


def findMaterialMap(image, suffixes=NORMAL_SUFFIXES,
                    extensions=IMAGE_EXTENSIONS) -> str:
    """找 ``image`` 旁边带 ``suffixes`` 之一的那张图；没有则返回 ``""``。

    :param image: 颜色贴图路径（``res/atlas/hero.png``）
    :param suffixes: 候选后缀（**长的在前**，见 :data:`NORMAL_SUFFIXES`）
    """
    if not autoEnabled():
        return ""
    candidates = candidateNames(image, suffixes, extensions)
    if not candidates:
        return ""
    for candidate in candidates:
        if resources.exists(candidate) and not resources.isDir(candidate):
            return candidate
    # 精确名字都没命中：再按"文件名 + 后缀"大小写不敏感地扫一遍目录（HERO_N.PNG 之类）
    stem = stripImageExtension(image)
    directory = resources.dirnamePath(stem) or "."
    if not resources.isDir(directory):
        return ""
    wanted = {resources.basenamePath(stem).lower() + suffix for suffix in suffixes}
    for entry in resources.listDir(directory):
        name, extension = os.path.splitext(entry)
        if extension.lower() not in extensions:
            continue
        if name.lower() in wanted:
            return resources.joinPath(directory, entry)
    return ""


def normalMapFor(image) -> str:
    """颜色贴图 → 法线贴图路径（``hero.png`` → ``hero_normal.png`` / ``hero_n.png``）。"""
    return findMaterialMap(image, NORMAL_SUFFIXES)


def specularMapFor(image) -> str:
    """颜色贴图 → 高光贴图路径（``hero.png`` → ``hero_lightmap.png`` / ``hero_l.png``）。"""
    return findMaterialMap(image, SPECULAR_SUFFIXES)


def materialMapsFor(image, normal_map=None, specular_map=None,
                    auto: bool = True) -> dict:
    """颜色贴图 → ``{"normal_map": 路径, "specular_map": 路径}``（**只含真找到的键**）。

    显式给的路径优先；``auto=False`` 时完全不查盘（只认显式参数）——
    "假装旁边有文件"的单测就这么用。
    """
    found = {}
    if auto and autoEnabled() and image:
        for key, suffixes in MATERIAL_MAP_KEYS.items():
            path = findMaterialMap(image, suffixes)
            if path:
                found[key] = path
    for key, value in (("normal_map", normal_map), ("specular_map", specular_map)):
        if value:
            found[key] = value
    return found


def asTexture(path):
    """``str`` → 懒上传贴图（``LazyTexture``）；现成贴图 / ``None`` 原样返回。"""
    if isinstance(path, str):
        return LazyTexture(path) if path else None
    return path


def materialFor(image=None, normal_map=None, specular_map=None, auto: bool = True,
                **params):
    """按颜色贴图自动配一个 :class:`~pyglgame.render.Lighting2D.Material2D`。

    :param image: 颜色贴图（图集 / 图块集 / 精灵图）的路径
    :param normal_map: 显式法线贴图（路径或贴图对象）
    :param specular_map: 显式高光贴图（同上）
    :param auto: ``False`` = 不查盘（只认显式参数）
    :param params: 其余原样转给 ``Material2D``（``shininess`` / ``normal_strength`` …）
    :return: 材质；**一张配对图都没有时返回 ``None``**（调用方自己决定要不要建空材质）

    ::

        material = materialFor("res/atlas/hero.png", shininess=48.0)
        # → Material2D(normal_map=LazyTexture("res/atlas/hero_n.png"), ...)
    """
    maps = materialMapsFor(image, normal_map=normal_map, specular_map=specular_map,
                           auto=auto)
    if not maps:
        return None
    from .Lighting2D import Material2D

    for key in ("normal_map", "specular_map"):
        if key in maps:
            maps[key] = asTexture(maps[key])
    return Material2D(**maps, **params)
