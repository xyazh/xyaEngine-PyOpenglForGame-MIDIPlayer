"""模糊类滤镜。

- :class:`GaussianBlurFilter`：可分离高斯模糊（横一趟 + 纵一趟，质量与参数无关）
- :class:`BlurFilter`：单趟的方框 / 方向（动感）/ 径向（缩放）/ 表面（保边）模糊

高斯模糊额外提供 :meth:`GaussianBlurFilter.blurInto`：只算模糊并把**内部低通
纹理**交出来给别的滤镜复用（锐化、Camera Raw 的清晰度都需要它，没必要各算一遍）。
"""

import math

from OpenGL.GL import *

from ..render.TextureStorage2D import TextureStorage2D
from ..xyazhServer.ConsoleMessage import ConsoleMessage
from .CopyFilter import CopyFilter
from .FillerBase import FillerBase, textureSize

#: BlurFilter：模式名 → filler_blur.frag 的 u_mode
BLUR_MODES = {"box": 0, "motion": 1, "radial": 2, "surface": 3}

#: 采样数上限（与 filler_blur.frag 里的 MAX_SAMPLES 一致）
MAX_SAMPLES = 32

#: 中间纹理用的 image unit。7 是引擎约定给"杂项"2D 纹理的单元
#: （Bloom/FFT 用 0..6，3D 噪声也用 7，双方都在 dispatch 前重新绑定，不会互相踩）
TEMP_IMAGE_UNIT = 7


class GaussianBlurFilter(FillerBase):
    """可分离高斯模糊。

    - ``radius`` 单位是**输入纹理的像素**：单趟能扫的最大半径是 :attr:`MAX_RADIUS`，
      更大的半径（例如整屏糊成一片的 400）会自动走**降采样**路径
      （见 :meth:`downscaleFor`），所以半径基本可以随便给；
    - ``sigma=None`` 时按 ``radius / 3`` 推（3σ 以外的权重已经看不到）；
      也可以只给 ``sigma``，半径按 ``ceil(3σ)`` 反推；
    - 中间缓冲固定 **RGBA32F**：两趟之间的量化误差不该由目标纹理的格式决定。
      目标若是 RGBA8，最后写进去时自然量化一次 —— 这才是正确的顺序；
    - 输入/输出尺寸不同时，模糊在**输入分辨率**上算，写到目标时由双线性缩放。
    """

    SHADER = "filler_gaussian"
    #: 采样半径按像素算 → 需要 u_texel
    USES_TEXEL = True
    #: 单趟（全分辨率）的采样半径上限（与 filler_gaussian.frag 的 MAX_RADIUS 一致）
    MAX_RADIUS = 32
    #: 大半径时最多降采样到 1/多少（能支持的最大半径 = MAX_RADIUS × MAX_DOWNSCALE）
    MAX_DOWNSCALE = 32

    def __init__(self, radius: float = None, sigma: float = None,
                 mix: float = 1.0, internal_format: int = GL_RGBA32F) -> None:
        super().__init__()
        if radius is None and sigma is None:
            radius = 4.0
        if radius is None:
            radius = math.ceil(float(sigma) * 3.0)
        limit = self.MAX_RADIUS * self.MAX_DOWNSCALE
        radius = int(round(float(radius)))
        if radius > limit:
            ConsoleMessage.printWarning(
                f"GaussianBlurFilter 半径 {radius} 超过上限 {limit}"
                f"（= MAX_RADIUS {self.MAX_RADIUS} × MAX_DOWNSCALE {self.MAX_DOWNSCALE}），"
                "已截断")
            radius = limit
        self.radius = max(1, radius)
        # sigma 按**最终半径**推（先定半径再算 σ，免得 σ 巨大而采样半径只有一点点）
        self.sigma = max(float(sigma) if sigma is not None else self.radius / 3.0, 0.05)
        self.mix = float(mix)
        self.internal_format = internal_format
        #: 第一趟的落点（横向结果）
        self.temp = None
        #: 第二趟的落点（纵向结果）—— blurInto 用它，主 filler 直接用调用方的目标
        self.result = None
        #: 降采样路径用的小尺寸纹理（按"第几级"缓存）
        self.small_textures = {}
        self._copy = None

    # === 半径 → 降采样倍率 ===

    def downscaleFor(self, radius: int = None) -> int:
        """这个半径要用几倍降采样（2 的幂；1 = 全分辨率直接扫）。

        单趟 shader 只能扫 :attr:`MAX_RADIUS` 个像素，超过就先把图缩小：
        每级 1/2 都是一次**双线性取样 = 2×2 盒平均**，既省采样又顺带抗锯齿。
        半径 400 → 1/16（小图上扫 25 像素），总开销仍然只有几个全屏 pass。
        """
        radius = self.radius if radius is None else int(radius)
        scale = 1
        while scale < self.MAX_DOWNSCALE and radius / scale > self.MAX_RADIUS:
            scale *= 2
        return scale

    @property
    def effectiveRadius(self) -> int:
        """实际生效的半径（降采样路径下是"小图半径 × 倍率"）。"""
        scale = self.downscaleFor()
        return max(1, int(round(self.radius / float(scale)))) * scale

    # === 纹理 ===

    def _allocate(self, width: int, height: int, texture) -> TextureStorage2D:
        if texture is not None and not texture.released:
            texture.release()
        texture = TextureStorage2D(width, height, unit=TEMP_IMAGE_UNIT,
                                   internal_format=self.internal_format)
        texture.useTextureParameteri(GL_LINEAR)
        return texture

    def _ensureTemp(self, width: int, height: int) -> TextureStorage2D:
        if self.temp is None or self.temp.released or (self.temp.w, self.temp.h) != (width, height):
            self.temp = self._allocate(width, height, self.temp)
        return self.temp

    def _ensureSmall(self, level: int, width: int, height: int) -> TextureStorage2D:
        """降采样链上第 ``level`` 级的纹理（尺寸变化时重建）。"""
        texture = self.small_textures.get(level)
        if texture is not None and not texture.released \
                and (texture.w, texture.h) == (width, height):
            return texture
        self.small_textures[level] = self._allocate(width, height, texture)
        return self.small_textures[level]

    # === FillerBase ===

    def filler(self, input_texture, output_texture):
        """算一遍高斯模糊（半径大时自动走降采样路径）。"""
        width, height = textureSize(input_texture)
        scale = self.downscaleFor()
        if scale > 1:
            return self._fillerDownscaled(input_texture, output_texture,
                                          width, height, scale)
        temp = self._ensureTemp(width, height)
        # 第一趟永远全强度；混合系数只在**第二趟**生效，而且以"原图"为基准
        # （两趟各混一次、或拿横向结果当基准都不对 —— 这是实机验证抓出来的）
        self._runPass(input_texture, temp, 1.0, 0.0, 1.0, None)
        self._runPass(temp, output_texture, 0.0, 1.0, self.mix, input_texture)
        return output_texture

    def _fillerDownscaled(self, input_texture, output_texture,
                          width: int, height: int, scale: int):
        """大半径：逐级 1/2 降采样 → 小尺寸上两趟高斯 → 放大时顺便和原图混合。

        每级 1/2 交给 :class:`CopyFilter`（目标只有一半尺寸，双线性取样正好等于
        2×2 盒平均），最后一步用 gaussian shader 的 ``u_radius = 0`` 分支
        （= "把 tex0 与 tex1 按 u_mix 混合"）把结果放大并混合。
        """
        if self._copy is None:
            self._copy = CopyFilter()
        source = input_texture
        level = 0
        side = scale
        while side > 1:
            level += 1
            side //= 2
            small = self._ensureSmall(level, max(1, width >> level),
                                      max(1, height >> level))
            self._copy.filler(source, small)
            source = small
        small_width, small_height = textureSize(source)
        temp = self._ensureSmall(level + 1, small_width, small_height)
        result = self._ensureSmall(level + 2, small_width, small_height)
        radius = max(1, min(int(round(self.radius / float(scale))), self.MAX_RADIUS))
        sigma = max(self.sigma / float(scale), 0.05)
        self._runPass(source, temp, 1.0, 0.0, 1.0, None,
                      radius=radius, sigma=sigma)
        self._runPass(temp, result, 0.0, 1.0, 1.0, None,
                      radius=radius, sigma=sigma)
        # 放大 + 混合：radius=0 那条分支（tex1 = 原图，见 filler_gaussian.frag）
        self._runPass(result, output_texture, 0.0, 0.0, self.mix, input_texture,
                      radius=0, sigma=sigma)
        return output_texture

    def blurInto(self, input_texture) -> TextureStorage2D:
        """只算模糊，结果落在内部低通纹理上并返回它（供其它滤镜复用）。"""
        width, height = textureSize(input_texture)
        if self.result is None or self.result.released or (self.result.w, self.result.h) != (width, height):
            self.result = self._allocate(width, height, self.result)
        return self.filler(input_texture, self.result)

    def _runPass(self, input_texture, output_texture, dir_x: float, dir_y: float,
                 mix: float, mix_source=None, radius: int = None,
                 sigma: float = None) -> None:
        width, height = textureSize(input_texture)
        self._input_size = (width, height)
        shader = self._beginPass(output_texture)
        self._bindInput(input_texture, 0)
        if mix_source is not None:
            # tex1 = 原图：第二趟的混合基准（见 filler_gaussian.frag）
            self._bindInput(mix_source, 1)
        self.uploadPassUniforms(shader, dir_x, dir_y, mix,
                                1 if mix_source is not None else 0,
                                radius=radius, sigma=sigma)
        self._draw()
        self._endPass()

    def uploadPassUniforms(self, shader, dir_x: float, dir_y: float,
                           mix: float = None, mix_source: int = 0,
                           radius: int = None, sigma: float = None) -> None:
        """一趟（横向 / 纵向）要传的 uniform（单测按它检查 shader 契约）。

        ``radius`` / ``sigma`` 不给就用实例自己的（降采样路径会传"小图上的"值）。
        """
        if mix is None:
            mix = self.mix
        self._uploadCommon(shader)
        shader.uniform1f("u_mix", float(mix))
        shader.uniform1i("u_mixSource", int(mix_source))
        shader.uniform2f("u_dir", float(dir_x), float(dir_y))
        shader.uniform1f("u_sigma", float(self.sigma if sigma is None else sigma))
        shader.uniform1i("u_radius", int(self.radius if radius is None else radius))

    def release(self) -> None:
        super().release()
        for name in ("temp", "result"):
            texture = getattr(self, name, None)
            if texture is not None and not texture.released:
                texture.release()
            setattr(self, name, None)
        for texture in self.small_textures.values():
            if texture is not None and not texture.released:
                texture.release()
        self.small_textures = {}
        if self._copy is not None:
            self._copy.release()
            self._copy = None


class BlurFilter(FillerBase):
    """单趟模糊：``box`` 方框 / ``motion`` 方向 / ``radial`` 径向 / ``surface`` 表面。

    - ``box``：``radius`` = 网格半宽（像素）
    - ``motion``：``radius`` = 拖尾长度（像素），``direction`` = 拖动方向
    - ``radial``：``radius`` = 向中心收缩的幅度（像素），``center`` = 中心（uv）
    - ``surface``：保边模糊，``radius`` = 邻域步长、``threshold`` = 色差容限
      （容限越大越接近普通模糊；越小越"平而不糊"）

    ``samples`` 是一维采样点数（box/surface 是 ``samples²`` 个），上限 32。
    追求高质量的大半径请用 :class:`GaussianBlurFilter`。
    """

    SHADER = "filler_blur"
    #: 半径按像素算 → 需要 u_texel
    USES_TEXEL = True

    def __init__(self, mode: str = "box", radius: float = 4.0, samples: int = 8,
                 threshold: float = 0.08, direction: tuple = (1.0, 0.0),
                 center: tuple = (0.5, 0.5), mix: float = 1.0) -> None:
        super().__init__()
        self.mode_name = str(mode).lower()
        if self.mode_name not in BLUR_MODES:
            raise ValueError(f"未知的 BlurFilter 模式 {mode!r}，"
                             f"可选：{sorted(BLUR_MODES)}")
        self.mode = BLUR_MODES[self.mode_name]
        self.radius = max(float(radius), 0.0)
        self.samples = int(max(1, min(int(samples), MAX_SAMPLES)))
        self.threshold = max(float(threshold), 1e-4)
        self.direction = (float(direction[0]), float(direction[1]))
        self.center = (float(center[0]), float(center[1]))
        self.mix = float(mix)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        self._uploadCommon(shader)
        shader.uniform1i("u_mode", self.mode)
        shader.uniform1f("u_radius", self.radius)
        shader.uniform1i("u_samples", self.samples)
        shader.uniform1f("u_threshold", self.threshold)
        shader.uniform2f("u_dir", self.direction[0], self.direction[1])
        shader.uniform2f("u_center", self.center[0], self.center[1])
