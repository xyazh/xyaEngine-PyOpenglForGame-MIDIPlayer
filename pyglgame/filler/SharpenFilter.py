"""锐化（USM，Unsharp Mask）。

``detail = 原图 - 低通图``，再按 ``amount`` 加回去；低通图由内部的高斯模糊
（:class:`~pyglgame.filler.BlurFilters.GaussianBlurFilter`）现算 ——
这就是 :meth:`FillerBase.inputs` 需要"两张输入纹理"的原因（tex0 / tex1）。

``threshold`` 只锐化"差异够明显"的像素：平坦区域（噪点、渐变里的色带）不动，
否则锐化会把噪声一起放大。
"""

from .BlurFilters import GaussianBlurFilter
from .FillerBase import FillerBase


class SharpenFilter(FillerBase):
    """USM 锐化：``amount`` 强度、``radius``/``sigma`` 低通半径、``threshold`` 门槛。"""

    SHADER = "filler_sharpen"

    def __init__(self, amount: float = 1.0, radius: float = 2.0, sigma: float = None,
                 threshold: float = 0.0, mix: float = 1.0) -> None:
        super().__init__()
        self.amount = float(amount)
        self.threshold = max(float(threshold), 0.0)
        self.mix = float(mix)
        #: 低通图生成器（内部持有两张 RGBA32F 中间纹理）
        self.blur = GaussianBlurFilter(radius=radius, sigma=sigma)
        #: 当前这帧的低通纹理（prepare 里生成）
        self.lowpass = None

    def prepare(self, input_texture, output_texture) -> None:
        self.lowpass = self.blur.blurInto(input_texture)

    def inputs(self, input_texture, output_texture) -> tuple:
        # tex0 = 原图，tex1 = 低通图
        return (input_texture, self.lowpass if self.lowpass is not None else input_texture)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        self._uploadCommon(shader)
        shader.uniform1f("u_amount", self.amount)
        shader.uniform1f("u_threshold", self.threshold)

    def release(self) -> None:
        super().release()
        if self.blur is not None:
            self.blur.release()
            self.blur = None
        self.lowpass = None
