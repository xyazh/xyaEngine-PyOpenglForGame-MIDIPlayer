"""资源包（``*.xypack``）：把 ``res/`` 目录打成一个可随机读取的包。

为什么是 zip，而不是自定义格式
------------------------------

* 标准库 :mod:`zipfile` 自带「索引 + 随机读取 + 解压」，**零新依赖**，
  也不必自己实现偏移表/校验和（那类代码是自己造 bug 的重灾区）；
* 条目名就是相对 ``res/`` 的路径（``shader/dis.frag``），语义与"目录形态"
  完全一致 —— 于是 :class:`~pyglgame.ResourceLocation.ResourceLocation`
  只需要换一个"读字节"的来源，其它逻辑一行都不用改；
* 任何解压工具都能打开、替换单个条目、比对内容（"包里的贴图是不是旧的"
  这类排查很省事）。

两个刻意的约定
--------------

1. **清单放在 zip 的 comment 里**（JSON），而不是塞一个 ``pack.json`` 条目。
   后者会在 ``res/`` 命名空间里凭空多一个文件，扫描目录（例如按名字找图集、
   列目录里现成的图集）时会碍事。
2. **默认 ``store`` 不压缩**：包里的东西大半是 png/ogg/mp4（已经压过），
   再 deflate 只是白费构建时间；文本类（shader/json）想压可以 ``--compress``。

写入是**确定性**的：条目按名字排序、时间戳固定成 1980-01-01、权限位固定，
同样的输入永远得到逐字节相同的包（便于比对"这次构建到底改了什么"）。
"""

import fnmatch
import json
import os
import time
import zipfile

#: 包的后缀（约定用 ``res.xypack``）
PACK_SUFFIX = ".xypack"

#: 清单版本：读取端按它判断"这个包是不是我认得的格式"
MANIFEST_VERSION = 1

#: 打包时固定用的时间戳（zip 的最早合法值），保证可复现
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

#: ``--compress auto`` 会压的文本类后缀（其余一律 store）
TEXT_SUFFIXES = (".glsl", ".frag", ".vert", ".comp", ".json", ".txt", ".md",
                 ".toml", ".yaml", ".yml", ".scene", ".tiled", ".csv", ".xml")

#: 打包时永远跳过的目录
SKIP_DIRS = ("__pycache__", ".git", ".svn", ".idea", ".vscode")


def normalizeEntry(name: str) -> str:
    """把条目名统一成 ``a/b/c``（正斜杠、去点、去首尾斜杠）。"""
    name = str(name).replace("\\", "/")
    parts = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == ".." and parts:
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def matchesAny(name: str, patterns) -> bool:
    """``fnmatch`` 风格的包含/排除匹配；``dir/**`` 也匹配目录本身。"""
    if not patterns:
        return False
    for pattern in patterns:
        pattern = str(pattern).replace("\\", "/").strip()
        if not pattern:
            continue
        if pattern.endswith("/**"):
            head = pattern[:-3].rstrip("/")
            if name == head or name.startswith(head + "/"):
                return True
        if fnmatch.fnmatchcase(name, pattern):
            return True
        # ``*.png`` 这种没有目录的写法，也允许匹配任意深度
        if "/" not in pattern and fnmatch.fnmatchcase(name.rsplit("/", 1)[-1], pattern):
            return True
    return False


def entryNames(source: str, include=(), exclude=()) -> list:
    """列出 ``source`` 目录下要进包的条目名（已排序、已过滤）。"""
    names = []
    for root, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for filename in sorted(files):
            full = os.path.join(root, filename)
            rel = normalizeEntry(os.path.relpath(full, source))
            if include and not matchesAny(rel, include):
                continue
            if exclude and matchesAny(rel, exclude):
                continue
            names.append(rel)
    names.sort()
    return names


def compressionFor(name: str, compress: str) -> int:
    """按 ``compress`` 策略决定某个条目的压缩方式。"""
    mode = str(compress or "store").lower()
    if mode in ("deflate", "zip_deflated"):
        return zipfile.ZIP_DEFLATED
    if mode == "auto" and name.lower().endswith(TEXT_SUFFIXES):
        return zipfile.ZIP_DEFLATED
    return zipfile.ZIP_STORED


def packDirectory(source: str, target: str, include=(), exclude=(),
                  compress: str = "store", extra: dict = None) -> dict:
    """把 ``source`` 目录打进 ``target`` 包，返回统计信息（dict）。

    ``source`` / ``target`` 由调用方给（一般是相对项目根的路径，或者由
    ``__file__`` 推出来的路径），本函数不写死任何绝对路径。
    """
    source = os.path.abspath(source)
    if not os.path.isdir(source):
        raise FileNotFoundError("要打包的目录不存在: %s" % source)

    names = entryNames(source, include=include, exclude=exclude)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)

    source_bytes = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in names:
            full = os.path.join(source, name.replace("/", os.sep))
            with open(full, "rb") as handle:
                data = handle.read()
            source_bytes += len(data)
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE_TIME)
            info.external_attr = 0o644 << 16      # 权限位固定，保证可复现
            info.compress_type = compressionFor(name, compress)
            archive.writestr(info, data)

        manifest = {
            "format": "xypack",
            "version": MANIFEST_VERSION,
            "tool": "tools/pack_resources.py",
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "compress": str(compress or "store").lower(),
            "root": os.path.basename(source),
            "files": len(names),
            "bytes": source_bytes,
            "include": list(include or ()),
            "exclude": list(exclude or ()),
        }
        if extra:
            manifest.update(extra)
        archive.comment = json.dumps(manifest, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")

    pack_bytes = os.path.getsize(target)
    return {"source": source, "target": os.path.abspath(target), "files": len(names),
            "bytes": source_bytes, "pack_bytes": pack_bytes,
            "ratio": (pack_bytes / source_bytes) if source_bytes else 0.0}


def readManifest(path: str) -> dict:
    """读包清单（坏包/非包返回 ``None``）。"""
    try:
        with zipfile.ZipFile(path) as archive:
            comment = archive.comment
    except (OSError, zipfile.BadZipFile):
        return None
    if not comment:
        return None
    try:
        manifest = json.loads(comment.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def isPackFile(path: str) -> bool:
    """判断"这个路径看起来是不是资源包"（后缀对 + 是合法 zip）。"""
    if not os.path.isfile(path):
        return False
    if not path.lower().endswith(PACK_SUFFIX):
        return False
    return zipfile.is_zipfile(path)


def listPack(path: str) -> list:
    """列出包里的条目：``[(名字, 解压后字节数), ...]``（按名字排序）。"""
    with zipfile.ZipFile(path) as archive:
        return sorted((info.filename, info.file_size) for info in archive.infolist())


class ResourcePack:
    """只读资源包。条目名与"目录形态"下的相对路径一一对应。

    句柄会一直开着（资源的读取发生在启动/首次使用时，重复 open 一次包
    在几十 MB 的量级上并不便宜），:meth:`close` 显式释放。
    `importlib.resources` 那种"每次读都重新定位"的做法在这里没必要。
    """

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        self._archive = None
        self._index = {}
        self._lower = {}
        self._dirs = None
        self.manifest = None
        self._open()

    def _open(self) -> None:
        if not os.path.isfile(self.path):
            raise FileNotFoundError("资源包不存在: %s" % self.path)
        try:
            self._archive = zipfile.ZipFile(self.path, "r")
        except zipfile.BadZipFile as error:
            raise ValueError(
                "资源包不是合法的 zip（%s）：%s\n"
                "重新生成一个：python tools/pack_resources.py" % (self.path, error))
        self.manifest = readManifest(self.path)
        for info in self._archive.infolist():
            if info.is_dir():
                continue
            name = normalizeEntry(info.filename)
            self._index[name] = info
            self._lower.setdefault(name.lower(), name)

    # === 查询 ===

    def names(self) -> list:
        return sorted(self._index)

    def entryName(self, name: str):
        """逻辑条目名 → 包里真实存在的名字（大小写不敏感兜底，Win 友好）。"""
        name = normalizeEntry(name)
        if name in self._index:
            return name
        return self._lower.get(name.lower())

    def exists(self, name: str) -> bool:
        return self.entryName(name) is not None

    def size(self, name: str) -> int:
        entry = self.entryName(name)
        return self._index[entry].file_size if entry else 0

    def directories(self) -> list:
        """包里出现过的所有目录（``a``、``a/b`` …），用于"按目录找东西"。"""
        if self._dirs is None:
            found = set()
            for name in self._index:
                parts = name.split("/")[:-1]
                for index in range(1, len(parts) + 1):
                    found.add("/".join(parts[:index]))
            self._dirs = found
        return sorted(self._dirs)

    def isDir(self, name: str) -> bool:
        """``name`` 是不是包里的目录（条目名的前缀）。"""
        name = normalizeEntry(name)
        if not name:
            return True
        if name in self._index:
            return False
        if self._dirs is None:
            self.directories()
        return name in self._dirs

    def children(self, directory: str) -> list:
        """某个目录下的直接子项（文件与子目录都算，名字不含目录前缀）。"""
        directory = normalizeEntry(directory)
        prefix = directory + "/" if directory else ""
        found = []
        for name in self._index:
            if not name.startswith(prefix):
                continue
            head = name[len(prefix):].split("/")[0]
            if head and head not in found:
                found.append(head)
        return sorted(found)

    # === 读取 ===

    def read(self, name: str):
        """读一个条目；不存在返回 ``None``。"""
        entry = self.entryName(name)
        if entry is None:
            return None
        return self._archive.read(entry)

    def close(self) -> None:
        if self._archive is not None:
            self._archive.close()
            self._archive = None

    def __str__(self) -> str:
        return "ResourcePack<%s, %d files>" % (self.path, len(self._index))

