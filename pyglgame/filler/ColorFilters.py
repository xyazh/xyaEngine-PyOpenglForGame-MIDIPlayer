"""色彩类滤镜（逐像素点操作）。

都走同一个程序 ``res/shader/filler_color.frag``（用 ``u_mode`` 选算法，和 SSAO /
体积渲染"一个程序多种模式"的写法一致），所以每个类都很薄：只做参数校验 +
上传 uniform。

用法（任何滤镜都一样）::

    from pyglgame.filler import GrayscaleFilter
    GrayscaleFilter().filler(src_texture, dst_texture)
"""

from .FillerBase import FillerBase

#: 与 filler_color.frag 里的 u_mode 一一对应
MODE_GRAYSCALE = 0
MODE_PIXELATE = 1
MODE_POSTERIZE = 2
MODE_SOLARIZE = 3
MODE_INVERT = 4
MODE_THRESHOLD = 5


class ColorFilterBase(FillerBase):
    """色彩类滤镜的共同部分：``u_mode`` + 公共 uniform（``u_texel`` / ``u_mix``）。"""

    SHADER = "filler_color"
    #: 子类必须指定 shader 里的模式号
    MODE: int = None

    def __init__(self, mix: float = 1.0) -> None:
        if self.MODE is None:
            raise ValueError(f"{type(self).__name__} 没有指定 MODE")
        super().__init__()
        #: 0 = 原图、1 = 完整滤镜（"滑块"用）
        self.mix = float(mix)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        self._uploadCommon(shader)
        shader.uniform1i("u_mode", self.MODE)


class GrayscaleFilter(ColorFilterBase):
    """黑白（灰度）。

    ``rec709``：True = 用 Rec.709 亮度权重（0.2126/0.7152/0.0722，人眼观感、
    和引擎光照里的亮度一致），False = 三通道平均（RGB 中性）。
    """

    MODE = MODE_GRAYSCALE

    def __init__(self, rec709: bool = True, mix: float = 1.0) -> None:
        super().__init__(mix)
        self.rec709 = bool(rec709)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        super().uploadUniforms(shader, input_texture, output_texture)
        shader.uniform1f("u_luma", 1.0 if self.rec709 else 0.0)


class PixelateFilter(ColorFilterBase):
    """像素化（马赛克）。

    ``block`` 是块的边长（**输入纹理的像素**）。块内颜色完全一致、边缘是硬的；
    想要"马赛克"效果请把输入/输出纹理的过滤方式设成 ``NEAREST``
    （``TextureStorage2D.useTextureParameteri(GL_NEAREST)``）。
    """

    MODE = MODE_PIXELATE

    #: 按像素算块大小 → 需要 u_texel
    USES_TEXEL = True

    def __init__(self, block: float = 8.0, mix: float = 1.0) -> None:
        super().__init__(mix)
        self.block = max(float(block), 1.0)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        super().uploadUniforms(shader, input_texture, output_texture)
        shader.uniform1f("u_block", self.block)


class PosterizeFilter(ColorFilterBase):
    """色调分离：把每个通道量化到 ``levels`` 个色阶。"""

    MODE = MODE_POSTERIZE

    def __init__(self, levels: int = 6, mix: float = 1.0) -> None:
        super().__init__(mix)
        self.levels = max(int(levels), 2)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        super().uploadUniforms(shader, input_texture, output_texture)
        shader.uniform1f("u_levels", float(self.levels))


class SolarizeFilter(ColorFilterBase):
    """曝光过度（Solarize）：亮于 ``threshold`` 的通道反相。"""

    MODE = MODE_SOLARIZE

    def __init__(self, threshold: float = 0.5, mix: float = 1.0) -> None:
        super().__init__(mix)
        self.threshold = float(threshold)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        super().uploadUniforms(shader, input_texture, output_texture)
        shader.uniform1f("u_threshold", self.threshold)


class InvertFilter(ColorFilterBase):
    """反相（底片）。"""

    MODE = MODE_INVERT

    def __init__(self, mix: float = 1.0) -> None:
        super().__init__(mix)


class ThresholdFilter(ColorFilterBase):
    """阈值：按亮度切成纯黑/纯白（二值化）。"""

    MODE = MODE_THRESHOLD

    def __init__(self, threshold: float = 0.5, rec709: bool = True, mix: float = 1.0) -> None:
        super().__init__(mix)
        self.threshold = float(threshold)
        self.rec709 = bool(rec709)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        super().uploadUniforms(shader, input_texture, output_texture)
        shader.uniform1f("u_threshold", self.threshold)
        shader.uniform1f("u_luma", 1.0 if self.rec709 else 0.0)
