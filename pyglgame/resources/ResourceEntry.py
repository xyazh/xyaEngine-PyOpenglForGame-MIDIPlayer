"""虚拟文件映射里的**值**：一个可调用对象，调用它得到该文件的 ``bytes``。

映射长这样（见 :class:`~pyglgame.resources.ResourceManager.ResourceManager`）::

    {
        "res/shader/dis.frag": <ResourceEntry>,
        "res/img/star.png":    <ResourceEntry>,
        "res/save/player.dat": <MyDecryptEntry>,   # ← 开发者自己替换的
    }

为什么值要做成"对象"而不是现成的 ``bytes``
------------------------------------------

* **惰性**：启动时不把整个包读进内存，第一次访问才读（贴图/音频动辄几十 MB）；
* **可定制**：开发者继承 :class:`ResourceEntry` 覆盖 :meth:`__call__` 就能做
  自己的文件处理 —— 解密、格式转换、运行时生成；也可以拿现成条目包一层
  （:class:`TransformEntry`），或者干脆换成任意 ``callable``：

  .. code-block:: python

      manager = Resources.manager()
      manager.files["res/secret.dat"] = MyDecryptEntry(...)      # 继承替换
      manager.files["res/gen.txt"] = lambda: b"runtime bytes"    # 纯可调用对象

引擎自带的几种
--------------

============================  ==================================================
:class:`ResourceEntry`        默认实现：向它所属的 :class:`ResourceLoader` 要字节
                              （目录形态 = 照目录读，封包形态 = 读封包内部文件）
:class:`CallableEntry`        用一个普通可调用对象当数据源
:class:`BytesEntry`           数据已经在内存里（``bytes``）
:class:`FileEntry`            磁盘上的真实文件（不走 Loader）
:class:`TransformEntry`       把现成条目"加工"一下（解密 / 格式转换）
============================  ==================================================
"""

import os


class ResourceEntry:
    """虚拟文件映射里的值：**可调用对象**，``entry()`` 返回该文件的 ``bytes``。

    :param name:  虚拟路径（映射的 key，形如 ``res/dir/file``）
    :param loader: 数据来源（:class:`~pyglgame.resources.ResourceLoader.ResourceLoader`）
    :param inner: 来源内部的名字（相对挂载点；不给时用 ``name``）
    """

    def __init__(self, name: str, loader=None, inner: str = None) -> None:
        self.name = str(name)
        self.loader = loader
        self.inner = str(name) if inner is None else str(inner)

    # === 取值（子类覆盖 __call__ 即可定制文件处理） ===

    def __call__(self) -> bytes:
        """取这个文件的字节（默认：向 Loader 要；拿不到返回 ``b""``）。"""
        if self.loader is None:
            return b""
        data = self.loader.read(self.inner)
        return data if data is not None else b""

    def toBytes(self) -> bytes:
        """:meth:`__call__` 的别名（写起来更像"读文件"）。"""
        return self()

    # === 元信息 ===

    def exists(self) -> bool:
        """这个条目背后真的有数据吗。"""
        return bool(self.loader.exists(self.inner)) if self.loader is not None else True

    def size(self) -> int:
        """字节数（拿不到时返回 ``0``）。"""
        return int(self.loader.size(self.inner)) if self.loader is not None else 0

    def realPath(self):
        """真实文件路径；**封包里的条目返回 ``None``**。"""
        return self.loader.realPath(self.inner) if self.loader is not None else None

    def reload(self) -> bytes:
        """取一次数据（默认实现没有缓存，等价于 :meth:`__call__`）。"""
        return self()

    # === 包装：任何"来源" → 条目 ===

    @classmethod
    def of(cls, name: str, source) -> "ResourceEntry":
        """把各种"来源"统一包成条目：现成条目 / ``bytes`` / 可调用对象 / 真实路径。"""
        if isinstance(source, ResourceEntry):
            return source
        if isinstance(source, (bytes, bytearray, memoryview)):
            return BytesEntry(name, bytes(source))
        if callable(source):
            return CallableEntry(name, source)
        if isinstance(source, str):
            return FileEntry(name, source)
        raise TypeError("不认识的资源来源: %r" % (type(source).__name__,))

    def __repr__(self) -> str:
        return "%s<%s>" % (type(self).__name__, self.name)


class CallableEntry(ResourceEntry):
    """用一个普通可调用对象当数据源：``lambda: b"..."`` / 生成式资源。"""

    def __init__(self, name: str, provider, inner: str = None) -> None:
        super().__init__(name, loader=None, inner=inner)
        self.provider = provider

    def __call__(self) -> bytes:
        data = self.provider()
        return data if data is not None else b""

    def exists(self) -> bool:
        return True

    def size(self) -> int:
        return len(self())

    def realPath(self):
        return None


class BytesEntry(ResourceEntry):
    """数据已经在内存里的条目（``bytes``）。"""

    def __init__(self, name: str, data: bytes = b"", inner: str = None) -> None:
        super().__init__(name, loader=None, inner=inner)
        self.data = bytes(data or b"")

    def __call__(self) -> bytes:
        return self.data

    def exists(self) -> bool:
        return True

    def size(self) -> int:
        return len(self.data)

    def realPath(self):
        return None


class FileEntry(ResourceEntry):
    """磁盘上的**真实文件**（"照目录读取"的最小实现，不走 Loader）。

    给"资源在项目外 / 必须给出真实路径"这类场合用；挂载点里的目录条目走的是
    :class:`~pyglgame.resources.ResourceLoader.DirectoryLoader`，读字节的语义一致。
    """

    def __init__(self, name: str, path: str, inner: str = None) -> None:
        super().__init__(name, loader=None, inner=inner)
        self.path = str(path)

    def __call__(self) -> bytes:
        try:
            with open(self.path, "rb") as handle:
                return handle.read()
        except OSError:
            return b""

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def realPath(self):
        return self.path


class TransformEntry(ResourceEntry):
    """把现成条目**加工**一下：解密 / 格式转换 / 拼装（开发者定制文件处理的入口）。

    ::

        entry = manager.files["res/save/player.dat"]
        manager.files["res/save/player.json"] = TransformEntry(
            "res/save/player.json", entry,
            lambda data: json.dumps(json.loads(xor(data))).encode("utf-8"))

    ``transform`` 是开发者给的"bytes → bytes"可调用对象（上游读不到数据时收到的
    会是 ``b""``，要不要抛错由它自己决定）。
    """

    def __init__(self, name: str, source, transform, inner: str = None) -> None:
        super().__init__(name, loader=None, inner=inner)
        self.source = ResourceEntry.of(name, source)
        self.transform = transform

    def __call__(self) -> bytes:
        data = self.transform(self.source())
        return data if data is not None else b""

    def exists(self) -> bool:
        return self.source.exists()

    def size(self) -> int:
        return len(self())

    def realPath(self):
        """加工过的数据在磁盘上并不存在（保留原条目的路径只作排查用）。"""
        return self.source.realPath()
