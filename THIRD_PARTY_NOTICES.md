# 第三方组件声明（THIRD PARTY NOTICES）

本发行物包含 **pyglgame 引擎**（MIT，Copyright (c) 2022 xyazh）以及下列第三方组件。
各组件的版权归其各自作者所有，并按其自身的许可条款分发。

**合规结论**：本发行物不含 GPL / AGPL 等传染性许可组件，可闭源商用发行。发行方需履行以下义务：

1. 将本文件与各组件完整的许可正文随发行物一并提供；
2. LGPL 组件（OpenAL Soft、FFmpeg）须以独立动态库形式随附，不得修改、不得静态链接，
   并须保持可替换；
3. 就 FreeType 在产品文档或程序的“关于”界面中作出署名（见下）；
4. 保留第 ② 类组件原有的版权与许可声明（Apache-2.0 组件另需保留其 NOTICE 文件）。

## ① 需履行特别义务的组件

| 组件 | 版本 | 许可 | 义务 |
|---|---|---|---|
| OpenAL Soft | 1.15.1 / 1.18.2 | LGPL（GNU Library GPL v2） | 保持独立动态库且可被替换；注明未做修改并附源码地址 |
| FFmpeg（随 opencv-python 分发） | — | LGPL-2.1 | 保持独立动态库；不得替换为含 x264 / x265 的 GPL 版本 |
| GLE（随 PyOpenGL 分发） | vc9 / vc10 / vc14 | IBM 样例源码许可，或 GPLv2（二选一） | 本项目选用 IBM 样例源码许可 |
| FreeType（静态链接于 Pillow） | 2.13.3 | FTL，或 GPLv2（二选一） | 本项目选用 FTL，须在产品文档或“关于”界面中致谢（见下） |

上述组件均以未修改的二进制形式分发（FreeType 静态链接于 Pillow 的扩展模块中）。

FreeType 采用 FTL，其广告条款要求原样保留以下致谢文字：

> Portions of this software are copyright © The FreeType Project
> (https://www.freetype.org/). All rights reserved.

## ② 只需保留声明的组件

以下组件在二进制发行时保留其版权与许可声明即可，无其他约束。

| 组件 | 版本 | 许可 |
|---|---|---|
| numpy（含 OpenBLAS、libdivide、dragon4） | 2.2.6 | BSD-3-Clause |
| PyOpenGL / PyOpenGL_accelerate | 3.1.9 | BSD-3-Clause |
| Pillow（含 brotli、HarfBuzz、lcms2、libjpeg-turbo、libpng、libwebp、OpenJPEG、libtiff、xz、zlib-ng） | 11.2.1 | MIT-CMU |
| PyGLM（含 GLM、pyglm-typing） | 2.8.2 | Zlib/libpng（GLM 与 pyglm-typing 为 MIT） |
| mido | 1.3.3 | MIT |
| keyboard | 0.13.5 | MIT |
| line_profiler | 5.0.2 | BSD-3-Clause（含 PSF-2.0 片段） |
| opencv-python（cv2） | 4.12.0.88 | Apache-2.0（打包器为 MIT） |
| Box2D / pybox2d（2D 物理后端） | 2.3.10 | Zlib |
| pybullet / Bullet 3（3D 物理后端） | 3.2.7 | Zlib |
| freeglut（随 PyOpenGL 分发） | vc9 / vc10 / vc14 | MIT/X11 |
| CPython 运行时与 Tcl/Tk（打包产物自带） | 3.10 | PSF-2.0 / BSD 风格 |

PyOpenGL 的许可另附免责说明：本软件按原样提供，不得用于危及人身安全的场合。

引擎内部另有若干标注为 Public Domain、PSF-2.0 或 MIT 的 Python 模块（如 OpenAL 的 Python 绑定、
由 CPython 标准库派生的模块、lz-string 移植），同属本类，仅需保留其原有声明。

## ③ 素材与内容

| 项目 | 许可 | 说明 |
|---|---|---|
| 内置字体“源古黑体”（YuanGuHei 3.000，Adobe Source Han 思源黑体衍生） | SIL OFL 1.1 | 可随发行物分发；许可须随字体同行；字体本身不得单独出售；若另行改名，不得使用保留字 “Source” |
| 第三方系统字体（如微软雅黑） | 各自专有许可 | 不得随发行物分发 |
| 音频、视频、图像、图集等素材 | 由内容作者确定 | 权属与生成记录由内容作者负责 |

## 分发与程序内呈现

- 发行时需将本文件与各组件许可正文一并置于发行物中。
- 程序内可通过引擎提供的第三方声明接口获取一行署名摘要、组件清单与本文全文，便于实现
  “关于 / 版权”界面。

## 维护要求

- 新增或升级第三方依赖后，须同步更新本文件与随附的许可正文。
- 更换 OpenAL、OpenCV、freeglut 等组件的二进制后，须核对组件版本串并复核本文。
