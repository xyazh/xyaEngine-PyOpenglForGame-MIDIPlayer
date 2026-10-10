"""从 Nuitka 打出来的 ``*.dist`` 里删掉**这个平台永远不会加载**的文件。

为什么需要这一步（都不是"猜"的，下面每条都能自己验一遍）
--------------------------------------------------------

Nuitka 是"宁可多带"的策略：它会把包目录里能找到的 DLL 全部拷进 dist。

* ``nuitka --list-package-dlls=OpenGL`` → **12 个** freeglut/gle 变体
  （32/64 位 × vc9/vc10/vc14）。而 PyOpenGL 的
  ``OpenGL/platform/win32.py`` 是按运行时条件挑一个的::

      if sys.hexversion >= 0x3050000: vc = 'vc14'      # CPython >= 3.5
      size = platform.architecture()[0].strip('bits')  # '64'

  所以 cp310-win_amd64 只会加载 ``freeglut64.vc14.dll`` / ``gle64.vc14.dll``，
  另外 10 个（约 1.4 MB）是死重量。
* ``nuitka --list-package-dlls=pyglgame.openal`` → ``lib/OpenAL32.dll``、
  ``lib/OpenAL64.dll``、``lib/libopenal32.so`` 一起拷。而
  :meth:`pyglgame.openal.Device._default_library_name` 在 Windows 上按位数
  二选一（64 位解释器 → ``OpenAL64.dll``），``.so`` 只在 Linux 用得着
  → 64 位构建少 2.9 MB。
* Tk 家族（``tcl86t.dll`` 1.8 MB、``tk86t.dll`` 1.5 MB、``_tkinter.pyd``、
  ``tcl8.*/`` 目录）：打包脚本已经用 ``--nofollow-import-to=tkinter`` 排除，
  这里是"兜底"——真漏进来了就删掉并说一声（引擎与 MIDIPlayer 都不 import
  tkinter/PIL.ImageTk/OpenGL.Tk）。
* ``*.pdb`` / ``__pycache__``：调试符号与字节码缓存，不该进发行包。
* 只剩"测试/Tk 才用"的扩展：``numpy/_core/_multiarray_tests.pyd``（只有
  numpy.testing 用，测试已经被排除）、``PIL/_imagingtk.pyd``（只有
  ``PIL.ImageTk`` 用，而 tkinter 已经被排除）。
* ``--drop-unused``（默认关，打包脚本用 ``XYA_DROP_UNUSED=1`` 打开）还会删
  ``unicodedata.pyd``（1.07 MB，只有 ``str.encode("idna")`` 那条链用）和
  ``_queue.pyd``（28 KB，``queue``/multiprocessing 用）。这两个是"能力"而不是
  "平台"，删了对应功能就没了，所以默认保留 —— 引擎自带 HTTP 客户端要编码主机名
  就得靠 ``unicodedata``。注意 ``unicodedata`` 是 **Nuitka 关不掉的**（它属于
  "non-excluded parts of standard library"，``--nofollow-import-to`` /
  ``--noinclude-custom-mode`` 都试过，2.7.6 无效），所以只能在这一层删。

用法（在仓库根执行）::

    python tools/prune_dist.py --list dist/MIDIPlayer.dist   # 只看会删哪些
    python tools/prune_dist.py dist/MIDIPlayer.dist          # 真删
    python tools/prune_dist.py dist/MIDIPlayer.dist --arch 32
    python tools/prune_dist.py dist/MIDIPlayer.dist --check  # 顺手核对"该留的还在"
    python tools/prune_dist.py dist/MIDIPlayer.dist --drop-unused

``--arch`` 默认取**当前解释器**的位数（打包脚本用的就是这个解释器）；
``--check`` 会在结束时核对"这个位数真正需要的那几个 DLL"是否还在，
缺了就返回码 1（构建脚本据此报警）。
"""

import argparse
import glob
import os
import struct

#: 两个位数都会删的（Tk 家族 + 调试符号 + 缓存 + 只剩测试/Tk 才用的扩展）
COMMON_PATTERNS = (
    "_tkinter.pyd",
    "tcl*.dll",
    "tk*.dll",
    "tcl8*/**/*",
    "tk8*/**/*",
    "**/*.pdb",
    "**/__pycache__/**/*",
    # numpy 自己的测试辅助扩展（只有 numpy.testing / numpy/**/tests 用，
    # 打包脚本已经用 --nofollow-import-to=*.tests 把测试排除掉了）
    "numpy/_core/_multiarray_tests.*",
    # PIL 的 Tk 插件（只有 PIL.ImageTk 用；tkinter 已经被排除，留着也跑不起来）
    "PIL/_imagingtk.*",
)

#: 只有 ``--drop-unused``（XYA_DROP_UNUSED=1）才会删的"这个应用不用的标准库模块"。
#: 它们跟平台无关，删掉之后对应能力就没了，所以默认保留：
#:   unicodedata.pyd  ``str.encode("idna")`` 会经过 encodings.idna → stringprep →
#:                    unicodedata；引擎自带 HTTP 客户端编码主机名时走这条路。
#:                    MIDIPlayer 不做网络请求，但引擎有这能力，删了就没了。
#:   _queue.pyd       ``queue``/multiprocessing 用；MIDIPlayer 用不到
#:                    （line_profiler 那条 multiprocessing 路径已经不在包里）。
UNUSED_PATTERNS = (
    "unicodedata.*",
    "_queue.*",
)

#: 只在 64 位构建里删的（32 位变体 + Linux 库）
PATTERNS_64 = (
    "OpenGL/DLLS/freeglut32.*.dll",
    "OpenGL/DLLS/gle32.*.dll",
    "OpenGL/DLLS/*.vc9.dll",
    "OpenGL/DLLS/*.vc10.dll",
    "pyglgame/openal/lib/OpenAL32.dll",
    "pyglgame/openal/lib/libopenal32.so",
)

#: 只在 32 位构建里删的（64 位变体 + Linux 库）
PATTERNS_32 = (
    "OpenGL/DLLS/freeglut64.*.dll",
    "OpenGL/DLLS/gle64.*.dll",
    "OpenGL/DLLS/*.vc9.dll",
    "OpenGL/DLLS/*.vc10.dll",
    "pyglgame/openal/lib/OpenAL64.dll",
    "pyglgame/openal/lib/libopenal32.so",
)

#: ``--check`` 要核对"该留的还在"的清单（按位数）
KEEP_64 = ("python310.dll", "OpenGL/DLLS/freeglut64.vc14.dll",
           "OpenGL/DLLS/gle64.vc14.dll", "pyglgame/openal/lib/OpenAL64.dll")
KEEP_32 = ("python310.dll", "OpenGL/DLLS/freeglut32.vc14.dll",
           "OpenGL/DLLS/gle32.vc14.dll", "pyglgame/openal/lib/OpenAL32.dll")


def interpreterBits() -> int:
    """当前解释器位数（64 / 32）——``--arch`` 的默认值。"""
    return struct.calcsize("P") * 8


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%.1f GB" % size


def collect(dist: str, arch: int, drop_unused: bool = False):
    """按规则收集要删的文件（已去重、已排序）。"""
    patterns = list(COMMON_PATTERNS)
    patterns += list(PATTERNS_64 if arch == 64 else PATTERNS_32)
    if drop_unused:
        patterns += list(UNUSED_PATTERNS)
    found = set()
    for pattern in patterns:
        for path in glob.glob(os.path.join(dist, pattern), recursive=True):
            if os.path.isfile(path):
                found.add(path)
    return sorted(found)


def removeEmptyDirs(dist: str) -> int:
    """删掉 prune 之后空掉的目录（``tcl8.*/`` 这类）。"""
    removed = 0
    for root, dirs, files in os.walk(dist, topdown=False):
        if root == dist or dirs or files:
            continue
        try:
            os.rmdir(root)
            removed += 1
        except OSError:
            pass
    return removed


def doList(dist: str, paths) -> int:
    total = 0
    for path in paths:
        size = os.path.getsize(path)
        total += size
        print("        %10.1f KB  %s"
              % (size / 1024.0, os.path.relpath(path, dist).replace(os.sep, "/")))
    print("[prune] 可删   : %d 个文件，%s" % (len(paths), human(total)))
    return 0


def doPrune(dist: str, paths) -> int:
    total = 0
    for path in paths:
        size = os.path.getsize(path)
        total += size
        rel = os.path.relpath(path, dist).replace(os.sep, "/")
        try:
            os.remove(path)
        except OSError as error:
            print("[prune] 删不掉: %s（%s）" % (rel, error))
            continue
        print("[prune] - %8.1f KB  %s" % (size / 1024.0, rel))
    dirs = removeEmptyDirs(dist)
    print("[prune] 删除   : %d 个文件%s，%s"
          % (len(paths), ("、%d 个空目录" % dirs) if dirs else "", human(total)))
    return 0


def doCheck(dist: str, arch: int) -> int:
    keep = KEEP_64 if arch == 64 else KEEP_32
    missing = [name for name in keep
               if not os.path.exists(os.path.join(dist, name.replace("/", os.sep)))]
    if missing:
        print("[prune] 少了 %d 个必需文件（构建不完整）：%s"
              % (len(missing), ", ".join(missing)))
        return 1
    print("[prune] 必需   : %d 个平台必需文件都在" % len(keep))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="删掉 Nuitka dist 里本平台永远不加载的文件（Tk/32 位/vc9/vc10/Linux 库）")
    parser.add_argument("dist", help="Nuitka 的 *.dist 目录")
    parser.add_argument("--arch", default="auto", choices=("auto", "32", "64"),
                        help="目标位数（默认 auto：当前解释器）")
    parser.add_argument("--list", action="store_true", help="只列出要删的文件后退出")
    parser.add_argument("--drop-unused", action="store_true",
                        help="另外删掉本应用用不到的标准库模块（unicodedata.pyd 会"
                             "让 idna/带主机名的 HTTP 请求失效，_queue.pyd 让"
                             " queue/multiprocessing 失效），默认不删")
    parser.add_argument("--check", action="store_true",
                        help="结束后核对平台必需文件是否还在（缺了返回码 1）")
    args = parser.parse_args(argv)

    dist = os.path.abspath(args.dist)
    if not os.path.isdir(dist):
        print("[prune] 不是目录: %s" % dist)
        return 1
    arch = interpreterBits() if args.arch == "auto" else int(args.arch)
    print("[prune] 目标   : %s（%d 位%s）"
          % (dist, arch, "，含 --drop-unused" if args.drop_unused else ""))

    paths = collect(dist, arch, drop_unused=args.drop_unused)
    if args.list:
        return doList(dist, paths)
    code = doPrune(dist, paths)
    if args.check:
        return doCheck(dist, arch) or code
    return code


if __name__ == "__main__":
    raise SystemExit(main())
