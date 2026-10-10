# LICENSES —— 随包分发的第三方许可正文

本目录由 `tools/collect_licenses.py` **生成**（刷新手改会被覆盖）。
人读的汇总与义务清单在仓库根的 `THIRD_PARTY_NOTICES.md`，
本项目的做法与维护方式见 `docs/第三方许可.md`。

| 组件 | 版本 | 许可 | 正文（本目录下） | 上游 / 备注 |
|---|---|---|---|---|
| Box2D / pybox2d | 2.3.10 | Zlib | `box2d/LICENSE.txt`、`box2d/METADATA.txt` | https://box2d.org/ —— 2D 物理后端（_Box2D.cp310-win_amd64.pyd） |
| FFmpeg（随 opencv-python 的 cv2/ 一起分发） | - | LGPL-2.1 | `ffmpeg/FFmpeg-LICENSE.txt` | https://ffmpeg.org/ —— 只在 cv2/opencv_videoio_ffmpeg4120_64.dll 里（Game4 出片用）；动态加载、可替换。**别**换成带 x264/x265 的 GPL 自编版 |
| res/fonts/ygblack.ttf（源古黑體 / YuanGuHei） | 3.000 | SIL OFL 1.1 | `fonts/LICENSE.txt` | https://github.com/adobe-fonts/source-han-sans —— Adobe Source Han（思源黑体）衍生；OFL 要求许可随字体走，保留字 'Source' 受保护、字体本身不得单卖 |
| 字体说明（res/fonts/ 的 OFL 义务） | - | SIL OFL 1.1 | `fonts/NOTICE.txt` | https://openfontlicense.org/ —— 为什么素材层的字体也要声明、以后加字体的约定 |
| freeglut（随 PyOpenGL 的 DLLS/ 分发） | vc9 / vc10 / vc14 构建 | MIT/X11 | `freeglut/LICENSE.txt`、`freeglut/README.txt` | https://freeglut.sourceforge.net/ —— OpenGL/DLLS/freeglut{32,64}.vc*.dll；只允许用其名做事实性署名 |
| FreeType（Pillow 静态链接） | 2.13.3 | FTL（或 GPLv2，二选一 → 本项目选 FTL） | `freetype/NOTICE.txt` | https://www.freetype.org/ —— FTL 要求在**产品文档**里致谢 FreeType Project |
| GLE（随 PyOpenGL 的 DLLS/ 分发） | vc9 / vc10 / vc14 构建 | IBM 样例源码许可 **或** GPLv2（二选一） | `gle/COPYING.txt`、`gle/COPYING.src.txt`、`gle/AUTHORS.txt`、`gle/README.txt` | https://sourceforge.net/projects/gle/ —— 本分发选 **IBM** 那一支（免费使用/分发/修改），不要选 GPLv2 |
| keyboard | 0.13.5 | MIT | `keyboard/LICENSE.txt` | https://github.com/boppreh/keyboard —— BaseWindow 的可选按键钩子 |
| line_profiler | 5.0.2 | BSD-3-Clause（另含 CPython/PSF 片段） | `line_profiler/LICENSE.txt`、`line_profiler/Python-LICENSE.txt` | https://github.com/pyutils/line_profiler —— Python-LICENSE.txt 是 PSF-2.0（供派生的 stdlib 代码用） |
| mido | 1.3.3 | MIT | `mido/LICENSE.txt` | https://github.com/mido/mido —— Game4 用它读 MIDI |
| numpy | 2.2.6 | BSD-3-Clause | `numpy/LICENSE.txt` | https://numpy.org/ —— 轮子里还带 OpenBLAS(BSD) / libdivide(zlib) / dragon4(MIT)，正文后半段列了 |
| OpenAL Soft（音频后端 DLL） | 1.15.1 / 1.18.2 | LGPL | `openal-soft/LICENSE-openal-soft.txt` | https://github.com/kcat/openal-soft —— 未修改、动态加载可替换；同时写到 pyglgame/openal/LICENSE-openal-soft.txt |
| opencv-python（cv2） | 4.12.0.88 | Apache-2.0（打包器那份是 MIT） | `opencv/opencv-python-LICENSE.txt`、`opencv/LICENSE.txt`、`opencv/THIRD-PARTY.txt` | https://opencv.org/ —— THIRD-PARTY.txt 是官方汇总（Windows 轮子里真正非宽松的只有 FFmpeg） |
| Pillow（PIL） | 11.2.1 | MIT-CMU（HPND 变体） | `pillow/LICENSE.txt` | https://python-pillow.org/ —— 一个文件里按组件分段：brotli / FreeType / HarfBuzz / lcms2 / libjpeg-turbo / libpng / libwebp / OpenJPEG / libtiff / xz / zlib-ng |
| pybullet（Bullet 3） | 3.2.7 | Zlib | `pybullet/AUTHORS.txt`、`pybullet/METADATA.txt` | https://github.com/bulletphysics/bullet3 —— AUTHORS.txt 是上游的 LICENSE.txt（只有作者名单）+ META；正文见 zlib.txt |
| pybullet 的 zlib 正文 | - | Zlib | `pybullet/zlib.txt` | https://github.com/bulletphysics/bullet3 —— 上游许可证正文（随包的 pybullet-LICENSE.txt 只有作者名单） |
| PyGLM（含 GLM、pyglm-typing） | 2.8.2 | Zlib/libpng | `pyglm/LICENSE.txt`、`pyglm/COPYING.txt` | https://github.com/Zuzu-Typ/PyGLM —— GLM 与 pyglm-typing 是 MIT，见 COPYING |
| PyOpenGL / PyOpenGL_accelerate | 3.1.9 | BSD-3-Clause | `pyopengl/LICENSE.txt`、`pyopengl/METADATA.txt` | http://pyopengl.sourceforge.net/ —— 另有免责声明：非容错设计，不得用于危及人身/财产的场合 |
| CPython 运行时（python310.dll / tcl / tk 等） | - | PSF-2.0（Tcl/Tk 是 BSD 风格） | `python/LICENSE-Python.txt` | https://www.python.org/ —— Nuitka 产物里自带；引擎内 xyazhServer/ihttp/cookiejar.py 也派生自 stdlib |
| GNU LGPL v2.1（正文；FFmpeg / OpenAL Soft 共用） | - | LGPL-2.1 | `LGPL-2.1.txt` | https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html —— 从 opencv-python 的第三方清单里原样截取（同一份文本） |

## 怎么用

* **发行**：整份目录跟 exe 一起发（`build.bat` 用 `--include-data-dir=./LICENSES=LICENSES`）；
  人读的入口是 `THIRD_PARTY_NOTICES.md`；
* **游戏内「关于/版权」**：`pyglgame.tools.notices`（`noticesText()` / `components()` /
  `attributionLine()`），见 `docs/第三方许可.md`；
* **升级依赖**：`pyglgame/libs` 里的版本变了就重跑 `python tools/collect_licenses.py`
  （版本号取自 `pyglgame/libs/VENDOR.json`，会自己跟上）；
* **检查**：`python tools/collect_licenses.py --verify`（`build.bat` 每次都会跑）。

