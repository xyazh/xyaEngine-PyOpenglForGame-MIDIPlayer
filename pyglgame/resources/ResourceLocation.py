"""资源定位器：把**逻辑路径**（``res/shader/dis.vert``）变成字节。

``ResourceLocation(file)`` 的用法**保持不变**（引擎与 demo 大量在用）::

    location = ResourceLocation("res/shader/dis.vert")
    data = location.toBytes()               # 目录形态 / 封包形态都吃
    document = ResourceLocation("res/atlas/hero.json").tryLoadJson()

读取委托给 :class:`~pyglgame.resources.Resources.Resources`（它再交给
:class:`~pyglgame.resources.ResourceManager.ResourceManager`）：逻辑路径落到
``res/`` 目录还是 ``res.xypack`` 封包由挂载表决定 —— 调用方写逻辑路径即可，
封包 / 目录两种形态都不用改代码。

* :meth:`realPath` 只在"必须拿到真实路径"的特殊场景用（交给外部工具、子进程）；
* :meth:`entry` 拿的是**虚拟文件映射**里的那个可调用对象 —— 想自己替换 / 加工
  某个文件的读法（解密、格式转换）就从这里下手，见
  :mod:`pyglgame.resources.ResourceEntry`。

旧导入路径 ``from pyglgame.ResourceLocation import ResourceLocation`` 仍然可用
（``pyglgame/ResourceLocation.py`` 只是本模块的再导出）。
"""

import json
from io import BytesIO

from ..xyazhServer.ConsoleMessage import ConsoleMessage
from .ResourcePath import ResourcePath
from .Resources import Resources


class ResourceLocation:
    """资源定位器。

    - 惰性读取（``preload=False`` 时首次访问才读盘/读包）
    - 支持按"解析到哪个资源"做字典键（:meth:`__hash__` / :meth:`__eq__`），
      便于做纹理/图集缓存；**目录形态与包形态的键不会互相串味**
    """

    #: ``blob:`` 伪路径前缀（没有对应文件、当作"存在但空"）
    BLOB_PREFIX = ResourcePath.BLOB_PREFIX

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
        self._cache_key = Resources.cacheKey(self.path)
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
        return Resources.isLogicalPath(self.path)

    def resolve(self) -> str:
        """一句话说明"这个路径解析到哪了"（``dir:…``/``pack:…``/``file:…``/``missing:…``）。"""
        return Resources.describe(self.path)

    def realPath(self):
        """真实文件路径；**资源包里的条目返回 ``None``**。

        只在"必须把文件路径交给别人"的特殊情况下用（未封包的外部工具、子进程等）。
        能用 :meth:`toBytes` / :meth:`getIO` 就不要用它 —— 封包之后拿不到真实路径。
        """
        return Resources.realPath(self.path)

    def listDir(self) -> list:
        """把自身当目录，列出直接子项（目录/包一致；不是目录返回 ``[]``）。"""
        return Resources.listDir(self.path)

    def isDir(self) -> bool:
        return Resources.isDir(self.path)

    # === 虚拟文件映射 ===

    def entry(self):
        """映射里那个**可调用对象**（``res/dir/file`` → 条目）；没有返回 ``None``。

        开发者可以拿它包一层（解密 / 格式转换）再 :meth:`setEntry` 回去。
        """
        return Resources.entry(self.path)

    def setEntry(self, source):
        """替换本路径的读法（解密的字面量 / 自己的条目 / 现成 ``bytes``）。"""
        return Resources.setEntry(self.path, source)

    # === 读取 ===

    def exists(self) -> bool:
        if ResourcePath.isBlob(self.path):
            return True
        return Resources.exists(self.path)

    def load(self) -> None:
        if ResourcePath.isBlob(self.path):
            self.data = b""
            return
        data = Resources.readBytes(self.path)
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

    def tryLoadJson(self, e: str = "utf-8") -> dict|list:
        try:
            return json.loads(self.tryToString(e))
        except json.JSONDecodeError:
            ConsoleMessage.printError(f"File {self.path} is not a valid JSON file")
        return None

    def getIO(self) -> BytesIO:
        return BytesIO(self.data)

    def __str__(self) -> str:
        return f"ResourceLocation<{self.path}>"
