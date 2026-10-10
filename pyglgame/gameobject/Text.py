"""文字板：把一段文字烘成贴图，贴在**世界空间**的一个四边形上（``Sprite2D`` 的子类）。

它就是一个**普通的 GameObject**：``x`` / ``y`` / ``z`` 摆在世界里，``p`` / ``t`` / ``r``
决定朝向，吃 2D 光照 / 半透明 / 阴影 / Object ID 拾取 —— 和别的对象混在一起放哪儿都行。

.. code-block:: python

    from pyglgame.gameobject.Text import Text

    # 2D：一块招牌（默认就在 XY 平面、面向 +Z，也就是 2D 相机的方向）
    sign = Text("SALE", font_size=64, x=3.0, y=1.5, z=0.5,
                font_color=(255, 220, 0, 255), bg_color=(0, 0, 0, 180),
                pixels_per_unit=32.0)
    sign.setHeight(0.8)                    # 板子 0.8 世界单位高（宽按比例）

    # 3D：立起来的牌子（绕 Y 转 90° = 正面朝 +X）—— 或者干脆永远正对镜头
    board = Text("公告", font_size=48, x=0.0, y=2.0, z=0.0, t=90.0)
    board.billboard = True                 # 每帧按相机朝向重算（广告牌）

    board.setText("公告 2")                 # 换文字：重烘贴图 + 重算世界尺寸
    board.color = (1.0, 0.6, 0.6, 1.0)      # 顶点染色（0-1，和 Sprite2D 一样，不重烘）

场景文档里也一样（``x`` / ``y`` / ``z`` 是**世界坐标**，与其它对象同约定）：

.. code-block:: json

    {"type": "Text", "name": "sign", "text": "SALE", "font_size": 64,
     "x": 3.0, "y": 1.5, "z": 0.5, "pixels_per_unit": 32.0,
     "properties": {"font_color": [255, 220, 0, 255], "bg_color": [0, 0, 0, 180]}}

尺寸（像素 → 世界单位）
----------------------

``pixels_per_unit`` 是引擎里量 2D 世界的通用约定（图集 / 瓦片地图都用它，默认
``32``：1 世界单位 = 32 像素）。所以 ``font_size=32`` 的文字板高约 1 个世界单位。
两个更顺手的入口：

- ``scale``：整体倍率（1 = 按 ``pixels_per_unit`` 算出来的大小）；
- ``height`` / :meth:`Text.setHeight`：直接说"板子多高（世界单位）"，宽度按贴图
  比例自动跟上（改它**不用重烘**）。

朝向与广告牌
------------

- 板的**正面是局部 +Z**（和 ``Sprite2D`` 一致）：``p``(绕 X) / ``t``(绕 Y) /
  ``r``(绕 Z) 就是"把它转过去"，顺序与 :class:`~pyglgame.gameobject.camera.Camera`
  的 ``setCameraPosAndPYR`` 同一套（``t=90`` → 正面朝世界 +X）；
- ⚠ **正面要朝着相机，否则看到的是背面 → 字左右镜像**（看起来像"颠倒"）。
  判断办法：先看这台相机的"屏幕右 / 上"是世界哪个方向（view 矩阵的第 0 / 1 行），
  再让"局部 +X → 屏幕右、局部 +Y → 屏幕上"。例：相机在原点、``t=90``（朝 +X 看）时
  屏幕右 = 世界 **+Z**，所以牌子要用 **``t = -90``**（``t = +90`` 是背对相机 → 镜像）；
- ``billboard = True``：每帧按**当前相机的右 / 上方向**重算四边形（与粒子系统的
  ``QuadRenderer`` 同一套约定），所以它永远正对镜头、**也不可能镜像**；拿不到相机就
  退回 XY 平面 —— 正好是 2D 想要的效果。广告牌模式下 ``p`` / ``t`` 不参与（朝向由相机
  决定），``r`` 仍然作为"在屏幕平面内自转"的角度保留。

GL 的时机（与 ``Sprite2D`` 同一条约定）
---------------------------------------

- ``Text(...)`` / ``setText`` / ``measure()`` **只碰 CPU**：PIL 排版 + 顶点数组 —— 所以能
  进场景文档、能在没有窗口时算好世界尺寸；
- 贴图推迟到**第一帧**（:meth:`Text.bake`）上传，之后只在"像素会变"的参数（文字 /
  字体 / 颜色 / 背景 / 行距 / 对齐 / 描边）变化时重烘；同款文字多个牌子共享一张贴图
  （见 :func:`loaderFor`）；
- 顶点：``set*`` 一调就（立刻）重算；**直接改字段**（``board.x = 3``，引擎里最常见的
  写法）由 :meth:`Text.rebuildIfNeeded` 在渲染时按"签名"发现并补上 —— 广告牌模式下
  相机一动也算签名变化（顶点要跟着重算）。

其它
----

- 贴图是"没翻过"的（``glTexImage2D`` 的第一行数据是 ``v = 0``，即图片顶部），
  所以 :attr:`Text.uv` 用 ``(0, 1, 1, 0)``（下边取 ``v1``）—— 照抄 ``Sprite2D`` 的默认
  ``(0, 0, 1, 1)`` 会把文字画成上下颠倒；
- 光照按"面向 +Z 的平面"算（顶点法线恒为 ``+Z``，与 ``Sprite2D`` 相同）：转过的板子
  接受光照的方向不会跟着转。要"完全不吃光照"就 ``lighting_mode="none"``；
- 一张贴图一段文字（没有 SDF / 字距微调）；长文本按整段烘，注意贴图尺寸。
"""

import math

import glm
from OpenGL.GL import GL_TRIANGLES

from ..render.BufferBuilder import COL, POS, TEX
from ..render.TextLoader import TextLoader, fontKey, normalizeAlign
from ..RenderGlobal import RenderGlobal
from ..ResourceLocation import ResourceLocation
from .Sprite2D import Sprite2D

#: 默认"每世界单位多少像素"（与图集 / 瓦片地图同一约定：1 单位 = 32 像素）
DEFAULT_PIXELS_PER_UNIT = 32.0

#: 拿不到相机时的广告牌基向量 = 世界 XY 平面（2D 场景本来就是这个朝向）
FALLBACK_AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

#: 九个锚点的"相对位置"：``(0, 0)`` = 左上角、``(0.5, 0.5)`` = 中心、``(1, 1)`` = 右下角
_ANCHOR_FACTORS = {
    "topleft": (0.0, 0.0), "top": (0.5, 0.0), "topright": (1.0, 0.0),
    "left": (0.0, 0.5), "center": (0.5, 0.5), "right": (1.0, 0.5),
    "bottomleft": (0.0, 1.0), "bottom": (0.5, 1.0), "bottomright": (1.0, 1.0),
}

#: 锚点别名的容忍度：``tl`` / ``top-left`` / ``TOP_LEFT`` / ``middle`` 都认
_ANCHOR_ALIASES = {
    "tl": "topleft", "tc": "top", "t": "top", "topcenter": "top", "tr": "topright",
    "l": "left", "cl": "left", "leftcenter": "left", "c": "center", "m": "center",
    "middle": "center", "centre": "center", "cr": "right", "rightcenter": "right",
    "r": "right", "bl": "bottomleft", "bc": "bottom", "bottomcenter": "bottom",
    "b": "bottom", "br": "bottomright",
}

#: 共享的字体加载器：``{(字体键, 字号): TextLoader}`` —— 同款文字只烘一次贴图
_LOADERS: dict[tuple, "TextLoader"] = {}


def normalizeAnchor(anchor="center") -> str:
    """锚点名规范化：大小写 / ``-`` / ``_`` / 空格不敏感，还认 ``tl`` / ``middle`` 这类别名。

    不认识的名字直接抛 :class:`ValueError`（早点发现拼错的锚点，总比默默按中心摆好）。
    """
    if anchor is None:
        anchor = "center"
    key = str(anchor).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    key = _ANCHOR_ALIASES.get(key, key)
    if key not in _ANCHOR_FACTORS:
        raise ValueError("不认识的锚点 %r（可用: %s）"
                         % (anchor, "、".join(sorted(_ANCHOR_FACTORS))))
    return key


def anchorFactors(anchor="center") -> tuple:
    """锚点名 → 相对系数 ``(hx, hy)``（``0`` = 左/上边、``1`` = 右/下边），纯函数。"""
    if isinstance(anchor, (tuple, list)):
        return (float(anchor[0]), float(anchor[1]))
    return _ANCHOR_FACTORS[normalizeAnchor(anchor)]


def anchorRect(width: float, height: float, anchor="center") -> tuple:
    """按锚点算**局部**矩形 ``(left, right, bottom, top)``（y 向上，锚点在原点）。

    - ``center``（默认）：关于原点对称 → ``(-w/2, w/2, -h/2, h/2)``；
    - ``topleft``：左上角在原点，往右下张开 → ``(0, w, -h, 0)``；
    - ``bottom``：底边中点在原点 → ``(-w/2, w/2, 0, h)``。

    几何按这个矩形烘进顶点、旋转发生在 ``(x, y, z)`` 上 —— 所以锚点同时也是"转轴"。
    """
    hx, hy = anchorFactors(anchor)
    width = float(width)
    height = float(height)
    return (-width * hx, width * (1.0 - hx), -height * (1.0 - hy), height * hy)


def cameraAxes() -> tuple:
    """当前相机的世界空间 ``(right, up)``，单位向量。

    视图矩阵的旋转部分是 ``R^T``，它的第 0 / 1 **行**就是相机的右 / 上方向 ——
    ``glm`` 的 ``m[列][行]`` 取值下是 ``(m[0][0], m[1][0], m[2][0])`` 与
    ``(m[0][1], m[1][1], m[2][1])``。与
    ``pyglgame.particle.ParticleRenderer.QuadRenderer.cameraAxes()`` 同一套约定
    （这里照抄一遍，免得 ``gameobject`` 反向依赖 ``particle``）；拿不到相机
    （没在相机 pass 里）就退回世界 XY 平面 —— 2D 场景本来就是这个朝向。

    ⚠ 2D 的 **NDC 直通相机**（``MSimple2D`` 默认）也会命中兜底：那种模式下没有
    "世界尺度"，XY 平面就是正确答案。
    """
    camera = getattr(RenderGlobal.instance, "using_camera", None)
    view_of = getattr(camera, "viewMatrix", None)
    if view_of is None:
        return FALLBACK_AXES
    view = view_of()
    if view is None:
        return FALLBACK_AXES
    right = (float(view[0][0]), float(view[1][0]), float(view[2][0]))
    up = (float(view[0][1]), float(view[1][1]), float(view[2][1]))
    if glm.length(glm.vec3(right)) < 1e-6 or glm.length(glm.vec3(up)) < 1e-6:
        return FALLBACK_AXES
    return (right, up)


def billboardAxes(roll: float = 0.0) -> tuple:
    """广告牌要用的 ``(right, up)``：在相机平面里再按 ``roll``（度）自转一下。

    ``roll > 0`` 是**屏幕上的逆时针**（和 :attr:`Text.r` 的手感一致）。
    """
    right, up = cameraAxes()
    angle = math.radians(float(roll))
    if not angle:
        return (right, up)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    rotated_right = tuple(float(right[i]) * cos_a + float(up[i]) * sin_a
                          for i in range(3))
    rotated_up = tuple(float(up[i]) * cos_a - float(right[i]) * sin_a
                       for i in range(3))
    return (rotated_right, rotated_up)


def loaderFor(font=None, font_size: int = 32) -> TextLoader:
    """取（或建）共享的 :class:`~pyglgame.render.TextLoader`（**纯 CPU**）。

    键 = ``(fontKey(font), font_size)``：同字体同字号的所有牌子共用一个 loader，于是
    **贴图缓存也共享** —— 十几个 "x1" 牌子只会上传一张贴图。
    """
    key = (fontKey(font), int(font_size))
    entry = _LOADERS.get(key)
    if entry is None:
        entry = TextLoader(font, int(font_size))
        _LOADERS[key] = entry
    return entry


def clearCache() -> None:
    """释放共享的全部文字贴图（**需要在 GL 上下文里调用**）。

    切场景 / 换素材包之后想让文字重烘时用。已经烘过字的 :class:`Text` 不用重建：
    下次 ``bake()`` 会发现贴图被释放（``Texture.released``）并重烘。

    字体对象（``_LOADERS``）**保留** —— 重烘要走同一份字体，留着也省一次解析。
    """
    for loader in _LOADERS.values():
        loader.clearCache()



class Text(Sprite2D):
    """世界空间的文字板（见模块文档）：贴图由 :class:`~pyglgame.render.TextLoader` 烘。

    ⚠ :attr:`texture` 由 :meth:`bake` 管理（按文字 / 样式重烘、多块牌子共享同一张）——
    不要手动 ``setTexture()``；要换成任意贴图请直接用 :class:`~pyglgame.gameobject.Sprite2D`。
    """

    def __init__(self, text: str = "", font=None, font_size: int = 32,
                 x: float = 0.0, y: float = 0.0, z: float = 0.0,
                 p: float = 0.0, t: float = 0.0, r: float = 0.0,
                 pixels_per_unit: float = DEFAULT_PIXELS_PER_UNIT,
                 scale: float = 1.0, height: float = None, anchor: str = "center",
                 billboard: bool = False,
                 font_color=(255, 255, 255, 255), bg_color=(0, 0, 0, 0),
                 padding: int = 4, line_spacing: int = 4, align: str = "left",
                 stroke_width: int = 0, stroke_fill=None,
                 material=None, lighting_mode: str = "full",
                 receive_shadow: bool = True, transparent: bool = True,
                 name: str = None) -> None:
        # ⚠ 下面这些字段必须在 super().__init__() **之前**设好：``Sprite2D.__init__``
        # 末尾会调 ``rebuild()``（虚分派到本类），那时它已经在读这些字段了
        self.text = "" if text is None else str(text)
        #: 字体：``None``（PIL 内置）/ 路径 / file-like / PIL 字体对象，见 ``TextLoader``
        if isinstance(font, ResourceLocation):
            font = font.getIO()
        self.font = font
        if int(font_size) <= 0:
            raise ValueError("font_size 必须大于 0，收到 %r" % (font_size,))
        self.font_size = int(font_size)
        #: 字体颜色（**PIL 的 0-255 RGBA**）—— 改它要重烘；整体染色 / 淡出用 :attr:`color`
        self.font_color = tuple(font_color)
        #: 背景颜色（0-255 RGBA）：带 alpha 就是一个"牌子底色"；全透明 = 只有字
        self.bg_color = tuple(bg_color)
        #: 四周留白（像素）：给底色留边、也让字形不贴到贴图边缘
        self.padding = max(int(padding), 0)
        self.line_spacing = max(int(line_spacing), 0)
        #: 多行文字的对齐：``left`` / ``center`` / ``right`` / ``justify``（大小写不敏感）
        self.align = normalizeAlign(align)
        self.stroke_width = max(int(stroke_width), 0)
        self.stroke_fill = None if stroke_fill is None else tuple(stroke_fill)
        #: 锚点：``(x, y, z)`` 对应板子上的哪个点（同时也是旋转轴），默认 ``center``
        self.anchor = normalizeAnchor(anchor)
        #: 每世界单位多少像素（默认 32，与图集 / 瓦片地图同一约定）
        self.pixels_per_unit = max(float(pixels_per_unit), 1e-6)
        #: 整体倍率（1 = 按 ``pixels_per_unit`` 算出来的大小）
        self.scale = max(float(scale), 0.0)
        self._height_request = None if height is None else max(float(height), 0.0)
        #: 是否始终面向摄像机（每帧按相机右 / 上方向重算顶点，见模块文档）
        self.billboard = bool(billboard)
        self._loader = None
        self._layout_size = None        # 排版尺寸（贴图像素）；None = 还没算
        self._texture_dirty = True      # 需要（重新）烘字
        self._flags = None
        super().__init__(texture=None, width=1.0, height=1.0, x=x, y=y, z=z, r=r,
                         color=(1.0, 1.0, 1.0, 1.0),
                         # ⚠ 文字贴图是"没翻过"的 → 下边要取 v1（见模块文档）；
                         # 照抄 Sprite2D 的默认 (0, 0, 1, 1) 会把文字画成上下颠倒
                         uv=(0.0, 1.0, 1.0, 0.0),
                         material=material, transparent=transparent, name=name,
                         receive_shadow=receive_shadow, lighting_mode=lighting_mode)
        self.p, self.t = float(p), float(t)      # GameObject.__init__ 把 p / t 清成了 0
        self._applySize()                        # 纯 CPU：算世界尺寸 + 建顶点

    # === 尺寸（纯 CPU，构造完就能读）===

    def measure(self) -> tuple:
        """文字贴图的**像素**尺寸 ``(w, h)``（纯 CPU；空文字 = ``(0, 0)``）。

        世界尺寸看 :attr:`width` / :attr:`height`（= 这个 ÷ ``pixels_per_unit`` × ``scale``）。
        想按"牌子多高"定尺寸就用 :meth:`setHeight`。
        """
        return self._layoutSize()

    def _layoutSize(self) -> tuple:
        """排版尺寸（贴图像素）：纯 CPU、带缓存；空文字 = ``(0, 0)``。"""
        if self._layout_size is None:
            if not self.text.strip():
                self._layout_size = (0, 0)
            else:
                self._layout_size = self.loader().measure(
                    self.text, padding=self.padding, line_spacing=self.line_spacing,
                    align=self.align, stroke_width=self.stroke_width)
        return self._layout_size

    def loader(self) -> TextLoader:
        """本对象用的（共享）:class:`~pyglgame.render.TextLoader`；改字体后会自动换。"""
        if self._loader is None:
            self._loader = loaderFor(self.font, self.font_size)
        return self._loader

    def _invalidateText(self) -> None:
        """"像素会变"的参数动了：作废贴图与排版尺寸（下一帧重烘）。"""
        self._texture_dirty = True
        self._layout_size = None
        self._built_signature = None

    def _applySize(self) -> None:
        """把"排版像素尺寸"换算成世界尺寸（:attr:`width` / :attr:`height`）并重建几何。"""
        px_w, px_h = self._layoutSize()
        ppu = max(float(self.pixels_per_unit), 1e-6)
        if self._height_request is not None and px_h > 0:
            # 用户要的是"板子多高（世界单位）" → 反推 scale（于是宽度自动按比例）
            self.scale = max(float(self._height_request), 0.0) * ppu / float(px_h)
        self.width = float(px_w) / ppu * self.scale
        self.height = float(px_h) / ppu * self.scale
        self.rebuild()

    def setSize(self, width: float, height: float) -> "Text":
        """直接给世界尺寸（**会拉伸**；想按比例请用 :meth:`setHeight`）。"""
        self._height_request = None
        return super().setSize(width, height)

    # === 改参数（都返回 self，方便一行写完）===

    def setText(self, text) -> "Text":
        """换文字（下一帧重烘贴图 + 重算世界尺寸）。"""
        text = "" if text is None else str(text)
        if text != self.text:
            self.text = text
            self._invalidateText()
            self._applySize()
        return self

    def setFontColor(self, font_color) -> "Text":
        """换**字体颜色**（PIL 的 0-255 RGBA）—— 要重烘；整体染色用 :meth:`setColor`。"""
        self.font_color = tuple(font_color)
        self._invalidateText()
        return self

    def setBackground(self, bg_color) -> "Text":
        """换牌子**底色**（0-255 RGBA，含 alpha；全透明 = 只有字）。"""
        self.bg_color = tuple(bg_color)
        self._invalidateText()
        return self

    def setFont(self, font=None, font_size: int = None) -> "Text":
        """换字体 / 字号（``font_size=None`` = 字号不变）。"""
        self.font = font
        if font_size is not None:
            if int(font_size) <= 0:
                raise ValueError("font_size 必须大于 0，收到 %r" % (font_size,))
            self.font_size = int(font_size)
        self._loader = None                     # 字体变了 → 换共享 loader
        self._invalidateText()
        self._applySize()
        return self

    def setPadding(self, padding: int) -> "Text":
        self.padding = max(int(padding), 0)
        self._invalidateText()
        return self

    def setLineSpacing(self, line_spacing: int) -> "Text":
        self.line_spacing = max(int(line_spacing), 0)
        self._invalidateText()
        return self

    def setAlign(self, align: str) -> "Text":
        """多行文字的对齐：``left`` / ``center`` / ``right`` / ``justify``（大小写不敏感）。"""
        self.align = normalizeAlign(align)
        self._invalidateText()
        return self

    def setStroke(self, width: int = 0, fill=None) -> "Text":
        """描边（``width`` 像素 + ``fill`` 0-255 RGBA）：浅色字压在亮背景上也看得清。"""
        self.stroke_width = max(int(width), 0)
        self.stroke_fill = None if fill is None else tuple(fill)
        self._invalidateText()
        return self

    def setColor(self, color=(1.0, 1.0, 1.0, 1.0)) -> "Text":
        """顶点染色（**0-1 浮点**，与 ``Sprite2D.color`` 同一个意思）：淡入淡出 / 整体调色，**不用重烘**。"""
        self.color = tuple(float(v) for v in color)
        return self.rebuild()

    def setOpacity(self, alpha: float) -> "Text":
        """只改透明度（0-1，写进 :attr:`color` 的 alpha）。"""
        r, g, b, _ = self.color
        return self.setColor((r, g, b, max(0.0, min(1.0, float(alpha)))))

    def setAnchor(self, anchor) -> "Text":
        """换锚点（``center`` 默认 / ``topleft`` / …… 或直接给 ``(hx, hy)`` 系数）。"""
        if isinstance(anchor, (tuple, list)):
            self.anchor = (float(anchor[0]), float(anchor[1]))
        else:
            self.anchor = normalizeAnchor(anchor)
        return self.rebuild()

    def setPixelsPerUnit(self, pixels_per_unit: float) -> "Text":
        """换"每世界单位多少像素"（尺寸随之变化；**不用重烘**）。"""
        self.pixels_per_unit = max(float(pixels_per_unit), 1e-6)
        self._applySize()
        return self

    def setScale(self, scale: float) -> "Text":
        """整体倍率（> 1 更大）；改它**不用重烘**，也不再按 :meth:`setHeight` 定尺寸。"""
        self._height_request = None
        self.scale = max(float(scale), 0.0)
        self._applySize()
        return self

    def setHeight(self, height: float) -> "Text":
        """按"板子多高（世界单位）"定尺寸，宽度按贴图比例自动跟上（**不用重烘**）。"""
        self._height_request = max(float(height), 0.0)
        self._applySize()
        return self

    def setBillboard(self, active: bool = True) -> "Text":
        """开关广告牌（永远正对镜头）：立刻按当前相机朝向重算顶点。"""
        self.billboard = bool(active)
        return self.rebuild()

    def setOrientation(self, p: float = None, t: float = None,
                       r: float = None) -> "Text":
        """设朝向（度，绕 X / Y / Z，与相机的 ``p`` / ``t`` / ``r`` 同一套；不传的不改）。"""
        if p is not None:
            self.p = float(p)
        if t is not None:
            self.t = float(t)
        if r is not None:
            self.r = float(r)
        return self.rebuild()

    def setPosition(self, x: float, y: float, z: float = None) -> "Text":
        """设世界坐标（``z`` 不传就不改，方便只在 2D 平面里挪）。"""
        self.x, self.y = float(x), float(y)
        if z is not None:
            self.z = float(z)
        return self.rebuild()

    # === 烘字（唯一碰 GL 的一步）===

    def bake(self) -> bool:
        """按需烘字并上传贴图（**需要 GL 上下文**）；文字没变时什么都不做。

        返回"现在有没有可画的贴图"：空文字 / 烘不出来 → ``False``（调用方据此跳过绘制，
        免得画出一块白板）。想避免第一帧卡一下，可以在加载阶段先调一次。
        """
        if (self.texture is not None and not self.texture.released
                and not self._texture_dirty):
            return True
        self._texture_dirty = False
        if not self.text.strip():
            self.texture = None
            self._applySize()
            return False
        texture = self.loader().createTexture(
            self.text, color=self.font_color, bg_color=self.bg_color,
            padding=self.padding, line_spacing=self.line_spacing, align=self.align,
            stroke_width=self.stroke_width, stroke_fill=self.stroke_fill)
        self.texture = texture
        if texture is None:
            return False
        # 贴图尺寸就是权威排版尺寸（与 measure() 共用 layoutText，正常不会不一致）
        if (int(texture.w), int(texture.h)) != self._layout_size:
            self._layout_size = (int(texture.w), int(texture.h))
            self._applySize()
        return True


    # === 几何（纯 CPU）===

    def _rotationMatrix(self):
        """本板的旋转矩阵 ``Rx(p) · Ry(t) · Rz(r)``（与相机的 ``p/t/r`` 同一套顺序）。"""
        if not (self.p or self.t or self.r):
            return None
        matrix = glm.mat4(1.0)
        if self.p:
            matrix = glm.rotate(matrix, math.radians(self.p), glm.vec3(1.0, 0.0, 0.0))
        if self.t:
            matrix = glm.rotate(matrix, math.radians(self.t), glm.vec3(0.0, 1.0, 0.0))
        if self.r:
            matrix = glm.rotate(matrix, math.radians(self.r), glm.vec3(0.0, 0.0, 1.0))
        return matrix

    def _worldCorner(self, lx: float, ly: float, rotation) -> tuple:
        """局部角点 → **世界坐标**：绕 ``(x, y, z)`` 转（``rotation=None`` = 不转）再平移。"""
        if rotation is None:
            return (float(self.x) + lx, float(self.y) + ly, float(self.z))
        point = rotation * glm.vec4(lx, ly, 0.0, 0.0)
        return (float(self.x) + float(point.x), float(self.y) + float(point.y),
                float(self.z) + float(point.z))

    def buildSignature(self) -> tuple:
        """顶点签名：任意一项变了就重写顶点（位置 / 朝向 / 锚点 / 尺寸 / 顶点色）。

        - 静态模式：``(p, t, r)`` 直接在旋转矩阵里；
        - 广告牌：``(p, t)`` 不参与（朝向由相机决定），但**相机的右 / 上方向**参与 ——
          相机一动，四边形就得跟着转。
        """
        common = (round(float(self.x), 5), round(float(self.y), 5),
                  round(float(self.z), 5), self.anchor, self._layoutSize(),
                  round(float(self.width), 6), round(float(self.height), 6),
                  tuple(self.color), bool(self.billboard))
        if self.billboard:
            return common + (round(float(self.r), 4), cameraAxes())
        return common + (round(float(self.p), 5), round(float(self.t), 5),
                         round(float(self.r), 5))

    def rebuild(self) -> "Text":
        """（重新）生成 **CPU 侧**顶点 —— **两种模式的顶点都是世界坐标**。

        ``RenderGlobal`` 的 translate / rotate / scale 是**整体世界坐标系**的调节，对象
        不该拿它们摆自己（也不该覆盖它们），所以：

        - 静态模式：局部角点按 ``Rx(p)·Ry(t)·Rz(r)`` 转完再加 ``(x, y, z)``；
        - ``billboard``：沿当前相机的右 / 上方向张开（相机一动就要重建，
          :meth:`rebuildIfNeeded` 每帧检查）。

        ⚠ 这里**不碰 GL**（与 ``Sprite2D.rebuild`` 同一约定）：真正的上传由
        :meth:`render` 里的 ``RenderBuffer.build()`` 完成 —— 所以构造完就能算尺寸，
        也能直接放进场景文档。
        """
        flags = POS | COL | TEX | self.material.formatFlags()
        if flags != self._flags:
            self.render_buffer.createBuffer(GL_TRIANGLES, flags)
            self._flags = flags
        builder = self.render_buffer.buffer_builder
        builder.clear()
        width, height = float(self.width), float(self.height)
        if width > 0.0 and height > 0.0:
            left, right, bottom, top = anchorRect(width, height, self.anchor)
            if self.billboard:
                (rx, ry, rz), (ux, uy, uz) = billboardAxes(self.r)
                ox, oy, oz = float(self.x), float(self.y), float(self.z)
                positions = [
                    (ox + rx * lx + ux * ly, oy + ry * lx + uy * ly,
                     oz + rz * lx + uz * ly)
                    for lx, ly in ((left, bottom), (right, bottom),
                                   (right, top), (left, top))]
            else:
                rotation = self._rotationMatrix()
                positions = [self._worldCorner(lx, ly, rotation)
                             for lx, ly in ((left, bottom), (right, bottom),
                                            (right, top), (left, top))]
            u0, v0, u1, v1 = self.uv          # 下边取 v0、上边取 v1（贴图没翻过，见模块文档）
            r, g, b, a = self.color
            # 顶点顺序与 UV 配对与 Sprite2D 完全一致：
            # 左下 → 右下 → 右上 / 左下 → 右上 → 左上
            for index, (u, v) in zip((0, 1, 2, 0, 2, 3),
                                     ((u0, v0), (u1, v0), (u1, v1),
                                      (u0, v0), (u1, v1), (u0, v1))):
                cx, cy, cz = positions[index]
                builder.pos(cx, cy, cz).col(r, g, b, a).tex(u, v).end()
        self._built_signature = self.buildSignature()
        return self

    # === 渲染 ===

    def render(self, dt: float, fps: float) -> None:
        if not self.active:
            return
        if getattr(RenderGlobal.instance, "using_shader", None) is None:
            return                       # 还没进渲染循环（主着色器都没加载）→ 什么都不做
        if not self.bake():              # 空文字 / 烘不出来 → 什么都不画（别画一块白板）
            return
        # 顶点（世界坐标）由 Sprite2D.render → rebuildIfNeeded() 按签名补齐；
        # 那几个全局模型矩阵一个字都不碰（见模块文档）
        super().render(dt, fps)

    def onRemoved(self) -> None:
        """注销时释放自己的 VBO / VAO。

        贴图**不在这里释放**：它属于共享缓存（同一款文字可能还有别的牌子在用）——
        要统一清用 :func:`clearCache`（或 ``TextLoader.clearCache()``）。
        """
        if self.render_buffer is not None:
            self.render_buffer.release()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return (f"<Text{label} {self.text!r} 字号={self.font_size} "
                f"{self.width:g}x{self.height:g} at "
                f"({self.x:.2f}, {self.y:.2f}, {self.z:.2f}) "
                f"p/t/r=({self.p:g}, {self.t:g}, {self.r:g})"
                f"{' 广告牌' if self.billboard else ''}"
                f"{' 已烘' if self.texture is not None else ' 待烘'}>")

