"""可替换的**资源来源**（Loader）：一个挂载点里有哪些文件、各自是什么字节。

引擎自带两个
------------

============================  ==========================================================
:class:`DirectoryLoader`      ``res/`` 目录形态：照目录读（开发时的"作者格式"）
:class:`PackLoader`           ``res.xypack`` 封包形态：读封包**内部文件**（发行格式）
============================  ==========================================================

要自定义（自加密包 / 别的压缩格式 / 从数据库或网络取资源），二选一：

* **换掉默认 Loader** —— ``ResourceManager.setPackLoader(MyLoader)``（或
  ``setDirectoryLoader``）：之后约定挂载都用它；
* **单个挂载点指定** —— ``Resources.mount("res", "res.pack", loader=MyLoader)``。

继承 :class:`ResourceLoader` 时只实现必要的方法就够（``names`` / ``read`` /
``exists`` 是核心，其余有默认实现），并把 :meth:`accepts` 写成自己的识别规则，
这样"自动识别挂载类型"和 `setupDefault` 也会认它。
"""

import os
import threading

from .ResourcePack import ResourcePack


class ResourceLoader:
    """资源来源接口（见模块文档）。

    :param target: 来源本体的真实路径（目录 / 包）；由管理器解析成绝对路径后传入
    """

    #: 挂载类型标识（``Mount.kind`` / ``Resources.describe()`` 里的那个词）
    KIND = "loader"

    def __init__(self, target: str) -> None:
        self.target = os.path.abspath(str(target))

    # === 自动识别 ===

    @classmethod
    def accepts(cls, target) -> bool:
        """这个 Loader 认不认 ``target``（挂载时自动判断用；自定义 Loader 覆盖它）。"""
        return False

    # === 查询 / 读取（名字都是**相对挂载点**的、正斜杠） ===

    def names(self) -> list:
        """来源里的所有**文件**名（排序；目录形态走 os.walk，封包形态走包索引）。"""
        return []

    def exists(self, name: str) -> bool:
        return False

    def isDir(self, name: str) -> bool:
        return False

    def read(self, name: str):
        """读一个文件的字节；不存在返回 ``None``。"""
        return None

    def size(self, name: str) -> int:
        return 0

    def children(self, name: str) -> list:
        """列一个目录下的直接子项（名字不含目录前缀）；不是目录返回 ``[]``。"""
        return []

    def realPath(self, name: str):
        """真实文件路径；来源里没有真实文件的（封包）返回 ``None``。"""
        return None

    def close(self) -> None:
        """释放句柄（管理器在卸载 / clear 时调用）。"""

    def __str__(self) -> str:
        return "%s<%s>" % (type(self).__name__, self.target)


class DirectoryLoader(ResourceLoader):
    """目录形态（``res/`` 文件夹）：**照目录读取**。

    条目在被访问时才落盘读，所以"改完立刻生效"，不用重新挂载；
    新增的文件也能读到（管理器查找时会直接问文件系统）。
    """

    KIND = "dir"

    def __init__(self, target: str) -> None:
        super().__init__(target)
        if not os.path.isdir(self.target):
            raise FileNotFoundError("要挂载的目录不存在: %s" % self.target)

    @classmethod
    def accepts(cls, target) -> bool:
        return os.path.isdir(target)

    def realPath(self, name: str):
        if not name:
            return self.target
        return os.path.join(self.target, str(name).replace("/", os.sep))

    def names(self) -> list:
        found = []
        for root, dirs, files in os.walk(self.target):
            dirs[:] = sorted(dirs)
            for filename in sorted(files):
                full = os.path.join(root, filename)
                found.append(ResourcePack.normalizeEntry(
                    os.path.relpath(full, self.target)))
        found.sort()
        return found

    def exists(self, name: str) -> bool:
        if not name:
            return os.path.isdir(self.target)
        return os.path.exists(self.realPath(name))

    def isDir(self, name: str) -> bool:
        if not name:
            return os.path.isdir(self.target)
        return os.path.isdir(self.realPath(name))

    def read(self, name: str):
        try:
            with open(self.realPath(name), "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def size(self, name: str) -> int:
        try:
            return os.path.getsize(self.realPath(name))
        except OSError:
            return 0

    def children(self, name: str) -> list:
        try:
            return sorted(os.listdir(self.realPath(name)))
        except OSError:
            return []


class PackLoader(ResourceLoader):
    """封包形态（``res.xypack``）：读封包**内部文件**。

    ``PACK_CLASS`` 是包格式的实现（默认 :class:`ResourcePack` = zip 容器）。
    想换封包格式（自加密包 / 别的压缩），两种做法：

    * ``class MyPackLoader(PackLoader): PACK_CLASS = MyEncryptedPack``，
      再 ``ResourceManager.setPackLoader(MyPackLoader)`` —— 最小改动；
    * 或者连同"怎么列条目、怎么读条目"一起重写（继承
      :class:`ResourceLoader`），换来的是完全自由的格式。

    包句柄会一直开着（资源的读取发生在启动/首次使用时，重复 open 一次包
    在几十 MB 的量级上并不便宜），:meth:`close` 显式释放。
    """

    KIND = "pack"

    #: 包格式实现（可替换）
    PACK_CLASS = ResourcePack

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self._lock = threading.RLock()
        self.pack = self.PACK_CLASS(self.target)
        #: 大小写不敏感索引（Windows 友好）：``小写名 -> 真名``
        self._lower = {name.lower(): name for name in self.pack.names()}

    @classmethod
    def accepts(cls, target) -> bool:
        """默认只认"后缀对 + 是合法 zip"的包（见 :meth:`ResourcePack.isPackFile`）。"""
        return ResourcePack.isPackFile(target)

    # === 名字 ===

    def entryName(self, name: str):
        """内部名 → 封包里真实存在的名字（大小写不敏感兜底）。"""
        name = ResourcePack.normalizeEntry(name)
        if self.pack.exists(name):
            return name
        return self._lower.get(name.lower())

    def names(self) -> list:
        """封包内部的文件名（排序）。"""
        return self.pack.names()

    def exists(self, name: str) -> bool:
        if not name:
            return True        # 挂载点本身
        return self.entryName(name) is not None or self.isDir(name)

    def isDir(self, name: str) -> bool:
        return self.pack.isDir(name)

    def children(self, name: str) -> list:
        return self.pack.children(name)

    # === 读取 ===

    def read(self, name: str):
        with self._lock:
            return self.pack.read(name)

    def size(self, name: str) -> int:
        return self.pack.size(name)

    def realPath(self, name: str):
        """封包里的条目在磁盘上没有自己的路径 —— 一律 ``None``。"""
        return None

    def close(self) -> None:
        with self._lock:
            self.pack.close()

    def __str__(self) -> str:
        return "PackLoader<%s, %d files>" % (self.target, len(self.pack.names()))
