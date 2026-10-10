"""文字排版 → 贴图（PIL 画字 + 上传，带缓存）。

引擎里的文字其实只做一件事：**把一段字符串画成一张 RGBA 贴图**
（:class:`~pyglgame.render.Texture.Texture`），再由上层决定怎么摆 —— 见
:class:`~pyglgame.gameobject.Text.Text`（屏幕空间的 HUD / UI 文字）。分工很清楚：

============================  ==================================================
本模块                         ``gameobject.Text``
============================  ==================================================
**排版 + 画 + 上传**：字体、多行、行距、   **摆**：像素坐标、锚点、缩放、旋转、NDC
对齐、描边、外边距、贴图缓存       换算、渲染状态（深度 / 混合 / pass）
============================  ==================================================

"不碰 GL 的那一半"单独拆成 :func:`layoutText` / :meth:`TextLoader.measure`：组字与
量宽高是纯 CPU 的（没有窗口、没有 GL 上下文也能算"这个标题占多大"）。
``glTexImage2D`` 只在 :meth:`TextLoader.createTexture` 里发生一次，之后同款文字
直接吃缓存。

字体（:func:`loadFont`）
------------------------

- ``None`` → PIL 自带的可缩放字体（零素材能显示，但**只有拉丁字符集**）；
- **真实文件路径**（``C:/Windows/Fonts/msyh.ttc``）→ 直接交给 PIL；
- **逻辑路径**（``res/font/x.ttf``）→ 走 :func:`pyglgame.resources.readBytes`，
  所以封成 ``res.xypack`` 之后照样读得到；
- file-like / 已是 PIL 字体对象 → 原样使用。

中文 / 日文之类要自带字体文件，PIL 自带的 Aileron 没有 CJK 字形。
"""

from io import BytesIO
import os

from OpenGL.GL import GL_CLAMP_TO_EDGE
from PIL import Image, ImageDraw, ImageFont

from .. import resources
from .Image import Image as GLImage
from .Texture import Texture


def loadFont(font=None, font_size: int = 24) -> "ImageFont.ImageFont":
    """按 ``font`` 建一个 PIL 字体对象（**纯 CPU**，不需要 GL 上下文）。见模块文档。"""
    size = int(font_size)
    if size <= 0:
        raise ValueError("font_size 必须大于 0，收到 %r" % (font_size,))
    if font is None or (isinstance(font, str) and not font.strip()):
        try:
            return ImageFont.load_default(size)
        except TypeError:               # 老 Pillow：load_default() 只有位图字体
            return ImageFont.load_default()
    if hasattr(font, "getbbox"):        # 已经是现成的字体对象
        return font
    if isinstance(font, (str, bytes, os.PathLike)) and not hasattr(font, "read"):
        path = os.fsdecode(font)
        if os.path.isfile(path):
            # 真实文件：交给 PIL 自己映射（系统字体动辄十几 MB，没必要读进内存）
            return ImageFont.truetype(path, size)
        data = resources.readBytes(path)
        if data:
            # 逻辑路径（``res/font/x.ttf``）：资源包里的字体也要能读
            return ImageFont.truetype(BytesIO(data), size)
        # 两边都没读到：还是交给 PIL 抛（"字体找不到"要早点暴露，别静默画空白）
        return ImageFont.truetype(path, size)
    return ImageFont.truetype(font, size)


def fontKey(font) -> tuple:
    """字体对象的**缓存键**（同键 = 同一个字体，可以共用一个 :class:`TextLoader`）。

    与 :func:`loadFont` 的入参一一对应：内置字体 / 路径 / 现成字体对象 / file-like。
    """
    if font is None or (isinstance(font, str) and not font.strip()):
        return ("default",)
    if isinstance(font, (str, bytes, os.PathLike)) and not hasattr(font, "read"):
        return ("path", os.fsdecode(font))
    if hasattr(font, "getbbox"):
        return ("object", getattr(font, "path", None), getattr(font, "size", None),
                id(font))
    return ("stream", id(font))


def normalizeAlign(align) -> str:
    """``align`` 规范化：大小写 / 空白不敏感；不认识的值直接抛 :class:`ValueError`。

    ``Pillow`` 只在**真画字**时才校验 ``align``（量尺寸那一步不一定管），而"量出来多大"
    与"画出来多大"必须是同一套规则 —— 所以这里自己校验一次，两边共用。
    """
    value = str(align if align is not None else "left").strip().lower() or "left"
    if value not in ("left", "center", "right", "justify"):
        raise ValueError("align 只能是 left / center / right / justify，收到 %r" % (align,))
    return value


def layoutText(font, text, padding: int = 4, line_spacing: int = 4,
               align: str = "left", stroke_width: int = 0) -> tuple:
    """算出"这段文字要占多大的贴图、从哪开始画"（**纯 CPU**，所以能单测、能离线布局）。

    返回 ``(width, height, offset)``：

    - ``width`` / ``height``：贴图的像素尺寸，**含四周的 ``padding``**；空文字（或只有
      空白字符）返回 ``(0, 0, offset)`` —— 调用方据此跳过绘制；
    - ``offset``：画字的起点（``ImageDraw.multiline_text`` 的 ``xy``）—— 已经补过
      ``padding`` 与 bbox 的偏移，所以字形一定落在贴图里面（``align`` 居中的行会
      有负的 bbox 起点，也在这一步被拉回来）。

    ``\\n`` 分行；``line_spacing`` 是**额外**行距（像素，Pillow 的 ``spacing``）；
    ``align``：``left`` / ``center`` / ``right`` / ``justify``（只有多行才有区别）；
    ``stroke_width``：描边宽度（描边会算进尺寸里，不会被切掉）。
    """
    text = "" if text is None else str(text)
    padding = max(int(padding), 0)
    spacing = max(int(line_spacing), 0)
    stroke_width = max(int(stroke_width), 0)
    align = normalizeAlign(align)           # 量尺寸 / 画字共用同一套校验
    offset = (float(padding), float(padding))
    if not text.strip():
        return (0, 0, offset)
    # 量 bbox 用的临时画布：只为了拿字体度量，1×1 就够
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = probe.multiline_textbbox((0.0, 0.0), text, font=font, spacing=spacing,
                                   align=align, stroke_width=stroke_width)
    width = int(round(bbox[2] - bbox[0]))
    height = int(round(bbox[3] - bbox[1]))
    if width <= 0 or height <= 0:
        return (0, 0, offset)
    return (width + padding * 2, height + padding * 2,
            (padding - bbox[0], padding - bbox[1]))


class TextLoader:
    """把文字画到 PIL 图像上再上传为纹理（见模块文档）。

    - :meth:`measure` **只算尺寸**（纯 CPU，不建纹理、不碰 GL）；
    - :meth:`createTexture` 才真的画 + 上传，并按"文字 + 全部样式"缓存
      （``cache=False`` 关掉缓存）；
    - 缓存里的贴图由本对象持有，:meth:`clearCache` 统一释放。

    贴图采样用 ``GL_CLAMP_TO_EDGE``：文字四边是空的（``padding``），用引擎默认的
    ``GL_REPEAT`` 会在贴图边缘把对面的像素拉进来（细边发虚）。
    """

    def __init__(self, font_path=None, font_size: int = 24, cache: bool = True) -> None:
        self.font_path = font_path
        self.font_size = int(font_size)
        self.font = loadFont(font_path, self.font_size)
        self.cache = cache
        self._texture_cache: dict[tuple, Texture] = {}

    # === 缓存键 / 量尺寸 / 画 ===

    def key(self, text, color=(255, 255, 255, 255), bg_color=(0, 0, 0, 0),
            padding: int = 4, line_spacing: int = 4, align: str = "left",
            stroke_width: int = 0, stroke_fill=None) -> tuple:
        """缓存键：**只要有一个像素会变就必须进键**（字体与字号由 loader 实例保证）。"""
        return (("" if text is None else str(text)), tuple(color), tuple(bg_color),
                max(int(padding), 0), max(int(line_spacing), 0),
                normalizeAlign(align), max(int(stroke_width), 0),
                None if stroke_fill is None else tuple(stroke_fill))

    def measure(self, text, padding: int = 4, line_spacing: int = 4,
                align: str = "left", stroke_width: int = 0) -> tuple:
        """文字贴图的像素尺寸 ``(width, height)``（**不碰 GL**，空文字 = ``(0, 0)``）。"""
        width, height, _ = layoutText(self.font, text, padding=padding,
                                     line_spacing=line_spacing, align=align,
                                     stroke_width=stroke_width)
        return (width, height)

    def createTexture(self, text, color=(255, 255, 255, 255), bg_color=(0, 0, 0, 0),
                      padding: int = 4, line_spacing: int = 4, align: str = "left",
                      stroke_width: int = 0, stroke_fill=None) -> "Texture | None":
        """把文字画成贴图并上传（**需要 GL 上下文**；空文字返回 ``None``）。

        - 贴图尺寸 = :meth:`measure` 的结果（两者共用 :func:`layoutText`，不会打架），
          尺寸也记在 ``texture.w`` / ``texture.h`` 上；
        - ``padding``：四周留白（给背景色留边、也让字形不贴边）；
        - ``color`` / ``bg_color`` / ``stroke_fill``：**PIL 的 0-255 RGBA**
          （与 ``Sprite2D.color`` 的 0-1 浮点不是一套，别混）。

        原实现把 ``img.tobytes()``（bytes）当 ``Image`` 传给
        ``Texture.createDataTexture``，会直接 ``AttributeError``；这里用
        :meth:`GLImage.fromPIL`。
        """
        align = normalizeAlign(align)
        cache_key = self.key(text, color, bg_color, padding, line_spacing,
                             align, stroke_width, stroke_fill)
        if self.cache and cache_key in self._texture_cache:
            return self._texture_cache[cache_key]
        raw_text = cache_key[0]
        width, height, offset = layoutText(self.font, raw_text, padding=padding,
                                          line_spacing=line_spacing, align=align,
                                          stroke_width=stroke_width)
        if width <= 0 or height <= 0:
            return None
        image = Image.new("RGBA", (width, height), tuple(bg_color))
        draw = ImageDraw.Draw(image)
        draw.multiline_text(offset, raw_text, fill=tuple(color), font=self.font,
                            spacing=max(int(line_spacing), 0), align=align,
                            stroke_width=max(int(stroke_width), 0),
                            stroke_fill=stroke_fill)
        texture = Texture.createFromImage(GLImage.fromPIL(image),
                                          wrap_mode=GL_CLAMP_TO_EDGE)
        if self.cache:
            self._texture_cache[cache_key] = texture
        return texture

    def clearCache(self) -> None:
        """释放缓存里的全部贴图（**需要在 GL 上下文里调用**）。"""
        for texture in self._texture_cache.values():
            if texture is not None and not texture.released:
                texture.release()
        self._texture_cache.clear()
