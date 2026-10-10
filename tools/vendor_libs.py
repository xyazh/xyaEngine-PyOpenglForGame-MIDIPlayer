"""把引擎用到的第三方包**固化**进 ``pyglgame/libs/``（保证环境稳定）。

为什么固化（而不是靠全局 site-packages）
----------------------------------------

* 换台机器 / 换个解释器 / 别人 ``pip install`` 过别的版本，游戏就可能换个行为；
  固化之后"跑哪个版本"由仓库说了算；
* Nuitka 打包时 ``PYTHONPATH=pyglgame/libs`` 就能拿到这一份，构建与运行**同源**；
* 引擎其实早就在这么做（``Box2D`` / ``pybullet`` 在 ``pyglgame/libs`` 下），
  这里只是把其余依赖按同一约定补齐。

固化什么（见 :data:`PACKAGES`）
-------------------------------

``numpy`` / ``cv2``(opencv-python) / ``PIL``(pillow) / ``pyglm`` / ``mido`` /
``line_profiler`` / ``keyboard`` / ``OpenGL``(PyOpenGL) / ``OpenGL_accelerate``。

顺带一起搬：``<包>.libs/``（numpy 的 BLAS 之类）、对应的 ``*.dist-info``
（``importlib.metadata`` 还能查到版本）。

用法（仓库根执行）::

    python tools/vendor_libs.py            # 从当前解释器复制 / 刷新
    python tools/vendor_libs.py --verify   # 与 VENDOR.json 核对（CI 用，缺文件就报错）
    python tools/vendor_libs.py --list     # 看固化清单

⚠ 固化的二进制是**给游戏解释器用的**（本机是 CPython 3.10 + win_amd64），
所有文件与 ABI 一起记进 ``pyglgame/libs/VENDOR.json``；运行时
:mod:`pyglgame.tools.vendor` 只会在 ABI **完全一致**时把它接进 ``sys.path``，
所以拿 3.14 跑测试时用的仍是 site-packages 里那套（不会拿 cp310 的扩展去装 3.14）。
"""

import argparse
import json
import os
import shutil
import sys
import sysconfig
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_LIBS = os.path.join(_ROOT, "pyglgame", "libs")
_MANIFEST = os.path.join(_LIBS, "VENDOR.json")

#: ``(模块名, 发行版名前缀)``；发行版名用来找 ``*.dist-info`` 与 ``<名字>.libs``
PACKAGES = (
    ("numpy", "numpy"),
    ("OpenGL", "PyOpenGL"),
    ("OpenGL_accelerate", "PyOpenGL_accelerate"),
    ("PIL", "pillow"),
    ("cv2", "opencv_python"),
    ("pyglm", "pyglm"),
    ("mido", "mido"),
    ("line_profiler", "line_profiler"),
    ("keyboard", "keyboard"),
)

#: 复制时永远跳过的目录（``__pycache__`` 与解释器版本相关，重装只会碍事）
SKIP_DIRS = ("__pycache__", ".git")

#: 已经以同样方式随包分发的物理后端（只校验、不复制）
ALREADY_VENDORED = ("Box2D", "pybullet.cp310-win_amd64.pyd", "pybullet_utils")


def sitePackages() -> str:
    """当前解释器的 site-packages（固化来源）。"""
    for key in ("purelib", "platlib"):
        path = sysconfig.get_paths().get(key)
        if path and os.path.isdir(path):
            return path
    raise RuntimeError("找不到当前解释器的 site-packages")


def abiTag() -> str:
    """当前解释器的扩展模块后缀，例如 ``.cp310-win_amd64.pyd``。"""
    return sysconfig.get_config_var("EXT_SUFFIX") or ""


def findDistInfo(site: str, distribution: str):
    """按发行版名前缀找 ``*.dist-info``（返回目录名列表）。"""
    wanted = distribution.lower().replace("-", "_")
    found = []
    for entry in sorted(os.listdir(site)):
        lower = entry.lower()
        if not (lower.endswith(".dist-info") or lower.endswith(".egg-info")):
            continue
        stem = lower.rsplit(".dist-info", 1)[0].rsplit(".egg-info", 1)[0]
        if stem == wanted or stem.startswith(wanted + "-"):
            found.append(entry)
    return found


def versionOf(distInfo: str) -> str:
    """``numpy-2.2.6.dist-info`` → ``2.2.6``。"""
    stem = distInfo.rsplit(".dist-info", 1)[0].rsplit(".egg-info", 1)[0]
    return stem.rsplit("-", 1)[-1]


def copyTree(source: str, target: str) -> tuple:
    """复制目录（跳过 ``__pycache__``），返回 ``(文件数, 字节数)``。"""
    files = 0
    total = 0
    for root, dirs, names in os.walk(source):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(root, source)
        destination = target if rel == "." else os.path.join(target, rel)
        os.makedirs(destination, exist_ok=True)
        for name in names:
            if name.endswith((".pyc", ".pyo")):
                continue
            src = os.path.join(root, name)
            dst = os.path.join(destination, name)
            shutil.copy2(src, dst)
            files += 1
            total += os.path.getsize(src)
    return files, total


def doVendor(args) -> int:
    site = sitePackages()
    print("[vendor] 来源   : %s" % site)
    print("[vendor] 目标   : %s" % os.path.relpath(_LIBS, _ROOT))
    print("[vendor] ABI    : %s（Python %s）" % (abiTag(), sys.version.split()[0]))
    os.makedirs(_LIBS, exist_ok=True)

    entries = []
    for module, distribution in PACKAGES:
        source = os.path.join(site, module)
        if not os.path.isdir(source):
            print("[vendor] 跳过   : %s（当前解释器里没有）" % module)
            continue
        files, total = copyTree(source, os.path.join(_LIBS, module))
        copied = [module]
        libs = os.path.join(site, module + ".libs")     # numpy 的 BLAS 之类
        if os.path.isdir(libs):
            more = copyTree(libs, os.path.join(_LIBS, module + ".libs"))
            files += more[0]
            total += more[1]
            copied.append(module + ".libs")
        version = ""
        for dist_info in findDistInfo(site, distribution):
            more = copyTree(os.path.join(site, dist_info),
                            os.path.join(_LIBS, dist_info))
            files += more[0]
            total += more[1]
            copied.append(dist_info)
            version = versionOf(dist_info)
        entries.append({"module": module, "distribution": distribution,
                        "version": version, "dirs": copied,
                        "files": files, "bytes": total})
        print("[vendor] %-20s %-10s %5d 文件 %8.1f MB"
              % (module, version or "-", files, total / 1048576.0))

    manifest = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "abi": abiTag(),
        "source": os.path.basename(site),
        "note": "运行时只在 ABI 一致时接进 sys.path（见 pyglgame/tools/vendor.py）",
        "already_vendored": list(ALREADY_VENDORED),
        "packages": entries,
    }
    with open(_MANIFEST, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False, sort_keys=True)
    print("[vendor] 合计   : %d 个包 / %d 文件 / %.1f MB"
          % (len(entries), sum(e["files"] for e in entries),
             sum(e["bytes"] for e in entries) / 1048576.0))
    print("[vendor] 清单   : %s" % os.path.relpath(_MANIFEST, _ROOT))
    return 0


def loadManifest():
    if not os.path.isfile(_MANIFEST):
        return None
    with open(_MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)


def doList() -> int:
    manifest = loadManifest()
    if manifest is None:
        print("[vendor] 还没有固化过：先跑 python tools/vendor_libs.py")
        return 1
    print("[vendor] 生成于 %s（Python %s，ABI %s）"
          % (manifest["generated"], manifest["python"], manifest["abi"]))
    for entry in manifest["packages"]:
        print("         %-20s %-10s %5d 文件 %8.1f MB"
              % (entry["module"], entry["version"], entry["files"],
                 entry["bytes"] / 1048576.0))
    return 0


def doVerify() -> int:
    manifest = loadManifest()
    if manifest is None:
        print("[vendor] 没有 %s，先跑 python tools/vendor_libs.py"
              % os.path.relpath(_MANIFEST, _ROOT))
        return 1
    problems = []
    if manifest.get("abi") != abiTag():
        print("[vendor] 注意：清单是 %s 固化的，当前解释器 ABI 是 %s —— "
              "运行时不会启用固化依赖" % (manifest.get("abi"), abiTag()))
    for entry in manifest["packages"]:
        for name in entry["dirs"]:
            if not os.path.exists(os.path.join(_LIBS, name)):
                problems.append("缺目录: %s" % name)
    for name in ALREADY_VENDORED:
        if not os.path.exists(os.path.join(_LIBS, name)):
            problems.append("缺随包第三方: %s" % name)
    if problems:
        for line in problems:
            print("[vendor] %s" % line)
        return 1
    print("[vendor] 固化内容完整（%d 个包）" % len(manifest["packages"]))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把第三方依赖固化进 pyglgame/libs/")
    parser.add_argument("--verify", action="store_true", help="核对固化结果后退出")
    parser.add_argument("--list", action="store_true", help="列出固化清单后退出")
    args = parser.parse_args(argv)
    if args.list:
        return doList()
    if args.verify:
        return doVerify()
    return doVendor(args)


if __name__ == "__main__":
    raise SystemExit(main())
