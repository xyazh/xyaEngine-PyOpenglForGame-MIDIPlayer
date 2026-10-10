"""滤镜（后处理）集合：输入一张纹理、输出（渲染）到另一张纹理。

所有滤镜都是 :class:`~pyglgame.filler.FillerBase.FillerBase` 的子类，接口统一：

.. code-block:: python

    from OpenGL.GL import GL_RGBA16F
    from pyglgame.filler import GrayscaleFilter, GaussianBlurFilter
    from pyglgame.render.TextureStorage2D import TextureStorage2D

    dst = TextureStorage2D(width, height, internal_format=GL_RGBA16F)
    GrayscaleFilter().filler(src_texture, dst)          # src/dst 都是 TextureBase
    GaussianBlurFilter(radius=6).filler(src_texture, dst)
    # 返回 dst，可以直接串成链：
    # GaussianBlurFilter().filler(GrayscaleFilter().filler(src, tmp), dst)

形状/口径上的约定（每个类都遵守）：

- ``filler(input_texture, output_texture)``：第一个是**输入源纹理**，
  第二个是**滤镜结果要写进去的目标纹理**，返回目标纹理；
- 目标纹理可以是任何能当颜色附件的纹理（``TextureStorage2D`` / ``Texture``），
  输入纹理的格式也不限（RGBA8 / RGBA16F / RGBA32F 都行）；
- ``mix=0`` 输出原图、``mix=1`` 输出滤镜结果（给 UI 滑块用）；alpha 默认原样保留
  （模糊一族会把 alpha 一起模糊）；
- 滤镜不会把 GL 状态留给调用方（混合/深度/视口/当前 program 都会还原）。

清单（按家族分组，都能单独用，也能串链）：

=========================  ==================================================
色彩（点操作）              GrayscaleFilter 黑白、PixelateFilter 像素化、
                          PosterizeFilter 色调分离、SolarizeFilter 曝光过度、
                          InvertFilter 反相、ThresholdFilter 阈值
卷积（3×3）                EdgeFilter 边缘提取（Sobel/Laplacian）、
                          StylizeFilter 风格化（浮雕/查找边缘/等高线）
模糊                       GaussianBlurFilter 可分离高斯、
                          BlurFilter 方框/方向/径向/表面
锐化                       SharpenFilter（USM，内部借高斯模糊算低通）
整体调色                   CameraRawFilter（曝光/白平衡/影调/清晰度/暗角/颗粒）
频率域                     FFTFilter、InverseFFTFilter、Spectrum2D
工具                       CopyFilter 拷贝/缩放/格式转换、Bloom（compute 泛光）
=========================  ==================================================

每个类的实现细节（着色器、参数含义、为什么这么做）写在各模块的 docstring 与
``res/shader/filler_*.frag`` 的头部注释里；完整说明（含相机滤镜链、频率域滤波、
踩过的坑）见 ``docs/滤镜与后处理.md``，在游戏里把滤镜结果画出来的例子见
``Test3.py``（FFT 频谱图 / 逆变换往返）。

挂到相机最终输出上（单个或多个，按加入顺序执行）::

    camera.addFilter(GrayscaleFilter())
    camera.addFilter(EdgeFilter("sobel"))      # 吃到灰度之后的结果
    camera.setFilters(PixelateFilter(block=6), InvertFilter())
    camera.clearFilters()                      # 摘掉全部
    camera.useFilters(False)                   # 暂时不跑（对象仍挂在相机上）

相机在 ``renderEnd()`` 里自动跑一遍，窗口输出采样链的结果；没挂滤镜时
这条链路零开销（不建纹理、不多跑 pass、画面逐像素不变）。
"""

from .Bloom import Bloom
from .BlurFilters import (BLUR_MODES, MAX_SAMPLES, BlurFilter,
                          GaussianBlurFilter)
from .CameraRawFilter import CameraRawFilter
from .ColorFilters import (ColorFilterBase, GrayscaleFilter, InvertFilter,
                           PixelateFilter, PosterizeFilter, SolarizeFilter,
                           ThresholdFilter)
from .CopyFilter import CopyFilter
from .EdgeFilters import EDGE_MODES, STYLIZE_MODES, EdgeFilter, StylizeFilter
from .FFTFilters import (VIEW_COMPLEX, VIEW_DISPLAY, VIEW_REAL, FFTFilter,
                         InverseFFTFilter, Spectrum2D, checkFFTSize,
                         isPowerOfTwo, potSize)
from .FillerBase import FillerBase, loadProgram, textureId, textureSize
from .FrequencyFilters import MASK_MODES, MASK_SHAPES, FrequencyMaskFilter
from .FilterChain import FilterChain
from .SharpenFilter import SharpenFilter

__all__ = [
    # 基类与工具
    "FillerBase", "loadProgram", "textureSize", "textureId", "FilterChain",
    "FrequencyMaskFilter", "MASK_MODES", "MASK_SHAPES",
    # 色彩
    "ColorFilterBase", "GrayscaleFilter", "PixelateFilter", "PosterizeFilter",
    "SolarizeFilter", "InvertFilter", "ThresholdFilter",
    # 卷积 / 风格化
    "EdgeFilter", "StylizeFilter", "EDGE_MODES", "STYLIZE_MODES",
    # 模糊 / 锐化
    "GaussianBlurFilter", "BlurFilter", "BLUR_MODES", "MAX_SAMPLES",
    "SharpenFilter",
    # 整体调色
    "CameraRawFilter",
    # 频率域
    "FFTFilter", "InverseFFTFilter", "Spectrum2D", "isPowerOfTwo",
    "checkFFTSize", "potSize", "VIEW_DISPLAY", "VIEW_COMPLEX", "VIEW_REAL",
    # 工具 / 老成员
    "CopyFilter", "Bloom",
]

