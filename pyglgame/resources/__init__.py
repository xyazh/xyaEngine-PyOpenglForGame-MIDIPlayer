"""逻辑资源路径 → 字节 / 目录信息（目录形态与资源包形态对上层**完全一致**）。

为什么要有这一层
----------------

原先引擎里到处是 ``res_path("shader", "dis.vert")`` —— 它把"资源在哪"
（项目根 + ``res`` 子目录）焊死在每个调用点，于是：

* 打包/换工作目录/单独分发资源都会出事（Nuitka 那条路径就是这么踩的）；
* 资源只能躺在磁盘上，没法封成一个包再分发。

现在改成：**调用方只写逻辑路径**（``res/shader/dis.vert``），由这里决定去哪拿：

========================  ============================================
``<项目根>/res/``（目录）   开发时的"作者格式"，改完立刻生效
``<项目根>/res.xypack``     发行格式（一个 zip 容器，见 ResourcePack）
``XYA_RES_DIR`` 环境变量    临时指到别的目录（调试/覆盖用）
``XYA_RES_PACK`` 环境变量   临时指到别的包
``XYA_RES_PREFER=pack``     两个都在时优先用包（默认优先目录）
========================  ============================================

规则（踩坑换来的，别改）
------------------------

1. **绝对路径、盘符、UNC、``blob:`` 不走挂载表**，按真实文件系统处理 ——
   游戏自己的产物（``output.mp4``、``profile.prof``）、用户塞进来的外部贴图
   本来就该走真实路径，这是"非特殊情况"里的"特殊情况"。
2. **挂载按注册顺序匹配**，第一个"真的存在这个条目"的挂载生效；都不存在时
   退回真实文件系统（保持老代码里"相对 CWD 的普通路径"语义不变）。
3. 条目名统一用正斜杠、去 ``.``、``..`` 就地化简（``res/scene/../atlas`` →
   ``res/atlas``），这样 Windows / Linux 写出来的逻辑路径一致。
"""

import os
import threading

from ..tools.paths import app_root
from .ResourcePack import (MANIFEST_VERSION, PACK_SUFFIX, TEXT_SUFFIXES,
                           ResourcePack, compressionFor, entryNames,
                           isPackFile, listPack, matchesAny, normalizeEntry,
                           packDirectory, readManifest)

#: 默认资源目录名（项目根下）
DEFAULT_RES_DIR = "res"

#: 默认资源包文件名（项目根下）
DEFAULT_PACK_NAME = DEFAULT_RES_DIR + PACK_SUFFIX

#: 挂载前缀：逻辑路径的第一段
DEFAULT_PREFIX = DEFAULT_RES_DIR

#: 显式指定资源目录 / 资源包 / 优先级的环境变量
ENV_DIR = "XYA_RES_DIR"
ENV_PACK = "XYA_RES_PACK"
ENV_PREFER = "XYA_RES_PREFER"


def isRealPath(path) -> bool:
    """是不是"真实文件系统路径"（绝对路径一律算；含盘符与 UNC）。"""
    return os.path.isabs(str(path))


def joinPath(*parts) -> str:
    """拼接路径片段，返回**真实习惯的分隔符**（Windows 上就是反斜杠）。

    与 :func:`os.path.join` 的唯一区别：**后一段是绝对路径时以它为准**
    （场景文档里偶尔会引用绝对路径的外部贴图）。

    ⚠ 这里刻意**不**产出"正斜杠的逻辑路径"：逻辑性（首段是不是挂载前缀）
    由 :mod:`pyglgame.resources` 在查找时用 :func:`normalizeEntry` 归一化，
    上层不用、也不该关心分隔符长什么样 —— 否则路径字符串形态会扩散到全引擎，
    "同一份资源两种写法"就会到处冒出来。
    """
    result = ""
    for part in parts:
        part = str(part or "")
        if not part:
            continue
        if isRealPath(part):
            result = part                  # 绝对路径出现 → 以它为准（前面积累的作废）
            continue
        if result in ("", "."):
            result = part
        else:
            result = result.rstrip("/\\") + "/" + part.lstrip("/\\")
    if not result:
        return ""
    return os.path.normpath(result)


def basenamePath(path) -> str:
    """逻辑路径 / 真实路径的"文件名"（最后一段）。"""
    text = str(path).rstrip("/\\")
    if isRealPath(text):
        return os.path.basename(text)
    if "/" not in text and "\\" not in text:
        return text
    return text.replace("\\", "/").rsplit("/", 1)[-1]


def dirnamePath(path) -> str:
    """逻辑路径的"所在目录"（真实路径走 :func:`os.path.dirname`）。"""
    path = str(path)
    if isRealPath(path):
        return os.path.dirname(path)
    normalized = normalizeEntry(path)
    if "/" not in normalized:
        return ""
    return normalized.rsplit("/", 1)[0]


def isLogicalPath(path, prefix: str = None) -> bool:
    """是不是"该走挂载表"的逻辑路径（非绝对路径）。"""
    path = str(path or "")
    if not path or path[:5] == "blob:" or isRealPath(path):
        return False
    if prefix is None:
        return True
    normalized = normalizeEntry(path)
    return normalized == prefix or normalized.startswith(prefix + "/")


class Mount:
    """一个挂载点：``前缀 -> 目录 / 资源包``。"""

    def __init__(self, prefix: str, kind: str, path: str, pack: ResourcePack = None) -> None:
        self.prefix = normalizeEntry(prefix)
        self.kind = kind                     # "dir" | "pack"
        self.path = os.path.abspath(path)    # 真实路径（目录或包），报错时用得上
        self.pack = pack
        self._lock = threading.RLock()

    # === 内部条目访问（inner 相对挂载根） ===

    def inner(self, logical: str) -> str:
        logical = normalizeEntry(logical)
        if logical == self.prefix:
            return ""
        if not self.prefix:
            return logical
        return logical[len(self.prefix) + 1:]

    def contains(self, logical: str) -> bool:
        logical = normalizeEntry(logical)
        if not self.prefix:
            return True
        return logical == self.prefix or logical.startswith(self.prefix + "/")

    def exists(self, inner: str) -> bool:
        if self.kind == "pack":
            return inner == "" or self.pack.exists(inner) or self.isDir(inner)
        return os.path.exists(self.realPath(inner)) if inner else os.path.isdir(self.path)

    def isDir(self, inner: str) -> bool:
        if self.kind == "pack":
            return inner == "" or self.pack.isDir(inner)
        return os.path.isdir(self.realPath(inner)) if inner else os.path.isdir(self.path)

    def realPath(self, inner: str) -> str:
        if not inner:
            return self.path
        return os.path.join(self.path, inner.replace("/", os.sep))

    def read(self, inner: str):
        if self.kind == "pack":
            with self._lock:
                return self.pack.read(inner)
        try:
            with open(self.realPath(inner), "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def listDir(self, inner: str) -> list:
        if self.kind == "pack":
            return self.pack.children(inner)
        try:
            return sorted(os.listdir(self.realPath(inner)))
        except OSError:
            return []

    def close(self) -> None:
        if self.pack is not None:
            self.pack.close()

    def __str__(self) -> str:
        return "Mount<%s -> %s %s>" % (self.prefix or "/", self.kind, self.path)


class ResourceManager:
    """挂载表 + 统一的读取/列目录接口（见模块文档）。"""

    def __init__(self) -> None:
        self._mounts: list = []
        self._lock = threading.RLock()
        self._defaults_ready = False
        #: 被"显式 mount()"过的前缀：约定挂载（setupDefault）不再往这些前缀上放东西
        self._explicit: set = set()

    # === 挂载管理 ===

    def mount(self, prefix: str, target: str = None, kind: str = None,
              front: bool = False):
        """显式挂载一个目录或资源包（**优先于约定挂载**：``setupDefault`` 不再动这个前缀）。

        ``kind`` 不给时按"目录优先、包其次"自动判断。
        """
        mount = self._mountInternal(prefix, target, kind=kind, front=front)
        with self._lock:
            self._explicit.add(mount.prefix)
        return mount

    def _mountInternal(self, prefix: str, target: str = None, kind: str = None,
                       front: bool = False):
        if target is None:
            target = prefix
            prefix = DEFAULT_PREFIX
        prefix = normalizeEntry(prefix)
        target = str(target)
        if not isRealPath(target):
            target = os.path.join(app_root(), target)
        if kind is None:
            if os.path.isdir(target):
                kind = "dir"
            elif isPackFile(target):
                kind = "pack"
            else:
                raise FileNotFoundError(
                    "既不是目录也不是资源包（%s）：需要一个目录，或一个由 "
                    "tools/pack_resources.py 生成的 *.xypack" % target)
        if kind == "pack":
            mount = Mount(prefix, "pack", target, pack=ResourcePack(target))
        else:
            if not os.path.isdir(target):
                raise FileNotFoundError("要挂载的目录不存在: %s" % target)
            mount = Mount(prefix, "dir", target)
        with self._lock:
            self.unmount(prefix, close=True)
            if front:
                self._mounts.insert(0, mount)
            else:
                self._mounts.append(mount)
        return mount

    def unmount(self, prefix: str, close: bool = True) -> bool:
        prefix = normalizeEntry(prefix)
        removed = False
        with self._lock:
            for mount in list(self._mounts):
                if mount.prefix == prefix:
                    self._mounts.remove(mount)
                    if close:
                        mount.close()
                    removed = True
        return removed

    def clear(self) -> None:
        with self._lock:
            for mount in self._mounts:
                mount.close()
            self._mounts.clear()
            self._explicit.clear()
        self._defaults_ready = False

    def mounts(self) -> tuple:
        self.setupDefault()
        with self._lock:
            return tuple(self._mounts)

    def setupDefault(self, force: bool = False) -> tuple:
        """按约定挂载项目自带的 ``res/``（目录）与 ``res.xypack``（包）。

        只在第一次真正用到资源时执行；两个都在时**优先目录**（开发时改完
        立刻生效），``XYA_RES_PREFER=pack`` 可以反过来（验证"包里那份"）。
        """
        if self._defaults_ready and not force:
            with self._lock:
                return tuple(self._mounts)
        self._defaults_ready = True
        with self._lock:
            if DEFAULT_PREFIX in self._explicit:
                # 用户已经显式挂过这个前缀了（例如把包指到别处/挂测试包），
                # 约定挂载不许把它顶掉
                return tuple(self._mounts)
        root = app_root()
        directory = os.environ.get(ENV_DIR) or os.path.join(root, DEFAULT_RES_DIR)
        pack = os.environ.get(ENV_PACK) or os.path.join(root, DEFAULT_PACK_NAME)
        prefer_pack = str(os.environ.get(ENV_PREFER, "")).lower() in ("pack", "1", "true")

        plan = []
        if prefer_pack and isPackFile(pack):
            plan.append(("pack", pack))
        if os.path.isdir(directory):
            plan.append(("dir", directory))
        if not prefer_pack and isPackFile(pack):
            plan.append(("pack", pack))
        for kind, target in plan:
            try:
                self._mountInternal(DEFAULT_PREFIX, target, kind=kind)
            except (OSError, ValueError):
                pass
        with self._lock:
            return tuple(self._mounts)

    # === 定位 ===

    def find(self, path: str):
        """逻辑路径 → ``(Mount, 条目名)``；没有挂载认领它时返回 ``None``。

        **第一个"真的存在这个条目"的挂载生效**（不是第一个前缀匹配的）——
        这样"目录里没有、包里才有"的兜底不会失效。
        """
        self.setupDefault()
        if isRealPath(path) or str(path)[:5] == "blob:":
            return None
        with self._lock:
            mounts = tuple(self._mounts)
        for mount in mounts:
            if not mount.contains(path):
                continue
            inner = mount.inner(path)
            if mount.exists(inner):
                return mount, inner
        return None

    def findMount(self, prefix: str):
        """按前缀取挂载点（不看条目是否存在），用于"列出这个前缀下有什么"。"""
        self.setupDefault()
        prefix = normalizeEntry(prefix)
        with self._lock:
            for mount in self._mounts:
                if mount.prefix == prefix:
                    return mount
        return None

    # === 读取 / 目录查询（逻辑路径与真实路径都吃） ===

    def readBytes(self, path: str):
        """读一个资源的全部字节；不存在返回 ``None``。

        逻辑路径 → 挂载表；绝对路径 / 挂载表里没有的相对路径 → 真实文件系统
        （后者保持老代码"相对 CWD 的普通路径"语义）。
        """
        path = str(path)
        if path[:5] == "blob:":
            return b""
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return mount.read(inner)
        if isRealPath(path):
            return self._readReal(path)
        # 没被任何挂载认领的相对路径：先按"相对 CWD"读（兼容老代码）
        data = self._readReal(path)
        if data is None:
            data = self._readReal(os.path.join(app_root(), path))
        return data

    @staticmethod
    def _readReal(path: str):
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        path = str(path)
        if path[:5] == "blob:":
            return True
        if self.find(path) is not None:
            return True
        if isRealPath(path):
            return os.path.exists(path)
        return (os.path.exists(path) or
                os.path.exists(os.path.join(app_root(), path)))

    def isDir(self, path: str) -> bool:
        path = str(path)
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return mount.isDir(inner)
        if isRealPath(path):
            return os.path.isdir(path)
        return (os.path.isdir(path) or
                os.path.isdir(os.path.join(app_root(), path)))

    def listDir(self, path: str) -> list:
        """列出目录下的直接子项（名字，不含目录前缀）；不是目录返回 ``[]``。"""
        path = str(path)
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return mount.listDir(inner)
        for candidate in (path, os.path.join(app_root(), path)):
            if os.path.isdir(candidate):
                try:
                    return sorted(os.listdir(candidate))
                except OSError:
                    return []
        return []

    def realPath(self, path: str):
        """目录挂载/真实路径能给出真实文件路径；**包里的条目返回 ``None``**。

        个别第三方接口只吃真实路径（例如直接丢给别的进程/C 库），那种情况下
        要么改用 :meth:`readBytes`（推荐），要么让资源保持目录形态。
        """
        path = str(path)
        if isRealPath(path):
            return path
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return mount.realPath(inner) if mount.kind == "dir" else None
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
            return "logical:%s:%s" % (normcase(mount.path), normcase(inner))
        if isRealPath(path):
            return "file:%s" % normcase(os.path.abspath(path))
        return "file:%s" % normcase(os.path.abspath(path))

    def describe(self, path: str) -> str:
        """给日志/报错用的一句话："这个逻辑路径落到哪了"。"""
        path = str(path)
        if path[:5] == "blob:":
            return "blob"
        found = self.find(path)
        if found is not None:
            mount, inner = found
            return "%s:%s" % (mount.kind, os.path.join(mount.path, inner))
        if isRealPath(path):
            return "file:%s" % path
        for candidate in (os.path.abspath(path), os.path.join(app_root(), path)):
            if os.path.exists(candidate):
                return "file:%s" % candidate
        return "missing:%s" % path


def normcase(path: str) -> str:
    """缓存键里统一大小写（Windows 不区分大小写，避免同一文件两个缓存条目）。"""
    return os.path.normcase(str(path)).replace("\\", "/")


#: 全局唯一的挂载表（引擎与 demo 都通过它拿资源）
_manager = ResourceManager()


def manager() -> ResourceManager:
    """取全局 :class:`ResourceManager`。"""
    return _manager


# === 模块级快捷方式（内部都转到全局 manager） ===

def mount(prefix: str, target: str = None, kind: str = None, front: bool = False):
    return _manager.mount(prefix, target, kind=kind, front=front)


def unmount(prefix: str) -> bool:
    return _manager.unmount(prefix)


def mounts() -> tuple:
    return _manager.mounts()


def setupDefault(force: bool = False) -> tuple:
    return _manager.setupDefault(force=force)


def readBytes(path: str):
    return _manager.readBytes(path)


def exists(path: str) -> bool:
    return _manager.exists(path)


def isDir(path: str) -> bool:
    return _manager.isDir(path)


def listDir(path: str) -> list:
    return _manager.listDir(path)


def realPath(path: str):
    return _manager.realPath(path)


def cacheKey(path: str) -> str:
    return _manager.cacheKey(path)


def describe(path: str) -> str:
    return _manager.describe(path)


