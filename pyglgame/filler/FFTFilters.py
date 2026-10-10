"""FFT / 逆 FFT 滤镜（频率域）。

- :class:`FFTFilter`：空间域图像 → **复频谱**（同时输出一张"频谱图"给人看）
- :class:`InverseFFTFilter`：复频谱（或单张实值频谱）→ 空间域图像
- :class:`Spectrum2D`：复频谱容器（两张 RGBA32F 纹理，``.rgb`` 分别是 R/G/B
  通道的实部与虚部），支持 :meth:`Spectrum2D.getData` / :meth:`Spectrum2D.setData`
  在 CPU 侧直接读改 —— 频率域滤波实验（低通/高通/陷波）就靠它。

计算走 ``res/shader/computeFFT.comp``（radix-2，原地蝶形 + 位反转输入），
最后用拷贝滤镜把结果落到**调用方给的任意纹理**上 —— compute shader 只能写
image unit（``layout(rgba32f)`` 与纹理格式必须严格一致），落到任意格式的目标
纹理还是得走一遍 fragment。

尺寸必须是 **2 的幂**（位反转的要求），alpha 不参与变换。

显存提示：``FFTFilter`` 默认占 3（蝶形 work）+ 1（输出图）+ 2（频谱快照）
共 6 张 RGBA32F：512² ≈ 25 MB、1024² ≈ 100 MB。只要"看一眼频谱图"的话用
``keep_spectrum=False`` 省掉快照那两张。

频率域滤波（先正变换 → 改频谱 → 再逆变换）::

    fft = FFTFilter()
    fft.filler(src, spectrum_view)        # src 的频谱图（给人看的）
    real, imag = fft.spectrum.getData()   # (h, w, 3) 两张 float32
    mask = lowpassMask(real.shape)        # 例如理想低通
    fft.spectrum.setData(real * mask, imag * mask)
    InverseFFTFilter(spectrum=fft.spectrum).filler(src, dst)   # 往返
"""

import math

import numpy as np
from OpenGL.GL import *

from ..RenderGlobal import RenderGlobal
from ..render.TextureStorage2D import TextureStorage2D
from .CopyFilter import CopyFilter
from .FillerBase import FillerBase, textureId, textureSize

# === 与 computeFFT.comp 里的 image unit 绑定一致 ===
#: workR / workG / workB：每个颜色通道的复数（.r = 实部、.g = 虚部）
WORK_UNITS = (1, 2, 3)
#: 频谱图 / 逆变换结果的落点
OUTPUT_UNIT = 4
#: 复频谱快照：实部 / 虚部（各是一张 .rgb 纹理）
SPECTRUM_UNITS = (5, 6)
#: "把输入缩到 2 的幂"用的缓冲（7 = 引擎约定给杂项 2D 纹理的单元）
INPUT_UNIT = 7

# === computeFFT.comp 的 mode ===
MODE_INIT_IMAGE = 0
MODE_FFT_HORIZONTAL = 1
MODE_FFT_VERTICAL = 2
MODE_SPECTRUM_VIEW = 3
MODE_INIT_SPECTRUM = 4
MODE_INVERSE_VIEW = 5
MODE_INIT_REAL_SPECTRUM = 6
MODE_STORE_SPECTRUM = 7
MODE_COMPLEX_VIEW = 8
MODE_INIT_COMPLEX_IMAGE = 9

#: 频谱的两种"表示"（见 FFTFilter / InverseFFTFilter 的 ``view`` 参数）
#: - ``display``：fftshift + log 幅度，给人看的（**不可还原**：幅度是 log 压缩过的、
#:   相位根本没存）
#: - ``complex``：R = 实部 / G = 虚部（除以 W·H 归一化 + fftshift 居中），
#:   **可编辑、可还原**（单通道 = 亮度；彩色精确往返用 Spectrum2D）
VIEW_DISPLAY = "display"
VIEW_COMPLEX = "complex"
#: 逆变换的输入表示：``real`` = 单张实值频谱（.rgb 是实部、虚部 0）
VIEW_REAL = "real"


def isPowerOfTwo(value: int) -> bool:
    """``value`` 是不是 2 的幂（1 也算）。"""
    return value > 0 and (value & (value - 1)) == 0


def potSize(width: int, height: int) -> tuple:
    """把尺寸规整成"每个维度都是 2 的幂"（位反转的硬要求）。

    **向下**取最近的 2 的幂 —— 不向上：1711×1048 向上会变成 2048×2048，
    六张 RGBA32F 缓冲要 400 MB；向下是 1024×1024（~100 MB）。

    相机输出/窗口尺寸几乎从来不是 2 的幂，所以 :class:`FFTFilter` /
    :class:`InverseFFTFilter` 内部都先缩到规整尺寸、算完**再缩回调用方的目标
    尺寸**：画面上看不出比例变化（只是域内重采样一次），也不用调用方去凑尺寸。
    """
    def floorPowerOfTwo(value: int) -> int:
        value = max(2, int(value))
        result = 1
        while result * 2 <= value:
            result *= 2
        return result
    return (floorPowerOfTwo(width), floorPowerOfTwo(height))


def checkFFTSize(width: int, height: int) -> None:
    """位反转要求每个维度的长度都是 2 的幂，不满足就当场报错。

    静默算出错误结果（或者越界写）是最难查的一类问题。
    """
    if not (isPowerOfTwo(int(width)) and isPowerOfTwo(int(height))):
        raise ValueError(f"FFT 的尺寸必须是 2 的幂（收到 {int(width)}×{int(height)}）")


def _checkView(value, allowed, owner: str) -> str:
    value = str(value).lower()
    if value not in allowed:
        raise ValueError(f"{owner} 不支持 view={value!r}，可选：{sorted(allowed)}")
    return value


class Spectrum2D:
    """复频谱快照：两张 RGBA32F 纹理（``.rgb`` = R/G/B 通道的实部 / 虚部）。

    - :attr:`size` / :attr:`width` / :attr:`height`
    - :meth:`getData` / :meth:`setData`：CPU 侧读改（**全尺寸读回是同步点**，
      调试/离线处理用，别每帧做）
    - :meth:`release`：显式释放；不调用的话随对象回收（``__del__`` 里释放）
    """

    def __init__(self, size, real_texture, imag_texture, internal_format=None) -> None:
        self.size = (int(size[0]), int(size[1]))
        self.real_texture = real_texture
        self.imag_texture = imag_texture
        #: 复频谱纹理的格式（edit() 分配中转纹理用；默认跟实部那张一致）
        self.internal_format = (internal_format
                                or getattr(real_texture, "internal_format",
                                           GL_RGBA32F))
        self._scratch = None

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    def getData(self):
        """读回 ``(实部, 虚部)``，各是 ``(h, w, 3)`` 的 float32 数组。

        行序与 GL 一致（第 0 行是 uv 里 v 最小的那一行），和 ``numpy.fft.fft2``
        的输出可以直接对齐比较。
        """
        real = self._download(self.real_texture)[:, :, :3].copy()
        imag = self._download(self.imag_texture)[:, :, :3].copy()
        return real, imag

    def _download(self, texture) -> np.ndarray:
        """``glGetTexImage`` 直接读进 numpy。

        不用 ``TextureBase.getData``：那条路按 ctypes 数组申请缓冲，
        而某些 PyOpenGL 版本在 handler 匹配时会对 ctypes 数组抛
        ``AttributeError: __module__``（引擎自己的 AsyncFrameGrabber 也是
        因为同样的原因改用 numpy 数组的）。
        """
        width, height = self.size
        buffer = np.empty((height, width, 4), dtype=np.float32)
        glBindTexture(GL_TEXTURE_2D, texture.id)
        glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_FLOAT, buffer)
        glBindTexture(GL_TEXTURE_2D, 0)
        return buffer

    def edit(self, filler, temp=None) -> "Spectrum2D":
        """对**实部 / 虚部各跑一遍**滤镜（频域掩码、模糊这类"两者同样处理"的编辑）。

        - ``filler`` 是任何 :class:`~pyglgame.filler.FillerBase.FillerBase`
          子类（例如 :class:`~pyglgame.filler.FrequencyFilters.FrequencyMaskFilter`）；
        - 结束后 ``real_texture`` / ``imag_texture`` 会指向**新**纹理
          （中转纹理与旧纹理对调），后续 ``getData/setData`` 照常可用；
        - 实部与虚部乘同一个系数 = 只改幅度、不改相位（常规频域滤波）；
          只改其中一张则是**故意破坏**共轭对称性（隐写/特效常这么干）。
        """
        scratch = temp if temp is not None else self._ensureScratch()
        filler.filler(self.real_texture, scratch)
        self.real_texture, scratch = scratch, self.real_texture
        filler.filler(self.imag_texture, scratch)
        self.imag_texture, scratch = scratch, self.imag_texture
        if temp is None:
            self._scratch = scratch
        return self

    def _ensureScratch(self) -> TextureStorage2D:
        """edit() 用的中转纹理（尺寸/格式跟频谱一致，懒分配）。"""
        scratch = self._scratch
        if scratch is None or scratch.released \
                or (scratch.w, scratch.h) != self.size:
            if scratch is not None and not scratch.released:
                scratch.release()
            scratch = TextureStorage2D(self.size[0], self.size[1], unit=INPUT_UNIT,
                                       internal_format=self.internal_format)
            scratch.useTextureParameteri(GL_NEAREST)
            self._scratch = scratch
        return scratch

    def setData(self, real, imag=None) -> None:
        """把 CPU 侧的（实部, 虚部）写回 GPU；``imag=None`` 表示虚部全 0。"""
        width, height = self.size
        self._upload(self.real_texture, self._pack(real, width, height))
        self._upload(self.imag_texture, self._pack(imag, width, height))

    @staticmethod
    def _pack(values, width: int, height: int) -> np.ndarray:
        data = np.zeros((height, width, 4), dtype=np.float32)
        data[:, :, 3] = 1.0
        if values is None:
            return data
        array = np.asarray(values, dtype=np.float32)
        if array.shape != (height, width, 3):
            raise ValueError(
                f"频谱数据形状应为 {(height, width, 3)}，收到 {array.shape}")
        data[:, :, :3] = array
        return np.ascontiguousarray(data)

    @staticmethod
    def _upload(texture, data: np.ndarray) -> None:
        glBindTexture(GL_TEXTURE_2D, texture.id)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4)
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, data.shape[1], data.shape[0],
                        GL_RGBA, GL_FLOAT, data)
        glBindTexture(GL_TEXTURE_2D, 0)

    def release(self) -> None:
        """释放两张频谱纹理（+ edit() 的中转纹理；需要在 GL 上下文里调用）。"""
        for texture in (self.real_texture, self.imag_texture, self._scratch):
            if texture is not None and not texture.released:
                texture.release()
        self._scratch = None

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


class _FFTBase(FillerBase):
    """FFT 家族的共同部分：work / output 缓冲、蝶形派发、拷贝落地。

    它没有 fragment 程序（``SHADER = None``）：计算在 compute shader 里，
    只有"把结果落到目标纹理"那一步用 :class:`~pyglgame.filler.CopyFilter`。
    """

    def __init__(self, internal_format: int = GL_RGBA32F) -> None:
        super().__init__(shader=None)
        self.internal_format = internal_format
        #: 当前缓冲对应的尺寸 (w, h)
        self.size = None
        self.work = None
        self.output = None
        self.spectrum = None
        #: 把输入缩到 2 的幂时用的缓冲（输入已经是 2 的幂就不建）
        self._input = None
        self._copy = CopyFilter()

    # === compute 程序（懒加载：不用 FFT 的项目不会编译它）===

    @property
    def compute(self):
        render_global = RenderGlobal.instance
        if render_global is None:
            raise RuntimeError(
                "FFT 滤镜要在引擎启动后使用（RenderGlobal.instance 还是空的）")
        return render_global.fft_shader

    # === 缓冲 ===

    def _allocate(self, width: int, height: int, unit: int) -> TextureStorage2D:
        texture = TextureStorage2D(width, height, unit=unit,
                                   internal_format=self.internal_format)
        # 蝶形/快照都是"逐 texel 精确对应"的读写，必须 NEAREST
        texture.useTextureParameteri(GL_NEAREST)
        return texture

    def _ensureBuffers(self, width: int, height: int) -> None:
        checkFFTSize(width, height)
        if self.work is not None and self.size == (width, height):
            return
        # 尺寸变了：旧缓冲丢掉。频谱快照的释放交给它自己的 release/__del__
        # （调用方可能还持有引用，例如拿去给 InverseFFTFilter 用）
        self._releaseTextures()
        self.size = (width, height)
        self.work = tuple(self._allocate(width, height, unit) for unit in WORK_UNITS)
        self.output = self._allocate(width, height, OUTPUT_UNIT)
        # 输出图是要"看"的：放大显示时双线性更舒服
        self.output.useTextureParameteri(GL_LINEAR)

    def _ensureInput(self, width: int, height: int) -> TextureStorage2D:
        """"输入不是 2 的幂时"的缩放落点（算完会用 CopyFilter 缩回目标尺寸）。"""
        texture = self._input
        if texture is not None and not texture.released \
                and (texture.w, texture.h) == (width, height):
            return texture
        if texture is not None and not texture.released:
            texture.release()
        texture = TextureStorage2D(width, height, unit=INPUT_UNIT,
                                   internal_format=self.internal_format)
        texture.useTextureParameteri(GL_LINEAR)
        self._input = texture
        return texture

    def _releaseTextures(self) -> None:
        for texture in tuple(self.work or ()):
            if texture is not None and not texture.released:
                texture.release()
        self.work = None
        if self.output is not None and not self.output.released:
            self.output.release()
        self.output = None
        if self._input is not None and not self._input.released:
            self._input.release()
        self._input = None
        self.size = None

    # === 派发 ===

    @staticmethod
    def _groups(count) -> int:
        return max(1, (int(count) + 15) // 16)

    @staticmethod
    def _bindTexture(shader, texture, unit: int, uniform: str) -> None:
        """绑到纹理单元并同步采样器 uniform（``texture`` 可以是对象或 id）。"""
        glActiveTexture(GL_TEXTURE0 + unit)
        glBindTexture(GL_TEXTURE_2D, textureId(texture))
        shader.uniform1i(uniform, unit)

    def _uploadCompute(self, shader, width: int, height: int, fallback=None) -> None:
        """启用程序、绑 image unit、上传尺寸与子类的额外 uniform。"""
        shader.use()
        for texture, unit in zip(self.work, WORK_UNITS):
            texture.bindUnit(unit)
        self.output.bindUnit(OUTPUT_UNIT)
        shader.uniform2i("imageSize", width, height)
        if fallback is not None:
            # 采样器必须绑一张有效纹理：mode 4 之外它们不会被读，但"没绑就采样"
            # 是未定义行为（有的驱动直接给 GL_INVALID_OPERATION）
            self._bindTexture(shader, fallback, 1, "spectrumRealTex")
            self._bindTexture(shader, fallback, 2, "spectrumImagTex")
        self.uploadComputeUniforms(shader, width, height)

    def uploadComputeUniforms(self, shader, width: int, height: int) -> None:
        """compute 的额外 uniform（子类补）。"""

    def _dispatch(self, shader, mode: int, count_x, count_y) -> None:
        shader.uniform1i("mode", mode)
        shader.dispatch(self._groups(count_x), self._groups(count_y), 1)
        # 同一张 work 缓冲"先读后写"，全局内存屏障不能省
        shader.memoryBarrier()

    def _butterflies(self, shader, width: int, height: int, inverse: bool) -> None:
        """横向 ``log2(w)`` 趟 + 纵向 ``log2(h)`` 趟，每趟一个内存屏障。

        必须**按维度成组**跑：蝶形的 span 由 ``fftStep`` 决定，两个方向各自
        从 0 数起；横纵交替（h0,v0,h1,v1...）读到的不再是上一趟的结果。
        """
        shader.uniform1i("inverseFFT", 1 if inverse else 0)
        for step in range(int(math.log2(width))):
            shader.uniform1i("fftStep", step)
            self._dispatch(shader, MODE_FFT_HORIZONTAL, width // 2, height)
        for step in range(int(math.log2(height))):
            shader.uniform1i("fftStep", step)
            self._dispatch(shader, MODE_FFT_VERTICAL, width, height // 2)

    def _copyTo(self, output_texture):
        """compute 写的是 image unit；用拷贝滤镜落到调用方给的目标纹理。"""
        self._copy.filler(self.output, output_texture)
        return output_texture

    def release(self) -> None:
        super().release()
        self._releaseTextures()
        if self._copy is not None:
            self._copy.release()
            self._copy = None


class FFTFilter(_FFTBase):
    """正变换（离散傅里叶变换）。

    - ``filler(src, dst)``：把 ``src`` 变换到频率域，``dst`` 得到频谱：
      ``view="display"``（默认）是 **fftshift + log 幅度的频谱图**（给人看的，
      **不可还原**）；``view="complex"`` 是 **复频谱图**（R = 实部、G = 虚部、
      除以 W·H 归一化、fftshift 居中）—— 可以**当图片直接改**，
      改完交给 ``InverseFFTFilter(view="complex")`` 就能还原（单通道 = 亮度）；
    - 输入尺寸**不必是 2 的幂**：内部按 :func:`potSize` 先缩到规整尺寸、算完
      再缩回目标纹理尺寸（相机的输出尺寸几乎总是奇数/非 2 的幂）；
    - :attr:`spectrum`：复频谱快照（:class:`Spectrum2D`，``keep_spectrum=True``
      时才有），可以交给 :class:`InverseFFTFilter` 做往返，或在 CPU 侧改；
      挂在相机滤镜链里时会**自动交接**给后面的逆变换（见 :meth:`provideSpectrum`）；
    - ``gain`` / ``log_base`` 只影响**频谱图的显示**，不影响频谱数据本身。
    """

    def __init__(self, gain: float = 1.0, log_base: float = None,
                 keep_spectrum: bool = True, internal_format: int = GL_RGBA32F,
                 view: str = VIEW_DISPLAY) -> None:
        super().__init__(internal_format=internal_format)
        self.gain = float(gain)
        #: 频谱图的 log 归一化基数；None = ``log2(w*h)``（直流分量落在 ~0.65 灰）
        self.log_base = log_base
        self.keep_spectrum = bool(keep_spectrum)
        #: ``display`` = 给人看的频谱图（不可还原）/ ``complex`` = 可编辑可还原的复频谱图
        self.view = _checkView(view, (VIEW_DISPLAY, VIEW_COMPLEX), "FFTFilter")

    def _ensureBuffers(self, width: int, height: int) -> None:
        super()._ensureBuffers(width, height)
        if not self.keep_spectrum:
            self.spectrum = None
            return
        if self.spectrum is None or self.spectrum.size != (width, height):
            self.spectrum = Spectrum2D(
                (width, height),
                *[self._allocate(width, height, unit) for unit in SPECTRUM_UNITS])

    def uploadComputeUniforms(self, shader, width: int, height: int) -> None:
        shader.uniform1f("spectrumGain", self.gain)
        base = self.log_base
        shader.uniform1f("spectrumLogBase",
                         float(base) if base else math.log2(float(width * height)))

    def filler(self, input_texture, output_texture):
        source_size = textureSize(input_texture)
        width, height = potSize(*source_size)
        self._ensureBuffers(width, height)
        # 输入不是 2 的幂（相机/窗口尺寸几乎总是这样）：先缩到规整尺寸，
        # 算完由 _copyTo 缩回**调用方目标纹理**的尺寸 —— 比例看不出来
        source = input_texture
        if source_size != (width, height):
            source = self._ensureInput(width, height)
            self._copy.filler(input_texture, source)
        shader = self.compute
        self._uploadCompute(shader, width, height, fallback=source)
        # 1) 空间域图像 → work 缓冲（位反转序）
        self._bindTexture(shader, source, 0, "inputImage")
        self._dispatch(shader, MODE_INIT_IMAGE, width, height)
        # 2) 蝶形（横向 + 纵向）
        self._butterflies(shader, width, height, inverse=False)
        # 3) 复频谱快照（供逆变换 / getData / 链上的交接使用）
        if self.spectrum is not None:
            self._dispatch(shader, MODE_STORE_SPECTRUM, width, height)
        # 4) 频谱 → 目标纹理（display = 给人看 / complex = 可编辑可还原）
        self._dispatch(shader, MODE_COMPLEX_VIEW if self.view == VIEW_COMPLEX
                       else MODE_SPECTRUM_VIEW, width, height)
        return self._copyTo(output_texture)

    def provideSpectrum(self):
        """把刚算出来的复频谱交给链上的下一个滤镜（见 FilterChain.apply）。

        于是 ``camera.setFilters(FFTFilter(), InverseFFTFilter())`` 就是一次
        **真正的往返**（还原画面），不需要手动传 ``spectrum=``。
        """
        return self.spectrum


class InverseFFTFilter(_FFTBase):
    """逆变换。

    四种用法（优先级从高到低）：

    1. ``filler(src, dst, spectrum=...)`` —— 临时指定复频谱；
    2. ``InverseFFTFilter(spectrum=...).filler(src, dst)`` —— 构造时指定；
    3. ``InverseFFTFilter(view="complex").filler(src, dst)`` —— 把 ``src`` 当
       **复频谱图**（R = 实部、G = 虚部、fftshift 居中、除以 W·H 归一化）读；
       配合 ``FFTFilter(view="complex")`` 就是"频谱当图片改完再还原"；
    4. ``InverseFFTFilter().filler(src, dst)`` —— 把 ``src`` 当成**实值频谱**
       （``.rgb`` = 实部、虚部 0）做逆变换。

    另外：**挂在相机滤镜链里时**（``camera.setFilters(FFTFilter(), InverseFFTFilter())``），
    链会把前一个 :class:`FFTFilter` 刚算出的复频谱自动交接过来（见
    :meth:`acceptSpectrum`），也就是"FFT → 逆 FFT = 还原画面"。
    尺寸不是 2 的幂也没关系：内部会先规整（见 :func:`potSize`），
    结果再缩回目标纹理尺寸。

    输出的 alpha 恒为 1（频率域里没有 alpha 的信息）。
    """

    def __init__(self, spectrum: Spectrum2D = None,
                 internal_format: int = GL_RGBA32F, view: str = VIEW_REAL) -> None:
        super().__init__(internal_format=internal_format)
        self.spectrum = spectrum
        #: 链上前一个 FFTFilter 交接过来的复频谱（用完即弃，避免下一帧拿过期数据）
        self._chain_spectrum = None
        #: ``real`` = 输入是实值频谱 / ``complex`` = 输入是复频谱图（R/G = 实/虚部）
        self.view = _checkView(view, (VIEW_REAL, VIEW_COMPLEX), "InverseFFTFilter")

    def acceptSpectrum(self, spectrum) -> None:
        """接收链上前一个滤镜产出的复频谱（由 :meth:`FilterChain.apply` 调用）。"""
        self._chain_spectrum = spectrum

    def filler(self, input_texture, output_texture, spectrum: Spectrum2D = None):
        # 优先级：调用参数 > 构造时给的 >（view="complex" 时读输入纹理）> 链上交接的
        spectrum = spectrum if spectrum is not None else self.spectrum
        if spectrum is None and self.view != VIEW_COMPLEX:
            spectrum = self._chain_spectrum
        self._chain_spectrum = None
        source_size = textureSize(input_texture)
        width, height = potSize(*source_size)
        if spectrum is not None and tuple(spectrum.size) != (width, height):
            raise ValueError(
                f"频谱是 {spectrum.size[0]}×{spectrum.size[1]}，而输入纹理 "
                f"{source_size[0]}×{source_size[1]} 对应的 FFT 尺寸是 "
                f"{width}×{height}：两者必须来自同一个尺寸")
        self._ensureBuffers(width, height)
        shader = self.compute
        source = input_texture
        if spectrum is None and source_size != (width, height):
            # 只有"把输入当实值频谱"时才需要那份缩放后的副本
            source = self._ensureInput(width, height)
            self._copy.filler(input_texture, source)
        self._uploadCompute(shader, width, height, fallback=source)
        if spectrum is not None:
            # 复频谱快照：实部 / 虚部各一张 .rgb 纹理 → 位反转序
            self._bindTexture(shader, spectrum.real_texture, 1, "spectrumRealTex")
            self._bindTexture(shader, spectrum.imag_texture, 2, "spectrumImagTex")
            self._dispatch(shader, MODE_INIT_SPECTRUM, width, height)
        elif self.view == VIEW_COMPLEX:
            # 把输入当**复频谱图**：R = 实部 / G = 虚部（fftshift 居中、归一化）
            self._bindTexture(shader, source, 0, "inputImage")
            self._dispatch(shader, MODE_INIT_COMPLEX_IMAGE, width, height)
        else:
            # 把输入当实值频谱（虚部 0）→ 位反转序
            self._bindTexture(shader, source, 0, "inputImage")
            self._dispatch(shader, MODE_INIT_REAL_SPECTRUM, width, height)
        self._butterflies(shader, width, height, inverse=True)
        self._dispatch(shader, MODE_INVERSE_VIEW, width, height)
        return self._copyTo(output_texture)
