"""引擎的资源入口（旧的 ``resources`` 模块的替代品）：**全是类方法 / 静态方法**。

调用方只写**逻辑路径**（``res/shader/dis.vert``）::

    from pyglgame.resources.Resources import Resources

    data = Resources.readBytes("res/img/star.png")     # 目录形态 / 封包形态都吃
    Resources.exists("res/atlas/hero.json")
    Resources.listDir("res/atlas")

读取由 :class:`~pyglgame.resources.ResourceManager.ResourceManager` 决定这个路径落到
``res/`` 目录还是 ``res.xypack`` 资源包 —— "开发时用目录、发行时用封包"不需要改任何
游戏代码（见 ``docs/资源包.md``）。

定制（不需要改引擎）
--------------------

* **虚拟文件映射** —— ``Resources.manager().files`` / :meth:`Resources.setEntry`：
  替换某个文件的"可调用条目"，做解密 / 格式转换 / 运行时生成
  （见 :mod:`pyglgame.resources.ResourceEntry`）；
* **封包 Loader** —— :meth:`Resources.setPackLoader` 或 :meth:`Resources.mount`
  的 ``loader=``：换成自己的封包格式（见 :mod:`pyglgame.resources.ResourceLoader`）；
* **独立的一张表** —— ``ResourceManager(autoload=False)`` + :meth:`Resources.use`。
"""

from .ResourceEntry import ResourceEntry
from .ResourceManager import Mount, ResourceManager
from .ResourcePath import ResourcePath


class Resources:
    """资源门面：常量 + 路径工具（静态方法）+ 读写查询（类方法，走全局管理器）。"""

    #: 默认挂载前缀 / 目录名 / 封包名（``res``、``res``、``res.xypack``）
    DEFAULT_PREFIX = ResourceManager.DEFAULT_PREFIX
    DEFAULT_DIR = ResourceManager.DEFAULT_DIR
    DEFAULT_PACK = ResourceManager.DEFAULT_PACK

    # === 全局管理器 ===

    @classmethod
    def manager(cls) -> ResourceManager:
        """取全局 :class:`ResourceManager`（挂载表与虚拟文件映射都在它身上）。"""
        return ResourceManager.shared()

    @classmethod
    def use(cls, manager: ResourceManager = None) -> ResourceManager:
        """换掉全局管理器（测试 / 沙盒；``None`` = 新建一张空表）。"""
        return ResourceManager.use(manager)

    # === 挂载 ===

    @classmethod
    def mount(cls, prefix, target=None, kind=None, loader=None, front=False) -> Mount:
        """挂载一个目录 / 资源包 / 自定义来源（``loader=`` 可换成自己的 Loader）。"""
        return cls.manager().mount(prefix, target, kind=kind, loader=loader, front=front)

    @classmethod
    def unmount(cls, prefix) -> bool:
        """卸掉某个前缀上的挂载。"""
        return cls.manager().unmount(prefix)

    @classmethod
    def mounts(cls) -> tuple:
        """当前所有挂载点（会先执行约定挂载）。"""
        return cls.manager().mounts()

    @classmethod
    def clear(cls) -> None:
        """清空挂载表与虚拟文件映射（测试 / 重来用）。"""
        cls.manager().clear()

    @classmethod
    def setupDefault(cls, force: bool = False) -> tuple:
        """按约定挂载项目自带的 ``res/`` 与 ``res.xypack``（见 ResourceManager）。"""
        return cls.manager().setupDefault(force=force)

    # === Loader 定制（自定义封包格式 / 目录来源） ===

    @classmethod
    def registerLoader(cls, loader_class, first: bool = False) -> tuple:
        """把自定义 Loader 加进挂载时的"自动识别"顺序。"""
        return ResourceManager.registerLoader(loader_class, first=first)

    @classmethod
    def setPackLoader(cls, loader_class) -> type:
        """替换**封包**默认 Loader（自定义封包格式走这里）。"""
        return ResourceManager.setPackLoader(loader_class)

    @classmethod
    def setDirectoryLoader(cls, loader_class) -> type:
        """替换**目录**默认 Loader。"""
        return ResourceManager.setDirectoryLoader(loader_class)

    # === 虚拟文件映射（虚拟路径 → 可调用条目） ===

    @classmethod
    def files(cls) -> dict:
        """当前**虚拟文件映射**（``{"res/dir/file": 可调用对象}``，可直接改）。"""
        return cls.manager().files

    @classmethod
    def rebuild(cls) -> dict:
        """重新扫一遍来源，重建虚拟文件映射。"""
        return cls.manager().rebuild()

    @classmethod
    def refresh(cls) -> dict:
        """:meth:`rebuild` 的别名（新增 / 删除的文件立即反映进来）。"""
        return cls.manager().refresh()

    @classmethod
    def entry(cls, path) -> ResourceEntry:
        """按虚拟路径取映射里的条目（可调用对象）；没有返回 ``None``。"""
        return cls.manager().entry(path)

    @classmethod
    def setEntry(cls, path, source) -> ResourceEntry:
        """注册 / 替换一个条目：``source`` = 条目 / ``bytes`` / ``callable`` / 路径。"""
        return cls.manager().setEntry(path, source)

    @classmethod
    def removeEntry(cls, path) -> bool:
        """撤掉 :meth:`setEntry` 注册的条目。"""
        return cls.manager().removeEntry(path)

    # === 读取 / 查询（逻辑路径与真实路径都吃） ===

    @classmethod
    def readBytes(cls, path):
        """读全部字节；不存在返回 ``None``。"""
        return cls.manager().readBytes(path)

    @classmethod
    def exists(cls, path) -> bool:
        return cls.manager().exists(path)

    @classmethod
    def isDir(cls, path) -> bool:
        return cls.manager().isDir(path)

    @classmethod
    def listDir(cls, path) -> list:
        return cls.manager().listDir(path)

    @classmethod
    def realPath(cls, path):
        """真实文件路径；封包里的条目返回 ``None``。"""
        return cls.manager().realPath(path)

    @classmethod
    def cacheKey(cls, path) -> str:
        """缓存键："解析到哪就按哪算"（目录与包互不串味）。"""
        return cls.manager().cacheKey(path)

    @classmethod
    def describe(cls, path) -> str:
        """给日志 / 报错用的一句话："这个逻辑路径落到哪了"。"""
        return cls.manager().describe(path)

    # === 路径工具（纯字符串，静态） ===

    @staticmethod
    def normalizeEntry(name) -> str:
        """统一成 ``a/b/c``（正斜杠、去 ``.``、``..`` 就地化简）。"""
        return ResourcePath.normalize(name)

    @staticmethod
    def joinPath(*parts) -> str:
        """拼接路径片段（后一段是绝对路径时以它为准）。"""
        return ResourcePath.join(*parts)

    @staticmethod
    def dirnamePath(path) -> str:
        """逻辑路径的"所在目录"。"""
        return ResourcePath.dirname(path)

    @staticmethod
    def basenamePath(path) -> str:
        """逻辑路径的"文件名"。"""
        return ResourcePath.basename(path)

    @staticmethod
    def isRealPath(path) -> bool:
        """是不是"真实文件系统路径"（绝对路径一律算）。"""
        return ResourcePath.isReal(path)

    @staticmethod
    def isLogicalPath(path, prefix: str = None) -> bool:
        """是不是"该走挂载表"的逻辑路径（给了 ``prefix`` 则问"落不落在它下面"）。"""
        return ResourcePath.isLogical(path, prefix)

    @staticmethod
    def isBlobPath(path) -> bool:
        """是不是 ``blob:`` 伪路径。"""
        return ResourcePath.isBlob(path)
