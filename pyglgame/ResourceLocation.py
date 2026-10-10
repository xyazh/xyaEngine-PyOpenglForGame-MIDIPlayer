"""资源定位器：把**逻辑路径**（``res/shader/dis.vert``）变成字节。

读取委托给 :mod:`pyglgame.resources`，由它决定路径落到项目里的 ``res/`` 目录
还是 ``res.xypack`` 资源包 —— 调用方写逻辑路径即可，封包 / 目录两种形态都不用
改代码。``res_path`` 只在必须拿到真实路径的特殊场景才用（交给外部工具、读写
游戏产物），见 :meth:`realPath`。
"""

import json
from io import BytesIO

from . import resources
from .xyazhServer.ConsoleMessage import ConsoleMessage


class ResourceLocation:
    """资源定位器。

    - 惰性读取（``preload=False`` 时首次访问才读盘/读包）
    - 支持按"解析到哪个资源"做字典键（:meth:`__hash__` / :meth:`__eq__`），
      便于做纹理/图集缓存；**目录形态与包形态的键不会互相串味**
    """

    @property
    def data(self) -> bytes:
        if self._data is None:
            self.load()
        return self._data

    @data.setter
    def data(self, value: bytes) -> None:
        self._data = value

    @data.deleter
    def data(self) -> None:
        # 释放内容（旧实现是 ``del self._data``，之后再访问会 AttributeError）
        self._data = None

    def __init__(self, path: str, preload: bool = False) -> None:
        self.path = str(path)
        self.preload = preload
        self._data: bytes = None
        # 缓存键在构造时定一次，之后即使挂载表变了，本对象的行为也不会前后不一致
        self._cache_key = resources.cacheKey(self.path)
        if preload:
            self.load()

    # === 便于做缓存键 ===
    def __hash__(self) -> int:
        return hash(self._cache_key)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ResourceLocation):
            return NotImplemented
        return self._cache_key == other._cache_key

    def cacheKey(self) -> str:
        return self._cache_key

    # === 解析信息（日志/报错/排查用） ===

    def isLogical(self) -> bool:
        """是不是逻辑路径（走挂载表）。"""
        return resources.isLogicalPath(self.path)

    def resolve(self) -> str:
        """一句话说明"这个路径解析到哪了"（``dir:...``/``pack:...``/``file:...``/``missing:...``）。"""
        return resources.describe(self.path)

    def realPath(self) -> str:
        """真实文件路径；**资源包里的条目返回 ``None``**。

        只在"必须把文件路径交给别人"的特殊情况下用（未封包的外部工具、子进程等）。
        能用 :meth:`toBytes` / :meth:`getIO` 就不要用它 —— 封包之后拿不到真实路径。
        """
        return resources.realPath(self.path)

    def listDir(self) -> list:
        """把自身当目录，列出直接子项（目录/包一致；不是目录返回 ``[]``）。"""
        return resources.listDir(self.path)

    def isDir(self) -> bool:
        return resources.isDir(self.path)

    # === 读取 ===

    def exists(self) -> bool:
        if self.path[:5] == "blob:":
            return True
        return resources.exists(self.path)

    def load(self) -> None:
        if self.path[:5] == "blob:":
            self.data = b""
            return
        data = resources.readBytes(self.path)
        if data is None:
            ConsoleMessage.printError(
                "Resource not found: %s (resolved: %s)" % (self.path, self.resolve()))
            self.data = b""
            return
        self.data = data

    def release(self) -> None:
        self._data = None

    def toBytes(self) -> bytes:
        return self.data

    def tryToString(self, e: str = "utf-8") -> str:
        try:
            return self.data.decode(e)
        except UnicodeDecodeError:
            ConsoleMessage.printError(f"File {self.path} is not a text file")
        return ""

    def tryLoadJson(self, e: str = "utf-8")->dict|list:
        try:
            return json.loads(self.tryToString(e))
        except json.JSONDecodeError:
            ConsoleMessage.printError(f"File {self.path} is not a valid JSON file")
        return None

    def getIO(self) -> BytesIO:
        return BytesIO(self.data)

    def __str__(self) -> str:
        return f"ResourceLocation<{self.path}>"