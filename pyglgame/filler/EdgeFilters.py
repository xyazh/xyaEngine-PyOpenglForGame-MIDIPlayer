"""卷积类滤镜（3×3 邻域）：边缘提取与风格化。

共用一个程序 ``res/shader/filler_edge.frag``：两个类的模式名分开写，
是为了让"边缘提取（Sobel/Laplacian）"和"风格化（浮雕/查找边缘/等高线）"
在代码里也各自成组 —— 它们调的确实是同一个 shader，只是模式不同。
"""

from .FillerBase import FillerBase

#: EdgeFilter：模式名 → filler_edge.frag 的 u_mode
EDGE_MODES = {"sobel": 0, "laplacian": 1}
#: StylizeFilter：模式名 → filler_edge.frag 的 u_mode
STYLIZE_MODES = {"emboss": 2, "find_edges": 3, "contour": 4}


class ConvolutionFilterBase(FillerBase):
    """卷积类滤镜的共同部分：模式名 → 模式号、强度、灰度开关。"""

    SHADER = "filler_edge"
    #: 卷积按像素邻域取样本 → 需要 u_texel
    USES_TEXEL = True
    #: 子类指定支持的模式名表
    MODES: dict = {}

    def __init__(self, mode: str, strength: float = 1.0,
                 gray: bool = True, mix: float = 1.0) -> None:
        super().__init__()
        self.mode_name = str(mode).lower()
        if self.mode_name not in self.MODES:
            raise ValueError(
                f"未知的 {type(self).__name__} 模式 {mode!r}，"
                f"可选：{sorted(self.MODES)}")
        self.mode = self.MODES[self.mode_name]
        #: 倍率：越大边缘越亮 / 越硬
        self.strength = float(strength)
        #: True = 灰度输出（部分模式含义不同，见 shader 里的分支注释）
        self.gray = bool(gray)
        self.mix = float(mix)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        self._uploadCommon(shader)
        shader.uniform1i("u_mode", self.mode)
        shader.uniform1f("u_strength", self.strength)
        shader.uniform1f("u_gray", 1.0 if self.gray else 0.0)


class EdgeFilter(ConvolutionFilterBase):
    """边缘提取。

    - ``mode="sobel"``：梯度幅值（推荐）。
      ``gray=False`` 时用原图颜色调制边缘强度（彩色线条、暗背景）；
    - ``mode="laplacian"``：二阶微分，对噪声更敏感、线条更细（恒为灰度）。

    边界靠输入纹理的 wrap 模式（默认 CLAMP_TO_EDGE），不会跨边采样。
    """

    MODES = EDGE_MODES

    def __init__(self, mode: str = "sobel", strength: float = 1.0,
                 gray: bool = True, mix: float = 1.0) -> None:
        super().__init__(mode, strength, gray, mix)

    # 便于阅读的别名（老代码里常见的写法）
    @property
    def sobel(self) -> bool:
        return self.mode_name == "sobel"


class StylizeFilter(ConvolutionFilterBase):
    """风格化：``emboss`` 浮雕 / ``find_edges`` 查找边缘 / ``contour`` 等高线。

    - ``emboss``：靠 左上-右下 的亮度差压出立体感（``gray=False`` = 彩色浮雕）；
    - ``find_edges``：PS 的"查找边缘"，白底彩色描边（``gray=True`` = 黑白线稿）；
    - ``contour``：按亮度等值带画线，``levels`` = 带数、``width`` = 线宽
      （占一个带的比例，0..0.5）；``gray=True`` 时输出"白底黑线"。
    """

    MODES = STYLIZE_MODES

    def __init__(self, mode: str = "emboss", strength: float = 1.0,
                 gray: bool = True, levels: float = 8.0, width: float = 0.15,
                 mix: float = 1.0) -> None:
        super().__init__(mode, strength, gray, mix)
        #: 等高线的等值带数量
        self.levels = max(float(levels), 2.0)
        #: 等高线的线宽（0..0.5）
        self.width = min(max(float(width), 0.0), 0.5)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        super().uploadUniforms(shader, input_texture, output_texture)
        shader.uniform1f("u_levels", self.levels)
        shader.uniform1f("u_width", self.width)
