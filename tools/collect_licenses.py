"""把随包分发的第三方组件许可**正文**汇总成 ``LICENSES/``（发行物要带的那一份）。

为什么要有这一步
----------------

``pyglgame/libs`` 里每个包都自带许可文件（``*.dist-info/...``、``Box2D/LICENSE.txt``…），
但它们散在几百个目录里，而且 **Nuitka 打包不会自动带走**（它只跟 ``import``；
见 ``docs/Nuitka打包.md`` §3）。结果就是"用了别人的东西、发行物里却没有声明"。

这个脚本把它们按**组件**收集成一份能直接跟包走的目录::

    LICENSES/
        INDEX.md                    ← 索引：组件 / 版本 / 许可 / 正文 / 上游
        numpy/LICENSE.txt           ← 从 libs 原样复制（不加工）
        opencv/…                    ← OpenCV 本体 + 打包器 + 第三方（含 FFmpeg）
        ffmpeg/FFmpeg-LICENSE.txt   ← 从 opencv 的第三方清单里**截取**那一段
        openal-soft/…               ← 随包 DLL 的声明（仓库里原本没有 → 脚本生成）
        LGPL-2.1.txt                ← FFmpeg / OpenAL Soft 共用
        …

三份配套文件（各司其职，别合并）::

    THIRD_PARTY_NOTICES.md            人读的汇总 + 义务清单（发行物要带的入口）
    docs/第三方许可.md                 本项目的合规做法（为什么这样拆、怎么维护）
    pyglgame/openal/LICENSE-openal-soft.txt   音频后端**就近**声明（由本脚本生成）

用法（仓库根执行）::

    python tools/collect_licenses.py            # 生成 / 刷新 LICENSES/
    python tools/collect_licenses.py --verify   # 产物与当前仓库是否一致（build.bat 会跑）
    python tools/collect_licenses.py --list     # 只列组件表

⚠ 两个"不要"：

* **不要手改 ``LICENSES/`` 里的文件** —— 刷新会覆盖；要改就改本脚本的
  :data:`GENERATED` / :data:`COMPONENTS`，或者改上游包里那份；
* **不要删 ``pyglgame/libs`` 里各包自带的许可文件** —— 那是它们的"署名"，
  见 ``pyglgame/libs/__init__.py``。

产物里刻意**不带时间戳**（输出可复现），所以 ``--verify`` 能逐字比对。
"""

import argparse
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_LIBS = os.path.join(_ROOT, "pyglgame", "libs")
_OUT = os.path.join(_ROOT, "LICENSES")
_MANIFEST = os.path.join(_LIBS, "VENDOR.json")
_NOTICES = os.path.join(_ROOT, "THIRD_PARTY_NOTICES.md")

#: 需要"从一个大清单里截取一段"的起始 / 结束标记（按 ``strip()`` 后比较）
_FFMPEG_START = "FFmpeg is redistributed within all opencv-python packages."
_FFMPEG_END = "Qt 5 is redistributed within non-headless opencv-python Linux and macOS packages."
_LGPL_START = "GNU LESSER GENERAL PUBLIC LICENSE"

#: zlib 许可正文（``pyglgame/libs/pybullet-LICENSE.txt`` 里**只有作者名单**，
#: 正文取自同源的 Box2D / pyglm 两份 zlib 文本；逐字照抄，不要"顺手改写"）
ZLIB_TEXT = """This software is provided 'as-is', without any express or implied
warranty.  In no event will the authors be held liable for any damages
arising from the use of this software.

Permission is granted to anyone to use this software for any purpose,
including commercial applications, and to alter it and redistribute it
freely, subject to the following restrictions:

1. The origin of this software must not be misrepresented; you must not
claim that you wrote the original software. If you use this software
in a product, an acknowledgment in the product documentation would be
appreciated but is not required.

2. Altered source versions must be plainly marked as such, and must not be
misrepresented as being the original software.

3. This notice may not be removed or altered from any source distribution.
"""

#: OpenAL Soft 声明（正文之外还要写清"哪几个文件、哪个版本、有没有改、源码在哪"）
OPENAL_SOFT_NOTICE = """OpenAL Soft —— 随本仓库分发的音频库（LGPL）
================================================================

本目录说明的是下面这几个二进制文件的来源与许可，它们不是本项目的代码：

    pyglgame/openal/lib/OpenAL64.dll     Windows 64 位（内嵌版本串 "1.1 ALSOFT 1.15.1"）
    pyglgame/openal/lib/OpenAL32.dll     Windows 32 位（内嵌版本串 "1.1 ALSOFT 1.15.1"）
    pyglgame/openal/lib/libopenal32.so   Linux
    pyglgame/openal/soft_oal_64.dll      Windows 64 位（内嵌版本串 "1.1 ALSOFT 1.18.2"）

* 上游：OpenAL Soft，https://github.com/kcat/openal-soft （官网 https://openal-soft.org/ ）
* 许可：**LGPL**（GNU 弱 copyleft）。上游仓库的 ``COPYING`` 是
  *GNU Library General Public License, version 2*；本目录同时附
  ``LICENSES/LGPL-2.1.txt``（LGPL v2.1 全文）供参考，逐字原文以官方
  ``COPYING`` 为准：https://github.com/kcat/openal-soft/blob/master/COPYING
* 修改情况：**未修改**，直接使用上游发布/发行版里的二进制；调用方式是
  ``ctypes`` 在运行期 ``LoadLibrary`` 动态加载（见 ``pyglgame/openal/Device.py``
  :class:`~pyglgame.openal.Device.Device`），因此用户可以**自行替换**这些 DLL
  （换成自己编译的同名文件即可生效）—— 这正是 LGPL 要求的"可重新链接"能力。
* 源码获取：见上面的仓库地址（tag/版本按内嵌版本串对应，1.15.1 / 1.18.2）。
* 你（分发者）要做的事：把本声明与 ``LICENSES/LGPL-2.1.txt`` 一起放进发行物，
  并保留"用户可替换该 DLL"的能力（别把它静态链进 exe、别改名到找不到）。

> 引擎里 ``pyglgame/openal/*.py``（``al.py`` / ``alc.py`` / ``library_loader.py`` …）
> 是 OpenAL 的 **Python 绑定**，与上面这些 DLL 不是一回事：绑定代码源自 PyOpenAL
> （作者 Zuzu_Typ，PyPI 标注 ``License :: Public Domain``），**不受限制**；
> 有约束的只有 DLL 本身。
"""

#: FreeType 的 FTL 有"广告条款"：二进制分发时必须在**产品文档**里致谢
FREETYPE_NOTICE = """FreeType —— 随 Pillow（``PIL/_imagingft.*.pyd``）静态链接的字体引擎
================================================================================

* 版本：2.13.3（见 ``LICENSES/pillow/LICENSE.txt`` 里的 ``===== freetype-2.13.3 =====`` 段）
* 许可：上游给**两种**许可让使用者**二选一**

  1. **FTL**（FreeType License）—— 类似 BSD，但带一条"广告条款"：
     **必须在产品文档里明确致谢 FreeType Project**；
  2. GPLv2（或更新）—— 只在你的项目本来就用 GPL 时选它。

  本项目选 **FTL**（见 ``THIRD_PARTY_NOTICES.md``），因此：

  - 发行物的文档 / 关于界面里要出现类似这样的一句话：
    "Portions of this software are copyright © The FreeType Project
     (https://www.freetype.org). All rights reserved."
  - **不要**选 GPLv2 那一支（会给整个产品带来 GPL 义务）。

* 正文：``LICENSES/pillow/LICENSE.txt``（Pillow 的许可文件里按组件分段列全了，
  含 FTL 与 GPLv2 两段原文）。上游：https://www.freetype.org/
* 引擎侧入口：Pillow 用它给 :class:`pyglgame.render.TextLoader` 排版（``ImageFont``）。
"""

#: 素材层（``res/``）里的字体：OFL 要求"许可随字体走"，所以单独写一份说明
FONTS_NOTICE = """res/fonts/ 里的字体（**会随 res.xypack 一起发给玩家**）
=======================================================

ygblack.ttf
-----------
* 名称：源古黑體 / YuanGuHei（版本 3.000 —— 族名/版本读自字体自身的 ``name`` 表）
* 版权：© 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.
* 许可：**SIL Open Font License 1.1**（字体 ``name`` 表里就写着这句，``fsType=0`` 不限制嵌入）
* 上游：Adobe Source Han Sans（思源黑体）<https://github.com/adobe-fonts/source-han-sans>
  —— 本目录的 ``LICENSE.txt`` 就是从那儿原样取的（逐字未改，4463 字节）
* 正文：``LICENSES/fonts/LICENSE.txt``（= 与 ttf 同目录的 ``res/fonts/ygblack.LICENSE.txt``）

OFL 1.1 的三条要点（发行时注意）
--------------------------------

1. **许可与版权声明要跟着字体走** —— 所以 ``res/fonts/ygblack.LICENSE.txt`` 与 ttf 放在
   同一目录（并一起被 ``tools/pack_resources.py`` 打进 ``res.xypack``）；
   ``LICENSES/fonts/LICENSE.txt`` 是给发行物根目录用的同一份。
2. **保留字（Reserved Font Name）**：``'Source'`` 受保护。本字体已改名（YuanGuHei，不含
   ``Source``）—— 符合 OFL 对"修改版"的要求；以后再改也**别**用 ``Source`` 起名。
3. **字体本身不得单独出售** —— 可以随游戏/软件一起卖，不能把字体文件当商品单卖。

以后加字体（约定）
------------------

* 字体文件与它的许可**放同一目录**，许可命名 ``<字体名>.LICENSE.txt``；
* 在 ``tools/collect_licenses.py`` 的 ``COMPONENTS`` 里加一条 —— ``--verify`` 与
  ``tests/test_notices.py`` 都会检查"每个 ttf/otf 旁边都有 .LICENSE.txt"；
* 系统字体（例如 ``C:/Windows/Fonts/msyh.ttc`` 微软雅黑）**不能**随发行物分发。
"""

#: 逐个组件抄什么（``copies`` = ``(仓库内源文件, LICENSES/<dir>/ 下的目标名)``）
#: ``modules`` 里的版本号从 ``pyglgame/libs/VENDOR.json`` 现取，升级依赖时索引会自动跟着变。
COMPONENTS = (
    {
        "dir": "numpy", "name": "numpy", "modules": ("numpy",),
        "license": "BSD-3-Clause", "home": "https://numpy.org/",
        "note": "轮子里还带 OpenBLAS(BSD) / libdivide(zlib) / dragon4(MIT)，正文后半段列了",
        "copies": (("pyglgame/libs/numpy-2.2.6.dist-info/LICENSE.txt", "LICENSE.txt"),),
    },
    {
        "dir": "pyopengl", "name": "PyOpenGL / PyOpenGL_accelerate",
        "modules": ("OpenGL", "OpenGL_accelerate"),
        "license": "BSD-3-Clause", "home": "http://pyopengl.sourceforge.net/",
        "note": "另有免责声明：非容错设计，不得用于危及人身/财产的场合",
        "copies": (("pyglgame/libs/PyOpenGL_accelerate-3.1.9.dist-info/license.txt",
                    "LICENSE.txt"),
                   ("pyglgame/libs/PyOpenGL-3.1.9.dist-info/METADATA", "METADATA.txt")),
    },
    {
        "dir": "pillow", "name": "Pillow（PIL）", "modules": ("PIL",),
        "license": "MIT-CMU（HPND 变体）", "home": "https://python-pillow.org/",
        "note": "一个文件里按组件分段：brotli / FreeType / HarfBuzz / lcms2 / libjpeg-turbo / "
                "libpng / libwebp / OpenJPEG / libtiff / xz / zlib-ng",
        "copies": (("pyglgame/libs/pillow-11.2.1.dist-info/licenses/LICENSE", "LICENSE.txt"),),
    },
    {
        "dir": "pyglm", "name": "PyGLM（含 GLM、pyglm-typing）", "modules": ("pyglm",),
        "license": "Zlib/libpng", "home": "https://github.com/Zuzu-Typ/PyGLM",
        "note": "GLM 与 pyglm-typing 是 MIT，见 COPYING",
        "copies": (("pyglgame/libs/pyglm-2.8.2.dist-info/licenses/LICENSE", "LICENSE.txt"),
                   ("pyglgame/libs/pyglm-2.8.2.dist-info/licenses/COPYING", "COPYING.txt")),
    },
    {
        "dir": "mido", "name": "mido", "modules": ("mido",),
        "license": "MIT", "home": "https://github.com/mido/mido",
        "note": "Game4 用它读 MIDI",
        "copies": (("pyglgame/libs/mido-1.3.3.dist-info/LICENSE", "LICENSE.txt"),),
    },
    {
        "dir": "keyboard", "name": "keyboard", "modules": ("keyboard",),
        "license": "MIT", "home": "https://github.com/boppreh/keyboard",
        "note": "BaseWindow 的可选按键钩子",
        "copies": (("pyglgame/libs/keyboard-0.13.5.dist-info/LICENSE.txt", "LICENSE.txt"),),
    },
    {
        "dir": "line_profiler", "name": "line_profiler", "modules": ("line_profiler",),
        "license": "BSD-3-Clause（另含 CPython/PSF 片段）",
        "home": "https://github.com/pyutils/line_profiler",
        "note": "Python-LICENSE.txt 是 PSF-2.0（供派生的 stdlib 代码用）",
        "copies": (("pyglgame/libs/line_profiler-5.0.2.dist-info/licenses/LICENSE.txt",
                    "LICENSE.txt"),
                   ("pyglgame/libs/line_profiler-5.0.2.dist-info/licenses/LICENSE_Python.txt",
                    "Python-LICENSE.txt")),
    },
    {
        "dir": "opencv", "name": "opencv-python（cv2）", "modules": ("cv2",),
        "license": "Apache-2.0（打包器那份是 MIT）", "home": "https://opencv.org/",
        "note": "THIRD-PARTY.txt 是官方汇总（Windows 轮子里真正非宽松的只有 FFmpeg）",
        "copies": (("pyglgame/libs/cv2/LICENSE.txt", "opencv-python-LICENSE.txt"),
                   ("pyglgame/libs/opencv_python-4.12.0.88.dist-info/LICENSE.txt",
                    "LICENSE.txt"),
                   ("pyglgame/libs/opencv_python-4.12.0.88.dist-info/LICENSE-3RD-PARTY.txt",
                    "THIRD-PARTY.txt")),
    },
    {
        "dir": "box2d", "name": "Box2D / pybox2d", "version": "2.3.10",
        "license": "Zlib", "home": "https://box2d.org/",
        "note": "2D 物理后端（_Box2D.cp310-win_amd64.pyd）",
        "copies": (("pyglgame/libs/Box2D/LICENSE.txt", "LICENSE.txt"),
                   ("pyglgame/libs/Box2D/METADATA.txt", "METADATA.txt")),
    },
    {
        "dir": "pybullet", "name": "pybullet（Bullet 3）", "version": "3.2.7",
        "license": "Zlib", "home": "https://github.com/bulletphysics/bullet3",
        "note": "AUTHORS.txt 是上游的 LICENSE.txt（只有作者名单）+ META；正文见 zlib.txt",
        "copies": (("pyglgame/libs/pybullet-LICENSE.txt", "AUTHORS.txt"),
                   ("pyglgame/libs/pybullet-METADATA.txt", "METADATA.txt")),
    },
    {
        "dir": "freeglut", "name": "freeglut（随 PyOpenGL 的 DLLS/ 分发）",
        "version": "vc9 / vc10 / vc14 构建", "license": "MIT/X11",
        "home": "https://freeglut.sourceforge.net/",
        "note": "OpenGL/DLLS/freeglut{32,64}.vc*.dll；只允许用其名做事实性署名",
        "copies": (("pyglgame/libs/OpenGL/DLLS/freeglut_COPYING.txt", "LICENSE.txt"),
                   ("pyglgame/libs/OpenGL/DLLS/freeglut_README.txt", "README.txt")),
    },
    {
        "dir": "gle", "name": "GLE（随 PyOpenGL 的 DLLS/ 分发）",
        "version": "vc9 / vc10 / vc14 构建",
        "license": "IBM 样例源码许可 **或** GPLv2（二选一）",
        "home": "https://sourceforge.net/projects/gle/",
        "note": "本分发选 **IBM** 那一支（免费使用/分发/修改），不要选 GPLv2",
        "copies": (("pyglgame/libs/OpenGL/DLLS/gle_COPYING", "COPYING.txt"),
                   ("pyglgame/libs/OpenGL/DLLS/gle_COPYING.src", "COPYING.src.txt"),
                   ("pyglgame/libs/OpenGL/DLLS/gle_AUTHORS", "AUTHORS.txt"),
                   ("pyglgame/libs/OpenGL/DLLS/GLE_WIN32_README.txt", "README.txt")),
    },
    {
        "dir": "python", "name": "CPython 运行时（python310.dll / tcl / tk 等）",
        "version": None, "license": "PSF-2.0（Tcl/Tk 是 BSD 风格）",
        "home": "https://www.python.org/",
        "note": "Nuitka 产物里自带；引擎内 xyazhServer/ihttp/cookiejar.py 也派生自 stdlib",
        "copies": (("pyglgame/libs/line_profiler-5.0.2.dist-info/licenses/LICENSE_Python.txt",
                    "LICENSE-Python.txt"),),
    },
    {
        "dir": "fonts", "name": "res/fonts/ygblack.ttf（源古黑體 / YuanGuHei）",
        "version": "3.000", "license": "SIL OFL 1.1",
        "home": "https://github.com/adobe-fonts/source-han-sans",
        "note": "Adobe Source Han（思源黑体）衍生；OFL 要求许可随字体走，"
                "保留字 'Source' 受保护、字体本身不得单卖",
        "copies": (("res/fonts/ygblack.LICENSE.txt", "LICENSE.txt"),),
    },
)

#: 需要从"一个大清单"里截取一段的（起点/终点都按 ``strip()`` 比较，终点行不含）
EXTRACTS = (
    {
        "dir": "ffmpeg", "name": "FFmpeg（随 opencv-python 的 cv2/ 一起分发）",
        "version": None, "license": "LGPL-2.1", "home": "https://ffmpeg.org/",
        "note": "只在 cv2/opencv_videoio_ffmpeg4120_64.dll 里（Game4 出片用）；"
                "动态加载、可替换。**别**换成带 x264/x265 的 GPL 自编版",
        "source": "pyglgame/libs/cv2/LICENSE-3RD-PARTY.txt",
        "start": _FFMPEG_START, "end": _FFMPEG_END,
        "target": "FFmpeg-LICENSE.txt",
    },
    {
        "dir": "", "name": "GNU LGPL v2.1（正文；FFmpeg / OpenAL Soft 共用）",
        "version": None, "license": "LGPL-2.1",
        "home": "https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html",
        "note": "从 opencv-python 的第三方清单里原样截取（同一份文本）",
        "source": "pyglgame/libs/cv2/LICENSE-3RD-PARTY.txt",
        "start": _LGPL_START, "end": _FFMPEG_END,
        "target": "LGPL-2.1.txt",
    },
)

#: 仓库里**没有**、由本脚本生成的正文；``extra`` 是额外要写出的仓库内相对路径
GENERATED = (
    {
        "dir": "pybullet", "name": "pybullet 的 zlib 正文", "version": None,
        "license": "Zlib", "home": "https://github.com/bulletphysics/bullet3",
        "note": "上游许可证正文（随包的 pybullet-LICENSE.txt 只有作者名单）",
        "target": "zlib.txt", "text": ZLIB_TEXT, "extra": (),
    },
    {
        "dir": "openal-soft", "name": "OpenAL Soft（音频后端 DLL）",
        "version": "1.15.1 / 1.18.2", "license": "LGPL",
        "home": "https://github.com/kcat/openal-soft",
        "note": "未修改、动态加载可替换；同时写到 pyglgame/openal/LICENSE-openal-soft.txt",
        "target": "LICENSE-openal-soft.txt", "text": OPENAL_SOFT_NOTICE,
        "extra": ("pyglgame/openal/LICENSE-openal-soft.txt",),
    },
    {
        "dir": "freetype", "name": "FreeType（Pillow 静态链接）", "version": "2.13.3",
        "license": "FTL（或 GPLv2，二选一 → 本项目选 FTL）",
        "home": "https://www.freetype.org/",
        "note": "FTL 要求在**产品文档**里致谢 FreeType Project",
        "target": "NOTICE.txt", "text": FREETYPE_NOTICE, "extra": (),
    },
    {
        "dir": "fonts", "name": "字体说明（res/fonts/ 的 OFL 义务）", "version": None,
        "license": "SIL OFL 1.1", "home": "https://openfontlicense.org/",
        "note": "为什么素材层的字体也要声明、以后加字体的约定",
        "target": "NOTICE.txt", "text": FONTS_NOTICE, "extra": (),
    },
)


def loadVersions() -> dict:
    """``{模块名: 版本}``（现取 ``pyglgame/libs/VENDOR.json``；没有清单就返回空表）。"""
    try:
        with open(_MANIFEST, encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {entry["module"]: entry["version"] for entry in info.get("packages", [])}


def readBytes(rel: str) -> bytes:
    """按**字节**读仓库内的文件（许可正文一律原样搬运，不做编码转换）。"""
    with open(os.path.join(_ROOT, rel.replace("/", os.sep)), "rb") as handle:
        return handle.read()


def extractBytes(rel: str, start: str, end: str) -> bytes:
    """从 ``rel`` 里截取 ``start`` 行到 ``end`` 行（含起、不含终）之间的内容。

    两个标记都按 ``strip()`` 后比较（清单里的缩进不一定稳定），找不到就抛
    ``RuntimeError`` —— 让 ``--verify``/打包能**响亮地**失败，而不是悄悄生成半份。
    """
    text = readBytes(rel).decode("utf-8", errors="replace")
    lines = text.splitlines()
    first = last = None
    for index, line in enumerate(lines):
        if first is None:
            if line.strip() == start.strip():
                first = index
        elif line.strip() == end.strip():
            last = index
            break
    if first is None:
        raise RuntimeError("在 %s 里找不到起始标记：%s" % (rel, start))
    if last is None:
        raise RuntimeError("在 %s 里找不到结束标记：%s" % (rel, end))
    chunk = lines[first:last]
    # 收尾：去掉尾部的空行与"分节线"（``-----`` / ``=====``，那属于下一节）
    def isNoise(line: str) -> bool:
        text = line.strip()
        return not text or set(text) <= set("-=_*")

    while chunk and isNoise(chunk[-1]):
        chunk.pop()
    return ("\n".join(chunk) + "\n").encode("utf-8")


def versionOf(component: dict, versions: dict) -> str:
    """组件的版本串：写死的优先，否则从 ``VENDOR.json`` 现取（多个模块用 ``/`` 连）。"""
    if component.get("version"):
        return component["version"]
    found = []
    for module in component.get("modules", ()):
        version = versions.get(module)
        if version and version not in found:
            found.append(version)
    return " / ".join(found) or "-"


def plan() -> dict:
    """算出 ``{仓库内相对路径: bytes}``：LICENSES/ 下全部正文 + INDEX.md 本身。

    纯函数（只读仓库、不写盘），所以 ``--verify`` 就是"拿这份和磁盘比"。
    """
    versions = loadVersions()
    files = {}
    rows = []

    def put(rel: str, data: bytes) -> None:
        files[rel.replace("\\", "/")] = data

    for component in COMPONENTS:
        targets = []
        for source, target in component["copies"]:
            name = "%s/%s" % (component["dir"], target)
            put("LICENSES/" + name, readBytes(source))
            targets.append(name)
        rows.append((component, targets))

    for extract in EXTRACTS:
        name = ("%s/%s" % (extract["dir"], extract["target"])).lstrip("/")
        put("LICENSES/" + name,
            extractBytes(extract["source"], extract["start"], extract["end"]))
        rows.append((extract, [name]))

    for generated in GENERATED:
        name = "%s/%s" % (generated["dir"], generated["target"])
        data = generated["text"].encode("utf-8")
        put("LICENSES/" + name, data)
        for extra in generated.get("extra", ()):
            put(extra, data)
        rows.append((generated, [name]))

    put("LICENSES/INDEX.md", renderIndex(rows, versions))
    return files


def renderIndex(rows: list, versions: dict) -> bytes:
    """``LICENSES/INDEX.md``：组件 → 版本 / 许可 / 正文 / 上游。

    刻意不写时间戳（输出可复现，``--verify`` 才能逐字比对）。
    """
    lines = [
        "# LICENSES —— 随包分发的第三方许可正文",
        "",
        "本目录由 `tools/collect_licenses.py` **生成**（刷新手改会被覆盖）。",
        "人读的汇总与义务清单在仓库根的 `THIRD_PARTY_NOTICES.md`，",
        "本项目的做法与维护方式见 `docs/第三方许可.md`。",
        "",
        "| 组件 | 版本 | 许可 | 正文（本目录下） | 上游 / 备注 |",
        "|---|---|---|---|---|",
    ]
    for component, targets in sorted(rows, key=lambda item: item[0]["dir"] or "~"):
        files = "、".join("`%s`" % name for name in targets)
        note = component.get("note", "")
        home = component.get("home", "")
        tail = ("%s —— %s" % (home, note)).strip(" ——") if note else home
        lines.append("| %s | %s | %s | %s | %s |"
                     % (component["name"], versionOf(component, versions),
                        component["license"], files, tail))
    lines += [
        "",
        "## 怎么用",
        "",
        "* **发行**：整份目录跟 exe 一起发（`build.bat` 用 `--include-data-dir=./LICENSES=LICENSES`）；",
        "  人读的入口是 `THIRD_PARTY_NOTICES.md`；",
        "* **游戏内「关于/版权」**：`pyglgame.tools.notices`（`noticesText()` / `components()` /",
        "  `attributionLine()`），见 `docs/第三方许可.md`；",
        "* **升级依赖**：`pyglgame/libs` 里的版本变了就重跑 `python tools/collect_licenses.py`",
        "  （版本号取自 `pyglgame/libs/VENDOR.json`，会自己跟上）；",
        "* **检查**：`python tools/collect_licenses.py --verify`（`build.bat` 每次都会跑）。",
        "",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def summarize(files: dict) -> str:
    """一句话说明产物规模（组件数 / 文件数 / 体积），给日志用。"""
    total = sum(len(data) for data in files.values())
    components = len(COMPONENTS) + len(EXTRACTS) + len(GENERATED)
    return "%d 个组件 / %d 个文件 / %.1f KB" % (components, len(files), total / 1024.0)


def doWrite() -> int:
    """生成 / 刷新 ``LICENSES/``（+ ``pyglgame/openal/LICENSE-openal-soft.txt``）。"""
    files = plan()
    for rel, data in sorted(files.items()):
        path = os.path.join(_ROOT, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        print("[licenses] 写出 %-46s %6.1f KB" % (rel, len(data) / 1024.0))
    if not os.path.isfile(_NOTICES):
        print("[licenses] 注意：还没有 %s（发行物的人读入口，别漏）"
              % os.path.relpath(_NOTICES, _ROOT))
    stale = staleFiles(files)
    for rel in stale:
        print("[licenses] 注意：%s 不在计划里（旧产物？可以手动删）" % rel)
    print("[licenses] 完成   : %s" % summarize(files))
    return 0


def staleFiles(files: dict) -> list:
    """``LICENSES/`` 下已存在、但这次不该生成的文件（升级依赖后可能留下的旧版本）。"""
    known = {rel.replace("\\", "/") for rel in files}
    found = []
    for root, _dirs, names in os.walk(_OUT):
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), _ROOT).replace("\\", "/")
            if rel not in known:
                found.append(rel)
    return sorted(found)


def doVerify() -> int:
    """产物与仓库当前状态是否一致（``build.bat`` 每次都会跑；缺/变都返回 1）。"""
    try:
        files = plan()
    except RuntimeError as error:
        print("[licenses] FAILED: %s" % error)
        return 1
    problems = []
    for rel, data in sorted(files.items()):
        path = os.path.join(_ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            problems.append("缺文件: %s" % rel)
            continue
        with open(path, "rb") as handle:
            if handle.read() != data:
                problems.append("内容不一致: %s" % rel)
    if not os.path.isfile(_NOTICES):
        problems.append("缺汇总: %s" % os.path.relpath(_NOTICES, _ROOT))
    if problems:
        for line in problems:
            print("[licenses] %s" % line)
        print("[licenses] 修复: python tools/collect_licenses.py")
        return 1
    print("[licenses] 许可正文完整（%s）" % summarize(files))
    return 0


def doList() -> int:
    """只列组件表（不打盘）。"""
    versions = loadVersions()
    for group, rows in (("复制", COMPONENTS), ("截取", EXTRACTS), ("生成", GENERATED)):
        for component in rows:
            print("[licenses] %s %-14s %-10s %s"
                  % (group, component["dir"] or "(根)", versionOf(component, versions),
                     component["license"]))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把随包分发的第三方许可正文汇总到 LICENSES/")
    parser.add_argument("--verify", action="store_true", help="核对产物后退出（不一致返回 1）")
    parser.add_argument("--list", action="store_true", help="只列组件表后退出")
    args = parser.parse_args(argv)
    if args.list:
        return doList()
    if args.verify:
        return doVerify()
    return doWrite()


if __name__ == "__main__":
    raise SystemExit(main())
