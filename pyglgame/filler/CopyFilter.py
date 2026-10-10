"""拷贝滤镜：把一张纹理写到另一张纹理上（可以改尺寸/格式）。

看着"没用"，其实是最常用的工具之一：

- HDR 缓冲 → LDR 纹理（目标 RGBA8 时由 GL 负责 clamp）；
- 512² 的结果缩放进 256² 的目标纹理（双线性）；
- **compute shader 产出的结果落地**：FFT 只能写 image unit（格式限定符必须
  与纹理格式严格一致），要写进"调用方给的任意纹理"就得过一遍这个 fragment 通道。
"""

from .FillerBase import FillerBase


class CopyFilter(FillerBase):
    """``tex0`` → 输出附件（尺寸/格式由目标纹理决定）。"""

    SHADER = "filler_copy"

    def __init__(self) -> None:
        super().__init__()
