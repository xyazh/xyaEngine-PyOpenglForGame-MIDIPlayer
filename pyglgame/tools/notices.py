"""第三方声明 / 许可正文的运行时入口（给游戏内「关于 / 版权」界面用）。

数据从哪来
----------

========================================  ==========================================
``<app_root>/THIRD_PARTY_NOTICES.md``     人读的汇总（发行物与 exe 同级）
``<app_root>/LICENSES/``                  各组件许可正文（索引 ``INDEX.md``）
``pyglgame/libs/VENDOR.json``             固化依赖的**真实版本**（about 里显示的就是
                                          这一份，不是写死的字符串）
========================================  ==========================================

``app_root()`` 在源码运行下是项目根、打包运行下是 exe 所在目录（见
:mod:`pyglgame.tools.paths`），而 ``build.bat`` 会把 ``THIRD_PARTY_NOTICES.md`` 与
``LICENSES/`` 放到 exe 旁边 —— 所以两种形态下这套 API 都能拿到东西；万一发行物漏了
文件，:func:`noticesText` 会退回 :data:`BUILTIN_NOTICES`（内置摘要，永远可用）。

API
---

:func:`attributionLine`  一行版权摘要（关于界面第一行）
:func:`components`       组件表 ``[{name, version, license, home, license_file}]``
:func:`noticesText`      汇总全文（读不到就退回内置摘要）
:func:`printNotices`     打到 stdout（demo 的"关于"按键用；**GBK 控制台安全**）
:func:`noticesPath` / :func:`licensesDir` 两个路径

完整清单与义务见 ``THIRD_PARTY_NOTICES.md`` / ``docs/第三方许可.md``；
正文由 ``tools/collect_licenses.py`` 生成（本模块**不复制**那份表格，只做运行时展示）。
"""

import os
import sys

from .paths import app_root
from .vendor import manifest

#: 汇总文件名（与 ``tools/collect_licenses.py``、``build.bat`` 保持一致）
NOTICES_NAME = "THIRD_PARTY_NOTICES.md"

#: 许可正文目录名
LICENSES_NAME = "LICENSES"

#: 内置摘要：发行物漏带文件时的兜底（**别**在这里堆正文，正文在 LICENSES/）
BUILTIN_NOTICES = """第三方声明（内置摘要 —— 完整版见发行物里的 THIRD_PARTY_NOTICES.md）
=========================================================================

pyglgame 引擎：MIT License，Copyright (c) 2022 xyazh。

随引擎分发的第三方组件（全部允许商用；正文见 LICENSES/）：
  * OpenAL Soft（音频 DLL）            LGPL      —— 未修改，可替换；源码见上游
  * FFmpeg（cv2 的 videoio DLL）       LGPL-2.1  —— 动态加载，可替换
  * GLE（PyOpenGL 的 gle*.dll）        IBM 样例源码许可 或 GPLv2（本分发选 IBM）
  * FreeType（Pillow 静态链接）        FTL 或 GPLv2（本分发选 FTL）
      Portions of this software are copyright (c) The FreeType Project
      (https://www.freetype.org/). All rights reserved.
  * numpy / PyOpenGL / Pillow / pyglm / mido / keyboard / line_profiler /
    opencv-python / Box2D / pybullet / freeglut / CPython
                                      BSD / MIT / Apache-2.0 / Zlib / PSF

本发行物不含 GPL / AGPL 传染性组件。
"""

#: 固化包（模块名 → 许可 / 上游 / 正文）：版本来自 VENDOR.json，所以这里只写许可
_VENDORED_LICENSES = {
    "numpy": ("BSD-3-Clause", "https://numpy.org/", "numpy/LICENSE.txt"),
    "OpenGL": ("BSD-3-Clause", "http://pyopengl.sourceforge.net/",
               "pyopengl/LICENSE.txt"),
    "OpenGL_accelerate": ("BSD-3-Clause", "http://pyopengl.sourceforge.net/",
                          "pyopengl/LICENSE.txt"),
    "PIL": ("MIT-CMU", "https://python-pillow.org/", "pillow/LICENSE.txt"),
    "cv2": ("Apache-2.0（含 FFmpeg/LGPL）", "https://opencv.org/",
            "opencv/LICENSE.txt"),
    "pyglm": ("Zlib/libpng", "https://github.com/Zuzu-Typ/PyGLM",
              "pyglm/LICENSE.txt"),
    "mido": ("MIT", "https://github.com/mido/mido", "mido/LICENSE.txt"),
    "line_profiler": ("BSD-3-Clause", "https://github.com/pyutils/line_profiler",
                      "line_profiler/LICENSE.txt"),
    "keyboard": ("MIT", "https://github.com/boppreh/keyboard",
                 "keyboard/LICENSE.txt"),
}

#: 随包分发但不在 VENDOR.json 里的（物理后端 / DLL / 静态链接件）
_EXTRA_COMPONENTS = (
    {"name": "pybullet (Bullet 3)", "version": "3.2.7", "license": "Zlib",
     "home": "https://github.com/bulletphysics/bullet3",
     "license_file": "pybullet/zlib.txt"},
    {"name": "Box2D / pybox2d", "version": "2.3.10", "license": "Zlib",
     "home": "https://box2d.org/", "license_file": "box2d/LICENSE.txt"},
    {"name": "OpenAL Soft", "version": "1.15.1 / 1.18.2", "license": "LGPL",
     "home": "https://github.com/kcat/openal-soft",
     "license_file": "openal-soft/LICENSE-openal-soft.txt"},
    {"name": "FFmpeg（cv2 的 videoio DLL）", "version": None, "license": "LGPL-2.1",
     "home": "https://ffmpeg.org/", "license_file": "ffmpeg/FFmpeg-LICENSE.txt"},
    {"name": "GLE（PyOpenGL 的 DLLS/）", "version": None,
     "license": "IBM 样例源码许可 或 GPLv2（本分发选 IBM）",
     "home": "https://sourceforge.net/projects/gle/", "license_file": "gle/COPYING.txt"},
    {"name": "freeglut（PyOpenGL 的 DLLS/）", "version": None, "license": "MIT/X11",
     "home": "https://freeglut.sourceforge.net/",
     "license_file": "freeglut/LICENSE.txt"},
    {"name": "FreeType（Pillow 静态链接）", "version": "2.13.3",
     "license": "FTL 或 GPLv2（本分发选 FTL，需在文档里致谢）",
     "home": "https://www.freetype.org/", "license_file": "freetype/NOTICE.txt"},
    {"name": "CPython 运行时 / Tcl-Tk", "version": None, "license": "PSF-2.0",
     "home": "https://www.python.org/", "license_file": "python/LICENSE-Python.txt"},
    {"name": "字体：源古黑體 / YuanGuHei（res/fonts/ygblack.ttf）", "version": "3.000",
     "license": "SIL OFL 1.1（Adobe Source Han 衍生，不得单卖字体本身）",
     "home": "https://github.com/adobe-fonts/source-han-sans",
     "license_file": "fonts/LICENSE.txt"},
)

#: FreeType 的 FTL 要求在产品文档里出现的致谢（见 ``LICENSES/freetype/NOTICE.txt``）
FREETYPE_ATTRIBUTION = ("Portions of this software are copyright (c) The FreeType "
                        "Project (https://www.freetype.org/). All rights reserved.")


def noticesPath() -> str:
    """``THIRD_PARTY_NOTICES.md`` 的路径（源码运行=项目根；打包运行=exe 目录）。"""
    return os.path.join(app_root(), NOTICES_NAME)


def licensesDir() -> str:
    """许可正文目录 ``LICENSES/`` 的路径。"""
    return os.path.join(app_root(), LICENSES_NAME)


def noticesText() -> str:
    """汇总全文；文件不在（发行物漏带）就退回 :data:`BUILTIN_NOTICES`。"""
    try:
        with open(noticesPath(), encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return BUILTIN_NOTICES
    return text or BUILTIN_NOTICES


def attributionLine() -> str:
    """一行版权摘要（适合放关于界面第一行或日志）。"""
    return ("pyglgame engine (MIT, (c) 2022 xyazh) — third-party components: "
            "see THIRD_PARTY_NOTICES.md / LICENSES/ "
            "(OpenAL Soft LGPL, FFmpeg LGPL, FreeType FTL, GLE IBM-or-GPL, "
            "BSD/MIT/Apache/Zlib/PSF others)")


def components() -> list:
    """组件表：固化依赖（版本现取 ``VENDOR.json``）＋随包二进制。

    返回 ``[{"name", "version", "license", "home", "license_file"}, …]``；
    ``license_file`` 是相对 ``LICENSES/`` 的路径（关于界面里"查看正文"就是它）。
    """
    items = []
    info = manifest() or {}
    for entry in info.get("packages", []):
        module = entry.get("module", "")
        license_name, home, license_file = _VENDORED_LICENSES.get(
            module, ("见 LICENSES/", "", ""))
        items.append({"name": "%s (%s)" % (module, entry.get("distribution", module)),
                      "version": entry.get("version"),
                      "license": license_name, "home": home,
                      "license_file": license_file})
    items.extend(dict(extra) for extra in _EXTRA_COMPONENTS)
    items.sort(key=lambda item: item["name"].lower())
    return items


def printNotices(text: str = None) -> None:
    """把汇总打到 stdout（demo 的"关于"按键用）。

    ⚠ 命令行工具的 ``print`` 只能用控制台代码页编得出的字符（GBK 上 emoji 会抛
    ``UnicodeEncodeError``，进而把 ``build.bat`` 整个带崩，见 ``docs/Nuitka打包.md``
    §4.5）—— 这里显式兜一层：编不出的字符降级成 ``?``，绝不让它抛。
    """
    payload = noticesText() if text is None else text
    stream = sys.stdout
    try:
        stream.write(payload if payload.endswith("\n") else payload + "\n")
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.write(payload.encode(encoding, "replace").decode(encoding, "replace")
                     + "\n")
    stream.flush()
