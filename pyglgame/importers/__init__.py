"""外部工具的格式 → **引擎自有文档**的导入器。

这里放的全是**纯数据**转换函数：读一个 dict（外部工具的 JSON），吐一个 dict
（引擎自有格式，见 ``docs/资源导入管线.md``）。它们**不碰 GL、不建 GameObject**，
所以既能离线跑（构建脚本），也能在运行时直接调，还能被单测 100% 覆盖。

============================  ==========================================================
:mod:`~pyglgame.importers.aseprite`   LibreSprite / Aseprite 的图集 JSON → 引擎图集文档
:mod:`~pyglgame.importers.tiled`      Tiled 的 JSON 导出 → 引擎场景文档
============================  ==========================================================

典型用法（离线转换一步，运行时只读引擎格式）::

    import json
    from pyglgame.importers import aseprite
    from pyglgame.render.TextureAtlas import TextureAtlas

    raw = json.loads(open("res/atlas/hero.json", encoding="utf-8").read())
    atlas_doc = aseprite.toAtlas(raw, image_path="res/atlas/hero.png")
    atlas = TextureAtlas.fromDict(atlas_doc)

也可以一行到位（``fromFile`` 自己读盘）::

    atlas = TextureAtlas.fromAseprite("res/atlas/hero.json")
"""

from . import aseprite, tiled

__all__ = ["aseprite", "tiled"]
