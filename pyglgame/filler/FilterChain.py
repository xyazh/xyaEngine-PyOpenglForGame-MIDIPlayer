"""滤镜链：把若干个滤镜依次挂在**相机最终输出**上。

.. code-block:: python

    from pyglgame.filler import EdgeFilter, GrayscaleFilter

    camera.addFilter(GrayscaleFilter())          # 先黑白
    camera.addFilter(EdgeFilter("sobel"))        # 再在灰度结果上求边缘

    camera.setFilters(PixelateFilter(block=6), InvertFilter())   # 一次替换整条链
    camera.clearFilters()                        # 摘掉全部
    camera.useFilters(False)                     # 暂时不跑（对象仍挂在相机上）

相机侧只做两件事：``renderEnd()`` 里把本帧最终颜色交给链跑一遍，
``_bindWindowTexture()`` 采样链的结果。所以"像素级后处理"和渲染管线其余部分
（阴影 / AO / WBOIT / Bloom / 体积散射）完全解耦。

链自己的实现要点：

1. **0 个滤镜 = 完全透明**：直接返回输入，不建纹理、不多跑一次 pass ——
   没挂滤镜的相机与加这套之前逐像素一致（老 demo 不受影响）；
2. 中间结果用两张 **ping-pong 纹理**轮着写（第 1 个滤镜写 A、第 2 个写 B、
   第 3 个又写 A……），因此**第 N 个滤镜的输出就是链的最终结果**，
   不需要额外的拷贝 pass（3 个滤镜 = 3 趟，而不是 4 趟）；
3. 中间纹理按**输入尺寸**分配，尺寸变了自动重建（窗口缩放）；
4. 链**不会写输入纹理**（那是相机自己的帧缓冲 / Bloom 结果），
   所以下一帧重新渲染不会互相污染；
5. 纹理格式默认 ``GL_RGBA16F``（HDR 值不会在中间步骤被夹到 1），
   通常直接传相机的 ``internal_format``。
"""

from OpenGL.GL import *

from ..render.TextureStorage2D import TextureStorage2D
from .FillerBase import FillerBase, textureSize

#: 中间纹理占用的 image unit。纯属"TextureStorage2D 构造时会绑一个 image unit"
#: 的副作用：6/7 是引擎里 Bloom(0..6)/FFT(1..6)/噪声(7) 都会在 dispatch 前
#: 重新绑定的单元，不会真的互相踩。
CHAIN_UNITS = (6, 7)

#: 链的长度上限（只是防呆：滤镜链越长越贵，真要几十个请自己组合成一张纹理）
MAX_FILTERS = 16


class FilterChain:
    """按加入顺序执行的滤镜链（输入/输出都是纹理）。"""

    def __init__(self, internal_format: int = GL_RGBA16F) -> None:
        self.internal_format = internal_format
        #: 加入顺序 = 执行顺序
        self.filters: list = []
        #: ping-pong 中间纹理（懒分配：真的跑滤镜时才建）
        self.textures: list = []
        self._size = None

    # === 组装 ===

    def add(self, filler: FillerBase) -> FillerBase:
        """追加一个滤镜（返回这个滤镜本身，方便 ``gray = chain.add(GrayscaleFilter())``）。"""
        return self.insert(len(self.filters), filler)

    def insert(self, index: int, filler: FillerBase) -> FillerBase:
        """插到指定位置（顺序 = 执行顺序：先插的滤镜先跑）。"""
        if not isinstance(filler, FillerBase):
            raise TypeError(
                f"滤镜链只接受 FillerBase 子类，收到 {type(filler).__name__}")
        if len(self.filters) >= MAX_FILTERS:
            raise ValueError(
                f"滤镜链最多 {MAX_FILTERS} 个滤镜（现在 {len(self.filters)} 个）："
                "再多请把几级合成中间结果复用")
        index = max(0, min(int(index), len(self.filters)))
        self.filters.insert(index, filler)
        return filler

    def remove(self, filler: FillerBase) -> bool:
        """摘掉某个滤镜；不在链里则返回 False（不抛）。"""
        for index, current in enumerate(self.filters):
            if current is filler:
                del self.filters[index]
                return True
        return False

    def clear(self) -> None:
        """摘掉全部滤镜（中间纹理留着：下次挂上就不用重建）。"""
        self.filters.clear()

    def __len__(self) -> int:
        return len(self.filters)

    def __iter__(self):
        return iter(self.filters)

    def __bool__(self) -> bool:
        return bool(self.filters)

    def __repr__(self) -> str:
        names = ", ".join(type(filler).__name__ for filler in self.filters)
        return f"FilterChain({names})"

    # === 执行 ===

    def apply(self, input_texture):
        """按顺序跑一遍，返回结果纹理。

        - 链是空的：返回 ``input_texture`` 本身（不做任何 GL 调用）
        - 否则返回链自己的一张中间纹理（**不是**输入）

        **频谱交接**（鸭子类型，本模块不依赖具体滤镜类）：某个滤镜产出
        ``provideSpectrum()`` 时，就把它的结果交给**后面第一个**
        ``acceptSpectrum(...)`` 的滤镜。这样
        ``chain.add(FFTFilter()).add(InverseFFTFilter())`` 是一次真正的往返
        （逆变换拿到复频谱才能还原画面，否则只能把输入当实值频谱）。
        """
        if not self.filters:
            return input_texture
        width, height = textureSize(input_texture)
        self._ensureTextures(width, height)
        source = input_texture
        spectrum = None
        for index, filler in enumerate(self.filters):
            if spectrum is not None and hasattr(filler, "acceptSpectrum"):
                filler.acceptSpectrum(spectrum)
            target = self.textures[index % len(self.textures)]
            filler.filler(source, target)
            if hasattr(filler, "provideSpectrum"):
                spectrum = filler.provideSpectrum()
            source = target
        return source

    # === 纹理 ===

    def _ensureTextures(self, width: int, height: int) -> None:
        if (self._size == (width, height) and len(self.textures) == len(CHAIN_UNITS)
                and all(not texture.released for texture in self.textures)):
            return
        self._releaseTextures()
        self._size = (width, height)
        for unit in CHAIN_UNITS:
            texture = TextureStorage2D(width, height, unit=unit,
                                       internal_format=self.internal_format)
            # 链的结果会被窗口 pass（以及下一个滤镜）放大/缩小采样，双线性
            texture.useTextureParameteri(GL_LINEAR)
            self.textures.append(texture)

    def resize(self, width: int, height: int) -> bool:
        """窗口尺寸变化时调用：丢掉旧纹理，下次 ``apply`` 按新尺寸重建。"""
        size = (int(width), int(height))
        if self._size == size:
            return False
        self._releaseTextures()
        # 记住目标尺寸：重复调用不再重复"丢"（丢过了就是丢过了）
        self._size = size
        return True

    def _releaseTextures(self) -> None:
        for texture in self.textures:
            if texture is not None and not texture.released:
                texture.release()
        self.textures = []
        self._size = None

    def release(self) -> None:
        """释放中间纹理（滤镜对象本身由调用方持有，这里不动它们）。"""
        self._releaseTextures()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
