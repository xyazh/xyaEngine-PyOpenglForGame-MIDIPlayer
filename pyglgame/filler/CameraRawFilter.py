"""Camera Raw 风格的整体显影（一个滤镜打包"面板上那一排滑块"）。

处理顺序与 Camera Raw 面板一致：曝光 → 白平衡 → 高光/阴影/白色/黑色 →
对比度 → 清晰度/锐化 → 鲜艳度 → 饱和度 → 暗角 → 颗粒
（每一步的数学都在 ``res/shader/filler_camera_raw.frag`` 里逐条注释）。

几点使用说明：

- 输入按**线性 HDR** 处理：曝光是 2^EV（物理正确），其余滑块是归一化参数
  ``[-1, 1]``（0 = 不动），方便直接接 UI 滑块；
- 清晰度与锐化**共用同一张低通图**（内部的高斯模糊，只在真的用到时才创建、
  才多跑一趟模糊）：只调曝光/颜色时不会多占显存也不会多算；
- 颗粒用屏幕空间 hash（同一参数每次结果一致，不会闪）。
"""

from .BlurFilters import GaussianBlurFilter
from .FillerBase import FillerBase


class CameraRawFilter(FillerBase):
    """Camera Raw：``exposure``（EV）/ 对比 / 高光 / 阴影 / 白平衡 / 色彩 / 清晰度 / 锐化 / 暗角 / 颗粒。"""

    SHADER = "filler_camera_raw"
    #: 颗粒用"像素坐标"做 hash → 需要 u_texel
    USES_TEXEL = True

    def __init__(self, exposure: float = 0.0, contrast: float = 0.0,
                 highlights: float = 0.0, shadows: float = 0.0,
                 whites: float = 0.0, blacks: float = 0.0,
                 temperature: float = 0.0, tint: float = 0.0,
                 vibrance: float = 0.0, saturation: float = 0.0,
                 clarity: float = 0.0, sharpen: float = 0.0,
                 vignette: float = 0.0, grain: float = 0.0,
                 lowpass_radius: float = 4.0, mix: float = 1.0) -> None:
        super().__init__()
        self.exposure = float(exposure)
        self.contrast = float(contrast)
        self.highlights = float(highlights)
        self.shadows = float(shadows)
        self.whites = float(whites)
        self.blacks = float(blacks)
        self.temperature = float(temperature)
        self.tint = float(tint)
        self.vibrance = float(vibrance)
        self.saturation = float(saturation)
        self.clarity = float(clarity)
        self.sharpen = float(sharpen)
        self.vignette = float(vignette)
        self.grain = float(grain)
        self.lowpass_radius = float(lowpass_radius)
        self.mix = float(mix)
        #: 低通图生成器（懒建：不用清晰度/锐化就不占显存）
        self.blur = None
        self.lowpass = None

    def needsLowpass(self) -> bool:
        """清晰度或锐化非 0 时才需要低通图（= 多一趟高斯模糊）。"""
        return self.clarity != 0.0 or self.sharpen != 0.0

    def prepare(self, input_texture, output_texture) -> None:
        if not self.needsLowpass():
            self.lowpass = None
            return
        if self.blur is None:
            self.blur = GaussianBlurFilter(radius=self.lowpass_radius)
        self.lowpass = self.blur.blurInto(input_texture)

    def inputs(self, input_texture, output_texture) -> tuple:
        # tex1 总是要绑（哪怕是原图）：shader 里用 u_hasLowpass 决定要不要采样它
        return (input_texture, self.lowpass if self.lowpass is not None else input_texture)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        self._uploadCommon(shader)
        shader.uniform1i("u_hasLowpass", 1 if self.lowpass is not None else 0)
        shader.uniform1f("u_exposure", self.exposure)
        shader.uniform1f("u_contrast", self.contrast)
        shader.uniform1f("u_highlights", self.highlights)
        shader.uniform1f("u_shadows", self.shadows)
        shader.uniform1f("u_whites", self.whites)
        shader.uniform1f("u_blacks", self.blacks)
        shader.uniform1f("u_temperature", self.temperature)
        shader.uniform1f("u_tint", self.tint)
        shader.uniform1f("u_vibrance", self.vibrance)
        shader.uniform1f("u_saturation", self.saturation)
        shader.uniform1f("u_clarity", self.clarity)
        shader.uniform1f("u_sharpen", self.sharpen)
        shader.uniform1f("u_vignette", self.vignette)
        shader.uniform1f("u_grain", self.grain)

    def release(self) -> None:
        super().release()
        if self.blur is not None:
            self.blur.release()
            self.blur = None
        self.lowpass = None
