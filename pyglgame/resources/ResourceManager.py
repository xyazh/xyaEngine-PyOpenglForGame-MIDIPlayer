"""挂载表 + **虚拟文件映射**：把"逻辑路径"变成字节（目录形态 / 封包形态等价）。

一张图
------

::

    ResourceLocation("res/shader/dis.vert")      ← 调用方只写逻辑路径
        │
        ▼
    Resources（门面，类方法 / 静态方法）          ← 引擎内部统一入口
        │
        ▼
    ResourceManager（挂载表 + 虚拟文件映射 files）
        ├── Mount("res" -> DirectoryLoader(<项目根>/res))        目录形态：照目录读
        └── Mount("res" -> PackLoader(<项目根>/res.xypack))      封包形态：读包内文件

**虚拟文件映射**（:attr:`ResourceManager.files`）= "虚拟路径 → 可调用对象"：

::

    {"res/shader/dis.vert": <ResourceEntry>,
     "res/img/star.png":    <ResourceEntry>}

挂载时把来源里的**每个文件**各自包成一个
:class:`~pyglgame.resources.ResourceEntry.ResourceEntry` 放进映射：目录形态照目录读，
封包形态读封包内部文件（见 :mod:`pyglgame.resources.ResourceLoader`）。
开发者可以拿现成条目包一层、换成自己的子类，或者直接塞一个
``lambda: b"..."`` —— 映射的值只要是"调用后返回 bytes 的可调用对象"就行
（见 :mod:`pyglgame.resources.ResourceEntry` 与 :meth:`setEntry`）。

查找顺序（踩坑换来的，别改）
----------------------------

1. **虚拟文件映射** —— 可定制的那一层，先问它（开发者替换过的条目立即生效）；
2. **挂载表** —— 按注册顺序，**第一个"真的存在这个条目"的挂载**生效
   （不是第一个前缀匹配的），这样"目录里没有、包里才有"的兜底不会失效；
3. **真实文件系统** —— 绝对路径 / 没被任何挂载认领的相对路径（保持老代码
   "相对 CWD 的普通路径"语义），再按"相对项目根"试一次；
4. ``blob:`` 伪路径 —— 当作"存在但空"。
"""

import os
import threading

from ..tools.paths import app_root
from .ResourceEntry import ResourceEntry
from .ResourceLoader import DirectoryLoader, PackLoader, ResourceLoader
from .ResourcePack import ResourcePack
from .ResourcePath import ResourcePath


class Mount:
    """一个挂载点：``前缀 -> Loader``（目录 / 封包 / 自定义来源）。

    逻辑路径的语义是 ``<前缀>/<挂载点内部的相对路径>``：``res/shader/x.vert``
    在前缀 ``res`` 的挂载点里就是内部名字 ``shader/x.vert``。
    """

    def __init__(self, prefix: str, loader: ResourceLoader) -> None:
        self.prefix = ResourcePath.normalize(prefix)
        self.loader = loader
        self.kind = getattr(loader, "KIND", "loader")
        self.path = getattr(loader, "target", "")
        self._lock = threading.RLock()

    # === 逻辑路径 <-> 内部名字 ===

    def innerName(self, path: str) -> str:
        """逻辑路径 → 挂载点内部的名字（``res/a/b`` → ``a/b``）。"""
        normalized = ResourcePath.normalize(path)
        if not self.prefix:
            return normalized
        if normalized == self.prefix:
            return ""
        return normalized[len(self.prefix) + 1:]

    def key(self, inner: str) -> str:
        """挂载点内部的名字 → 虚拟路径（映射的 key：``a/b`` → ``res/a/b``）。"""
        inner = ResourcePath.normalize(inner)
        if not self.prefix:
            return inner
        if not inner:
            return self.prefix
        return "%s/%s" % (self.prefix, inner)

    def contains(self, path: str) -> bool:
        normalized = ResourcePath.normalize(path)
        if not self.prefix:
            return True
        return normalized == self.prefix or normalized.startswith(self.prefix + "/")

    # === 建条目 ===

    def entry(self, virtual: str = None, inner: str = None) -> ResourceEntry:
        """建一个"照来源读"的条目（虚拟文件映射里的默认值）。"""
        if inner is None:
            inner = self.innerName(virtual)
        else:
            inner = ResourcePath.normalize(inner)
        if virtual is None:
            virtual = self.key(inner)
        return ResourceEntry(virtual, self.loader, inner)

    # === 读写（参数都是内部名字） ===

    def names(self) -> list:
        """来源里的所有文件名（相对挂载点，正斜杠）。"""
        return self.loader.names()

    def exists(self, inner: str) -> bool:
        return self.loader.exists(inner)

    def isDir(self, inner: str) -> bool:
        return self.loader.isDir(inner)

    def read(self, inner: str):
        with self._lock:
            return self.loader.read(inner)

    def size(self, inner: str) -> int:
        return self.loader.size(inner)

    def listDir(self, inner: str) -> list:
        return self.loader.children(inner)

    def realPath(self, inner: str):
        return self.loader.realPath(inner)

    @property
    def pack(self):
        """封包形态下的 :class:`ResourcePack`（目录形态返回 ``None``）—— 排查用。"""
        return getattr(self.loader, "pack", None)

    def close(self) -> None:
        self.loader.close()

    def __str__(self) -> str:
        return "Mount<%s -> %s %s>" % (self.prefix or "/", self.kind, self.path)



class ResourceManager:
    """挂载表 + 虚拟文件映射（见模块文档）。

    * 全局实例：:meth:`ResourceManager.shared`（引擎与 demo 默认都用它）；
    * 也可以 ``ResourceManager(autoload=False)`` 自建一张独立的表（测试 / 沙盒），
      再用 ``Resources.use(manager)`` 临时顶替全局那一张。
    """

    #: 默认资源目录名（项目根下）
    DEFAULT_DIR = "res"

    #: 默认资源包文件名（项目根下）
    DEFAULT_PACK = DEFAULT_DIR + ResourcePack.SUFFIX

    #: 挂载前缀：逻辑路径的第一段
    DEFAULT_PREFIX = DEFAULT_DIR

    #: 显式指定资源目录 / 资源包 / 优先级的环境变量
    ENV_DIR = "XYA_RES_DIR"
    ENV_PACK = "XYA_RES_PACK"
    ENV_PREFER = "XYA_RES_PREFER"

    #: 挂载时**自动识别**的顺序（自定义 Loader 用 :meth:`registerLoader` 插进来）
    LOADERS = (DirectoryLoader, PackLoader)

    #: 约定挂载（setupDefault）用哪个 Loader —— 可替换成自定义封包 / 目录来源
    directoryLoader = DirectoryLoader
    packLoader = PackLoader

    #: 全局唯一实例（``None`` = 还没建）
    _shared = None

    # === 全局实例 ===

    @classmethod
    def shared(cls) -> "ResourceManager":
        """取全局 :class:`ResourceManager`（没有就建一个）。"""
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    @classmethod
    def use(cls, manager: "ResourceManager" = None) -> "ResourceManager":
        """换掉全局实例（测试 / 沙盒用；``None`` = 直接新建一张空表）。"""
        cls._shared = cls() if manager is None else manager
        return cls._shared

    def __init__(self, autoload: bool = True) -> None:
        #: 是否允许 :meth:`setupDefault` 自动挂上项目的 ``res/`` / ``res.xypack``
        self.autoload = bool(autoload)
        #: **虚拟文件映射**：虚拟路径（``res/dir/file``）→ 可调用对象
        self.files: dict = {}
        self._mounts: list = []
        self._custom: dict = {}
        self._lower: dict = {}
        self._lock = threading.RLock()
        self._defaults_ready = False
        #: 被"显式 mount()"过的前缀：约定挂载（setupDefault）不再往这些前缀上放东西
        self._explicit: set = set()

    # === Loader 定制 ===

    @classmethod
    def registerLoader(cls, loader_class, first: bool = False) -> tuple:
        """把一个自定义 Loader 加进"自动识别"的顺序里（返回更新后的顺序）。

        顺序 = 优先级：``LOADERS`` 里前一个 :meth:`ResourceLoader.accepts` 认了就用它。
        """
        loaders = list(cls.LOADERS)
        loaders = [loader_class] + loaders if first else loaders + [loader_class]
        cls.LOADERS = tuple(loaders)
        return cls.LOADERS

    @classmethod
    def setPackLoader(cls, loader_class) -> type:
        """替换**封包**默认 Loader（自定义封包格式走这里，见 ``docs/资源包.md``）。"""
        cls.packLoader = loader_class
        return loader_class

    @classmethod
    def setDirectoryLoader(cls, loader_class) -> type:
        """替换**目录**默认 Loader（例如按命名规则过滤掉一批文件）。"""
        cls.directoryLoader = loader_class
        return loader_class

    @classmethod
    def loaderFor(cls, target: str, kind: str = None, loader=None) -> ResourceLoader:
        """给一个目标建 Loader：显式 ``loader`` > 显式 ``kind`` > 自动识别。

        ``loader`` 可以是 Loader **类**（用 ``target`` 建实例），也可以是现成实例。
        """
        target = str(target)
        if not ResourcePath.isReal(target):
            target = os.path.join(app_root(), target)
        if loader is not None:
            if isinstance(loader, type):
                return loader(target)
            return loader
        if kind == "dir":
            return cls.directoryLoader(target)
        if kind == "pack":
            return cls.packLoader(target)
        for loader_class in cls.LOADERS:
            if loader_class.accepts(target):
                return loader_class(target)
        raise FileNotFoundError(
            "既不是目录也不是资源包（%s）：需要一个目录，或一个由 "
            "tools/pack_resources.py 生成的 %s" % (target, cls.DEFAULT_PACK))

    # === 挂载管理 ===

    def mount(self, prefix: str, target: str = None, kind: str = None,
              loader=None, front: bool = False) -> Mount:
        """挂载一个目录 / 资源包 / 自定义来源，返回 :class:`Mount`。

        * ``target`` 不给时把 ``prefix`` 当目标（即挂到默认前缀 ``res`` 下）；
        * ``kind``：``"dir"`` / ``"pack"``；不给就自动识别（见 :attr:`LOADERS`）；
        * ``loader``：Loader 类或实例（自定义封包格式走这里，优先级最高）；
        * ``front=True`` 插到最前面（查找优先）；
        * **显式挂载优先于约定挂载**：``setupDefault`` 不会再动这个前缀；
        * 同一个「前缀 + 目标」重复挂载会换掉旧的那份（幂等）；同一个前缀下的
          **不同**来源可以并存 —— 目录 + 封包就是这样：按挂载顺序查找，
          先命中的生效，先挂的目录里没有的条目由后面的封包兜底。

        挂载完成会立即重建**虚拟文件映射**（来源里的每个文件都进映射）。
        """
        mount = self._mountInternal(prefix, target, kind=kind, loader=loader, front=front)
        with self._lock:
            self._explicit.add(mount.prefix)
        return mount

    def _mountInternal(self, prefix: str, target: str = None, kind: str = None,
                       loader=None, front: bool = False) -> Mount:
        if target is None:
            target = prefix
            prefix = self.DEFAULT_PREFIX
        prefix = ResourcePath.normalize(prefix)
        mount = Mount(prefix, self.loaderFor(target, kind=kind, loader=loader))
        with self._lock:
            # 同一个「前缀 + 目标」重复挂载 = 换掉旧的那份（幂等）；
            # 同一个前缀下的**不同**来源可以并存（目录 + 封包就是常见的一种）
            for existing in list(self._mounts):
                if existing.prefix == prefix and existing.path == mount.path:
                    self._mounts.remove(existing)
                    existing.close()
            if front:
                self._mounts.insert(0, mount)
            else:
                self._mounts.append(mount)
        self.rebuild()
        return mount

    def unmount(self, prefix: str, close: bool = True) -> bool:
        """卸掉某个前缀上的挂载（顺带刷新映射）；真卸掉过返回 ``True``。"""
        prefix = ResourcePath.normalize(prefix)
        removed = False
        with self._lock:
            for mount in list(self._mounts):
                if mount.prefix == prefix:
                    self._mounts.remove(mount)
                    if close:
                        mount.close()
                    removed = True
        if removed:
            self.rebuild()
        return removed

    def clear(self) -> None:
        """清空挂载表、虚拟文件映射与"显式挂载"记录（测试 / 重来用）。"""
        with self._lock:
            for mount in self._mounts:
                mount.close()
            self._mounts.clear()
            self._explicit.clear()
            self._custom.clear()
        self._defaults_ready = False
        self.rebuild()

    def mounts(self) -> tuple:
        """当前所有挂载点（会先执行约定挂载）。"""
        self.setupDefault()
        with self._lock:
            return tuple(self._mounts)

    def setupDefault(self, force: bool = False) -> tuple:
        """按约定挂载项目自带的 ``res/``（目录）与 ``res.xypack``（包）。

        只在第一次真正用到资源时执行。**两个都在时都挂上**，查找顺序按偏好排：
        默认目录优先（开发时改完立刻生效），目录里没有的条目再从包里兜底；
        ``XYA_RES_PREFER=pack`` 反过来（验证"包里那份"）。
        显式挂载过的前缀不会被顶掉（``tests/test_resources.py`` 有锁）。
        """
        if self._defaults_ready and not force:
            with self._lock:
                return tuple(self._mounts)
        self._defaults_ready = True
        if not self.autoload:
            with self._lock:
                return tuple(self._mounts)
        with self._lock:
            if self.DEFAULT_PREFIX in self._explicit:
                return tuple(self._mounts)
        root = app_root()
        directory = os.environ.get(self.ENV_DIR) or os.path.join(root, self.DEFAULT_DIR)
        pack = os.environ.get(self.ENV_PACK) or os.path.join(root, self.DEFAULT_PACK)
        prefer_pack = str(os.environ.get(self.ENV_PREFER, "")).lower() in ("pack", "1", "true")

        # 偏好 = 查找顺序（列表里靠前的先命中）
        ordered = (("pack", pack), ("dir", directory)) if prefer_pack \
            else (("dir", directory), ("pack", pack))
        plan = []
        for kind, target in ordered:
            if kind == "dir":
                if os.path.isdir(target):
                    plan.append((kind, target))
            elif self.packLoader.accepts(target):
                plan.append((kind, target))

        with self._lock:
            mounted = {(mount.prefix, mount.path) for mount in self._mounts}
        for kind, target in plan:
            if (self.DEFAULT_PREFIX, os.path.abspath(str(target))) in mounted:
                continue
            try:
                self._mountInternal(self.DEFAULT_PREFIX, target, kind=kind)
            except (OSError, ValueError):
                pass
        with self._lock:
            return tuple(self._mounts)

    # === 虚拟文件映射 ===

    def rebuild(self) -> dict:
        """按当前挂载表重建**虚拟文件映射**，返回它（就是 :attr:`files`）。

        * 每个挂载点里的每个文件 → ``{虚拟路径: 可调用条目}``；
        * 先注册的挂载点先占（与 :meth:`find` 的顺序一致）；
        * 开发者用 :meth:`setEntry` 放进去的条目**不会被覆盖**（定制优先）。
        """
        files = {}
        for mount in tuple(self._mounts):
            for inner in mount.names():
                key = mount.key(inner)
                if key not in files:
                    files[key] = mount.entry(key, inner)
        files.update(self._custom)
        with self._lock:
            self.files = files
            self._lower = {key.lower(): key for key in files}
        return self.files

    def refresh(self) -> dict:
        """重新扫一遍来源并刷新映射（新增 / 删除的文件立即反映进来）。"""
        return self.rebuild()

    def entry(self, path: str) -> ResourceEntry:
        """按虚拟路径取映射里的**条目**（可调用对象）；没有返回 ``None``。

        绝对路径与 ``blob:`` 不走映射（映射的 key 是 ``res/dir/file`` 这样的虚拟路径）。
        """
        path = str(path)
        if ResourcePath.isBlob(path) or ResourcePath.isReal(path):
            return None
        self.setupDefault()
        key = ResourcePath.normalize(path)
        with self._lock:
            found = self.files.get(key)
            if found is None:
                real_key = self._lower.get(key.lower())
                if real_key is not None:
                    found = self.files.get(real_key)
        return found

    def setEntry(self, path: str, source) -> ResourceEntry:
        """注册 / 替换一个条目 —— 虚拟文件映射的"写入口"。

        ``source`` 可以是：条目对象 / ``bytes`` / 任意 ``callable`` / 真实文件路径
        （见 :meth:`ResourceEntry.of`）。放进去的东西**不会被 :meth:`rebuild` 覆盖**，
        于是"自定义加密文件 / 格式转换 / 运行时生成"都能直接挂到某条虚拟路径上:

        ::

            manager = Resources.manager()
            manager.setEntry("res/save/player.dat", MyDecryptEntry(...))
            manager.setEntry("res/gen/hello.txt", lambda: b"hello")
        """
        key = ResourcePath.normalize(path)
        entry = ResourceEntry.of(key, source)
        with self._lock:
            self._custom[key] = entry
            self.files[key] = entry
            self._lower[key.lower()] = key
        return entry

    def removeEntry(self, path: str) -> bool:
        """撤掉 :meth:`setEntry` 注册的条目（挂载点扫出来的那些不受影响）。"""
        key = ResourcePath.normalize(path)
        with self._lock:
            removed = self._custom.pop(key, None) is not None
        if removed:
            self.rebuild()
        return removed

    def entries(self) -> dict:
        """当前虚拟文件映射的**副本**（遍历 / 排查用）。"""
        self.setupDefault()
        with self._lock:
            return dict(self.files)

    @staticmethod
    def dataOf(entry):
        """把映射里的值（条目 / 任意可调用对象 / ``bytes``）变成字节。"""
        if entry is None:
            return None
        if isinstance(entry, (bytes, bytearray, memoryview)):
            return bytes(entry)
        data = entry()
        return data if data is not None else b""

    # === 定位 ===

    def find(self, path: str):
        """逻辑路径 → ``(Mount, 内部名字)``；没有挂载认领它时返回 ``None``。

        **第一个"真的存在这个条目"的挂载生效**（不是第一个前缀匹配的）——
        这样"目录里没有、包里才有"的兜底不会失效。
        """
        self.setupDefault()
        path = str(path)
        if ResourcePath.isBlob(path) or ResourcePath.isReal(path):
            return None
        with self._lock:
            mounts = tuple(self._mounts)
        for mount in mounts:
            if not mount.contains(path):
                continue
            inner = mount.innerName(path)
            if mount.exists(inner):
                return mount, inner
        return None

    def findMount(self, prefix: str):
        """按前缀取挂载点（不看条目是否存在），用于"列出这个前缀下有什么"。"""
        self.setupDefault()
        prefix = ResourcePath.normalize(prefix)
        with self._lock:
            for mount in self._mounts:
                if mount.prefix == prefix:
                    return mount
        return None

    # === 读取 / 查询（逻辑路径与真实路径都吃） ===

    def readBytes(self, path: str):
        """读一个资源的全部字节；不存在返回 ``None``。

        顺序见模块文档：虚拟文件映射 → 挂载表 → 真实文件系统
        （后者保持老代码"相对 CWD / 相对项目根"的普通路径语义）。
        """
        path = str(path)
        if ResourcePath.isBlob(path):
            return b""
        entry = self.entry(path)
        if entry is not None:
            data = self._readEntry(entry)
            if data is not None:
                return data
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return mount.read(inner)
        if ResourcePath.isReal(path):
            return self._readReal(path)
        data = self._readReal(path)
        if data is None:
            data = self._readReal(os.path.join(app_root(), path))
        return data

    @staticmethod
    def _readEntry(entry):
        """从映射里的条目取字节；**"条目已经不存在"时返回 ``None``**。

        返回 ``None`` 是为了让 :meth:`readBytes` 继续往下兜底：目录形态下把某个
        文件删掉之后，映射里那条旧条目会失效，这时应该交给下一个挂载点（比如封包）
        去回答 —— 而不是回一个空 ``bytes``。
        """
        if isinstance(entry, ResourceEntry) and not entry.exists():
            return None
        data = ResourceManager.dataOf(entry)
        return data if data is not None else b""

    @staticmethod
    def _readReal(path: str):
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        """这个资源存在吗（映射里的自定义条目也算存在）。"""
        path = str(path)
        if ResourcePath.isBlob(path):
            return True
        entry = self.entry(path)
        if entry is not None:
            if not isinstance(entry, ResourceEntry) or entry.exists():
                return True
        if self.find(path) is not None:
            return True
        if ResourcePath.isReal(path):
            return os.path.exists(path)
        return (os.path.exists(path) or
                os.path.exists(os.path.join(app_root(), path)))

    def isDir(self, path: str) -> bool:
        path = str(path)
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return mount.isDir(inner)
        if ResourcePath.isReal(path):
            return os.path.isdir(path)
        return (os.path.isdir(path) or
                os.path.isdir(os.path.join(app_root(), path)))

    def listDir(self, path: str) -> list:
        """列出目录下的直接子项（名字，不含目录前缀）；不是目录返回 ``[]``。

        目录形态 / 封包形态一致；另外会把 :meth:`setEntry` 注册在这个目录下的
        "虚拟文件"一起列出来（否则自定义资源在图集 / 材质搜索里会"看不见"）。
        """
        path = str(path)
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return self._mergeVirtual(path, mount.listDir(inner))
        for candidate in (path, os.path.join(app_root(), path)):
            if os.path.isdir(candidate):
                try:
                    return self._mergeVirtual(path, sorted(os.listdir(candidate)))
                except OSError:
                    return []
        return self._mergeVirtual(path, [])

    def _mergeVirtual(self, path: str, listed: list) -> list:
        """把映射里注册在 ``path`` 下的"虚拟文件"并进列目录结果。"""
        prefix = ResourcePath.normalize(path)
        prefix = prefix + "/" if prefix else ""
        names = list(listed)
        with self._lock:
            keys = tuple(self._custom)
        for key in keys:
            if prefix and not key.startswith(prefix):
                continue
            head = key[len(prefix):].split("/")[0]
            if head and head not in names:
                names.append(head)
        return sorted(names)

    def realPath(self, path: str):
        """真实文件路径；**封包里的条目返回 ``None``**。

        只在"必须把文件路径交给别人"的特殊情况下用（未封包的外部工具、子进程等）。
        能用 :meth:`readBytes` 就不要用它 —— 封包之后拿不到真实路径。
        """
        path = str(path)
        if ResourcePath.isReal(path):
            return path
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return mount.realPath(inner)
        entry = self.entry(path)
        if entry is not None and isinstance(entry, ResourceEntry):
            real = entry.realPath()
            if real:
                return real
        for candidate in (path, os.path.join(app_root(), path)):
            if os.path.exists(candidate):
                return candidate
        return None

    def cacheKey(self, path: str) -> str:
        """缓存键：**解析到哪就按哪算**（目录与包互不串味，绝对路径保持原样）。"""
        path = str(path)
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return "logical:%s:%s" % (ResourcePath.normcase(mount.path),
                                      ResourcePath.normcase(inner))
        entry = self.entry(path)
        if entry is not None:
            return "virtual:%s" % ResourcePath.normcase(ResourcePath.normalize(path))
        return "file:%s" % ResourcePath.normcase(os.path.abspath(path))

    def describe(self, path: str) -> str:
        """给日志 / 报错用的一句话："这个逻辑路径落到哪了"。"""
        path = str(path)
        if ResourcePath.isBlob(path):
            return "blob"
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return "%s:%s" % (mount.kind, os.path.join(mount.path, inner.replace("/", os.sep)))
        if ResourcePath.isReal(path):
            return "file:%s" % path
        entry = self.entry(path)
        if entry is not None:
            return "virtual:%s" % ResourcePath.normalize(path)
        for candidate in (os.path.abspath(path), os.path.join(app_root(), path)):
            if os.path.exists(candidate):
                return "file:%s" % candidate
        return "missing:%s" % path

    def __str__(self) -> str:
        return "ResourceManager<%d mounts, %d files>" % (len(self._mounts), len(self.files))
