"""频域滤镜：直接作用在**复频谱**上的掩码（低通 / 高通 / 带通 / 陷波）。

.. code-block:: python

    # 1) 链式（单通道 / 亮度）：频谱当图片改完再还原
    camera.setFilters(FFTFilter(view="complex"),
                      FrequencyMaskFilter("lowpass", cutoff=0.2),
                      InverseFFTFilter(view="complex"))

    # 2) 彩色精确往返：直接改 Spectrum2D 的实部/虚部
    fft = FFTFilter()
    fft.filler(src, view_texture)
    fft.spectrum.edit(FrequencyMaskFilter("highpass", cutoff=0.3))
    InverseFFTFilter(spectrum=fft.spectrum).filler(src, dst)

掩码的语义：按"到零频的距离"给每个频点乘一个系数，**实部/虚部乘同一个系数** ——
只改幅度、不改相位，所以逆变换回来就是教科书里的频域滤波效果：

- ``lowpass``：柔化 / 去噪 / 抽掉细节（越大越糊）
- ``highpass``：只留边缘（类似"查找边缘"的频域版）
- ``bandpass``：只留某个频带（拉丝 / 纹理 / 去网纹的反向）
- ``notch``：掐掉某个频带（**去周期性网纹/摩尔纹**的主力）

``shape`` 选择过渡形状：``ideal`` 硬边（便宜但有振铃）、``gaussian`` 软（推荐）、
``butterworth`` 阶数可调（``order`` 越大越接近硬边）。
"""

from .FillerBase import FillerBase

#: 掩码模式名 → shader 里的 u_mode
MASK_MODES = {"lowpass": 0, "highpass": 1, "bandpass": 2, "notch": 3}
#: 过渡形状名 → shader 里的 u_shape
MASK_SHAPES = {"ideal": 0, "gaussian": 1, "butterworth": 2}


class FrequencyMaskFilter(FillerBase):
    """频域掩码滤镜（作用在复频谱上，见模块文档）。

    - ``cutoff``：归一化半径（0~1；``1`` = 最高频）。非方形图上按**每轴归一化**
      计算，所以是"频率意义上的圆"；
    - ``width``：``bandpass`` / ``notch`` 的带宽；
    - ``order``：``butterworth`` 的阶数（越大越接近 hard cutoff）；
    - ``center``：零频所在位置（fftshift 之后默认在画面中心）。
    """

    SHADER = "filler_freq_mask"

    def __init__(self, mode: str = "lowpass", shape: str = "gaussian",
                 cutoff: float = 0.25, width: float = 0.1, order: float = 2.0,
                 center: tuple = (0.5, 0.5), mix: float = 1.0) -> None:
        super().__init__()
        self.mode_name = str(mode).lower()
        if self.mode_name not in MASK_MODES:
            raise ValueError(
                f"未知的 FrequencyMaskFilter 模式 {mode!r}，可选：{sorted(MASK_MODES)}")
        self.shape_name = str(shape).lower()
        if self.shape_name not in MASK_SHAPES:
            raise ValueError(
                f"未知的 FrequencyMaskFilter 过渡形状 {shape!r}，"
                f"可选：{sorted(MASK_SHAPES)}")
        self.mode = MASK_MODES[self.mode_name]
        self.shape = MASK_SHAPES[self.shape_name]
        self.cutoff = min(max(float(cutoff), 0.0), 1.0)
        self.width = min(max(float(width), 0.0), 1.0)
        self.order = max(float(order), 0.1)
        self.center = (float(center[0]), float(center[1]))
        self.mix = float(mix)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        self._uploadCommon(shader)
        shader.uniform1i("u_mode", self.mode)
        shader.uniform1i("u_shape", self.shape)
        shader.uniform1f("u_cutoff", self.cutoff)
        shader.uniform1f("u_width", max(self.width, 1e-4))
        shader.uniform1f("u_order", self.order)
        shader.uniform2f("u_center", self.center[0], self.center[1])
