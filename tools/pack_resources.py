"""把 ``res/`` 目录打成一个资源包（``*.xypack``），或校验一个已有的包。

压缩策略（``--compress``）::

    store     不压（引擎默认；png/ogg/mp4 本来就压过，再压白费时间）
    deflate   全部 deflate
    auto      只 deflate 文本类后缀（见引擎的 ``TEXT_SUFFIXES``），其余 store
    max       文本类 **加字体** 用 LZMA，其余 store ← 打包脚本用的就是它

``max`` 是这里唯一"引擎不支持、由本工具补上"的策略，做法是先用 ``store``
打一遍（条目名/顺序/清单仍然全部由引擎 :func:`packDirectory` 决定），再原地
把命中后缀的条目换一种压缩方式（:func:`recompressPack`）。这样引擎一行都不用改：
包还是那个 zip，读还是 :mod:`zipfile`。

用法（在仓库根执行；所有默认路径都由 ``__file__`` 推出来，**不写死绝对路径**）::

    python tools/pack_resources.py                      # res/ → res.xypack（全量）
    python tools/pack_resources.py --compress auto      # 文本类再 deflate 一下
    python tools/pack_resources.py --compress max       # 文本+字体 LZMA（最省，打包用）
    python tools/pack_resources.py --exclude "audio/**" --exclude "movies/**"
    python tools/pack_resources.py --out dist/res.xypack
    python tools/pack_resources.py --list res.xypack    # 看包里有什么
    python tools/pack_resources.py --verify res.xypack  # 与 res/ 目录逐条比对（CI/回归）

包格式与"为什么用 zip"见 :mod:`pyglgame.resources.ResourcePack`；
引擎侧怎么挂载见 :mod:`pyglgame.resources`（``res/`` 目录与 ``res.xypack``
二选一，两个都在时优先目录）。
"""

import argparse
import os
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pyglgame.resources import (PACK_SUFFIX, TEXT_SUFFIXES, ResourcePack,  # noqa: E402
                                entryNames, listPack, packDirectory)

#: 字体后缀：包里的"大件"就是它（``res/fonts/ygblack.ttf`` 11.4 MB）。
#: 引擎的 ``TEXT_SUFFIXES`` 不含字体，所以 ``--compress auto`` 会把字体原样存进包。
FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".woff", ".woff2")

#: ``--compress max`` 会用 LZMA 再压一遍的后缀（文本 + 字体）
MAX_SUFFIXES = tuple(TEXT_SUFFIXES) + FONT_SUFFIXES


def defaultSource() -> str:
    return os.path.join(_ROOT, "res")


def defaultTarget() -> str:
    return os.path.join(_ROOT, "res" + PACK_SUFFIX)


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%.1f GB" % size


def recompressPack(path: str, suffixes=MAX_SUFFIXES,
                   compress_type: int = zipfile.ZIP_LZMA) -> dict:
    """原地把包里命中 ``suffixes`` 的条目换成 ``compress_type``，其余保持不动。

    只换"压缩方式"这一个字段：条目名、顺序、时间戳、权限位、创建者、包清单
    （zip comment）全部逐字保留，所以包对引擎来说还是同一个包
    （:class:`~pyglgame.resources.ResourcePack` 就是 ``zipfile`` 读，
    解压方式由 zip 自己记着，上层要的只是一串字节）。

    为什么要"先 store 再重压"而不是让引擎直接写 LZMA：
    引擎 :data:`pyglgame.resources.ResourcePack.TEXT_SUFFIXES` 是引擎的约定，
    打包脚本不去改它（改了就等于动引擎），于是把"怎么压"这一步留在本工具里。

    返回统计：``{"entries", "recompressed", "bytes_before", "bytes_after"}``。
    """
    path = os.path.abspath(path)
    temp = path + ".recompressing"
    wanted = tuple(suffixes)
    entries = 0
    recompressed = 0
    try:
        with zipfile.ZipFile(path, "r") as source:
            comment = source.comment
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED) as target:
                for info in source.infolist():
                    data = b"" if info.is_dir() else source.read(info.filename)
                    entries += 1
                    compress = (compress_type if info.filename.lower().endswith(wanted)
                                else zipfile.ZIP_STORED)
                    out = zipfile.ZipInfo(info.filename, info.date_time)
                    out.external_attr = info.external_attr
                    out.internal_attr = info.internal_attr
                    out.create_system = info.create_system
                    out.compress_type = compress
                    target.writestr(out, data)
                    if compress != info.compress_type:
                        recompressed += 1
                target.comment = comment
        before = os.path.getsize(path)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.remove(temp)
        raise
    after = os.path.getsize(path)
    return {"entries": entries, "recompressed": recompressed,
            "bytes_before": before, "bytes_after": after}


def doPack(args) -> int:
    stats = packDirectory(args.src, args.out, include=args.include,
                          exclude=args.exclude,
                          # max：先 store 打好（条目/清单由引擎决定），再重压
                          compress="store" if args.compress == "max" else args.compress,
                          extra={"compress": "max"} if args.compress == "max" else None)
    again = None
    if args.compress == "max":
        again = recompressPack(args.out)
        stats["pack_bytes"] = again["bytes_after"]
        stats["ratio"] = (again["bytes_after"] / stats["bytes"]) if stats["bytes"] else 0.0
    print("[pack] 源目录 : %s" % os.path.relpath(stats["source"], _ROOT)
          if stats["source"].startswith(_ROOT) else "[pack] 源目录 : %s" % stats["source"])
    print("[pack] 输出   : %s" % stats["target"])
    print("[pack] 条目   : %d 个，原始 %s → 包 %s（%.1f%%）"
          % (stats["files"], human(stats["bytes"]), human(stats["pack_bytes"]),
             stats["ratio"] * 100.0))
    if again is not None:
        print("[pack] max    : %d/%d 个条目改 LZMA，%s → %s（省 %s）"
              % (again["recompressed"], again["entries"],
                 human(again["bytes_before"]), human(again["bytes_after"]),
                 human(again["bytes_before"] - again["bytes_after"])))
    if args.exclude:
        print("[pack] 排除   : %s" % ", ".join(args.exclude))
    if args.include:
        print("[pack] 只收   : %s" % ", ".join(args.include))
    return 0


def doList(path: str) -> int:
    pack = ResourcePack(path)
    manifest = pack.manifest or {}
    print("[list] 包     : %s" % path)
    if manifest:
        print("[list] 清单   : format=%s version=%s compress=%s files=%s created=%s"
              % (manifest.get("format"), manifest.get("version"),
                 manifest.get("compress"), manifest.get("files"),
                 manifest.get("created")))
    for name, size in listPack(path):
        print("        %10d  %s" % (size, name))
    print("[list] 合计   : %d 个条目" % len(pack.names()))
    return 0


def doVerify(args) -> int:
    """把包和源目录逐条比对：缺的 / 多的 / 大小不一样的都报出来。

    ``--include/--exclude`` 与打包时一致，所以"故意没打进包的东西"不会误报。
    """
    pack = ResourcePack(args.out)
    source = os.path.abspath(args.src)
    onDisk = {}
    if os.path.isdir(source):
        for name in entryNames(source, include=args.include, exclude=args.exclude):
            onDisk[name] = os.path.getsize(
                os.path.join(source, name.replace("/", os.sep)))

    inPack = {name: size for name, size in listPack(args.out)}
    onlyOnDisk = sorted(set(onDisk) - set(inPack))
    onlyInPack = sorted(set(inPack) - set(onDisk))
    different = sorted(name for name in set(onDisk) & set(inPack)
                       if onDisk[name] != inPack[name])

    print("[verify] 源目录 : %s（%d 个文件）" % (source, len(onDisk)))
    print("[verify] 包     : %s（%d 个条目）" % (args.out, len(inPack)))
    for label, names in (("只有磁盘有（包漏了）", onlyOnDisk),
                         ("只有包里有（磁盘删了？）", onlyInPack),
                         ("大小不一致", different)):
        if names:
            print("[verify] %s: %s" % (label, ", ".join(names[:10])
                                       + (" …" if len(names) > 10 else "")))
    if onlyOnDisk or onlyInPack or different:
        print("[verify] 不一致：跑一次 `python tools/pack_resources.py` 重新打包")
        return 1
    print("[verify] 一致（条目数与大小都相同）")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="res/ → 资源包（*.xypack）；目录形态与包形态对引擎是等价的")
    parser.add_argument("--src", default=None, help="源目录（默认：<项目根>/res）")
    parser.add_argument("--out", default=None, help="输出/输入包（默认：<项目根>/res%s）"
                        % PACK_SUFFIX)
    parser.add_argument("--include", action="append", default=[],
                        help="只收这些条目（可重复；支持 *.png / dir/** 这类写法）")
    parser.add_argument("--exclude", action="append", default=[],
                        help="排除这些条目（可重复；常用来踢掉 audio/**、movies/**）")
    parser.add_argument("--compress", default="store",
                        choices=("store", "deflate", "auto", "max"),
                        help="压缩策略：store（默认）/ deflate / auto"
                             "（只压文本）/ max（文本+字体用 LZMA，最省；"
                             "其余 png 之类保持 store）")
    parser.add_argument("--list", action="store_true", help="列出包里的条目后退出")
    parser.add_argument("--verify", action="store_true",
                        help="把包与源目录比对后退出（不一致时返回码 1）")
    args = parser.parse_args(argv)

    args.src = args.src or defaultSource()
    args.out = args.out or defaultTarget()

    if args.list:
        return doList(args.out)
    if args.verify:
        return doVerify(args)
    return doPack(args)


if __name__ == "__main__":
    raise SystemExit(main())
