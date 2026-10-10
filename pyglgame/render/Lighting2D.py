"""2D 光照的材质层（Phase 7）：法线贴图 / 高光贴图 / 材质参数。

## 2D 光照是怎么跑起来的

引擎的光照是**前向**的（见 ``docs/光照与体积渲染.md``），2D 不需要新 pass，
只要回答三个问题：**法线从哪来、高光有多强、环境光是什么颜色**。

- **法线**：2D 精灵都躺在 XY 平面上、朝向 +Z。所以切线空间的三个轴就是
  ``(世界 +X, 世界 +Y, 朝相机)``，法线贴图直接解到世界法线即可（``dis.frag`` 的
  ``shadeNormal()``）。物体自己在屏幕里转了角度、或者贴图被镜像时，
  由 ``rotation`` / ``scale`` 修正 —— 否则高光会朝错方向。
- **光源**：用的还是 :class:`~pyglgame.render.Light.Light`。2D 里**光的 z 就是"离精灵
  平面的高度"**：点光放 ``z > 0`` 就能照亮平面的精灵；正好在平面里（``z = 0``）的光
  对平面精灵几乎没有贡献（``N·L ≈ 0``）。快捷构造见 ``Light.light2D()``。
- **环境光**：2D 模式（``u_light2D == 1``）用平环境光 ``RenderGlobal.ambient2D``，
  不再用半球环境光（精灵的 ``N.y`` 没有物理意义）。

## 材质开关走"顶点格式位"

``N2D`` / ``S2D`` 两个位由 :class:`Material2D` 决定、由**几何**带进 draw call
（``RenderBuffer`` 每次绘制都上传 ``formatType``），所以"这个精灵有法线贴图、下一个没有"
是逐 draw call 生效的，不会把上一个精灵的贴图泄漏给后面的图元。

贴图单元：``tex0`` = 颜色、``tex3`` = 法线贴图、``tex4`` = 高光贴图
（``tex5`` 是 lightmap，``tex1/tex2`` 归 Bloom）。

``tex4`` 有两种语义，由 :attr:`Material2D.specular_mode` 选（见 ``dis.frag`` 的
``u_specularMode``）：

- ``"highlight"``（默认）：**Blinn-Phong 镜面光斑** —— ``a`` = 光斑强度、``rgb`` = 光斑颜色。
  只有反射方向对上的地方才亮，跟视角相关；
- ``"brightness"``：**直接光亮度倍率** —— ``a × 2`` = 该像素直接光的倍率
  （``0.5`` = 不变、``1`` = 亮一倍、``0`` = 全灭），``rgb`` = 染色。
  只要被照亮就逐像素生效，环境光不受影响。
"""

import numpy as np

from ..RenderGlobal import RenderGlobal
from .BufferBuilder import AMB2D, N2D, S2D, UNLIT2D

#: 法线贴图使用纹理单元 3，高光贴图使用单元 4（与 ``dis.frag`` 的 tex3/tex4 对应）
NORMAL_TEXTURE_UNIT = 3
SPECULAR_TEXTURE_UNIT = 4

#: 高光贴图语义：Blinn-Phong 镜面光斑（默认；``a`` = 光斑强度、``rgb`` = 光斑颜色）
SPECULAR_HIGHLIGHT = 0
#: 高光贴图语义：直接光亮度倍率（``a × 2`` = 直接光倍率：0.5 不变 / 1 加倍 / 0 全灭）
SPECULAR_BRIGHTNESS = 1
#: ``specular_mode`` 名字 → ``u_specularMode`` 标志
SPECULAR_MODES = {"highlight": SPECULAR_HIGHLIGHT, "brightness": SPECULAR_BRIGHTNESS}

#: 光照模式：``"full"`` = 环境光 + 直接光 + 高光（默认）
LIGHTING_FULL = 0
#: 光照模式：``"ambient"`` = **只吃环境光**（直接光 / 高光 / 阴影整段跳过）
LIGHTING_AMBIENT = AMB2D
#: 光照模式：``"none"`` = **完全不受光照**（环境光也不加，只输出颜色 / 贴图）
LIGHTING_NONE = UNLIT2D
#: ``lighting_mode`` 名字 → 顶点格式位（**这些位逐 draw call 生效**，见 BufferBuilder）
LIGHTING_MODES = {"full": LIGHTING_FULL, "ambient": LIGHTING_AMBIENT,
                  "none": LIGHTING_NONE}
#: 别名（外部工具 / 资产里常见的写法都收）
LIGHTING_ALIASES = {"default": "full", "all": "full", "on": "full",
                    "ambient_only": "ambient", "ambientonly": "ambient",
                    "unlit": "none", "no_light": "none", "nolight": "none",
                    "off": "none"}


def lightingModeFlags(mode) -> int:
    """把 ``lighting_mode``（名字或位）转成顶点格式位（``0`` / ``AMB2D`` / ``UNLIT2D``）。

    名字大小写不敏感，别名也认（``unlit`` / ``ambient_only`` …）；写错立刻抛
    ``ValueError``（而不是画到一半才知道）。
    """
    if isinstance(mode, str):
        key = mode.strip().lower()
        key = LIGHTING_ALIASES.get(key, key)
        if key not in LIGHTING_MODES:
            raise ValueError(f"未知的光照模式：{mode!r}"
                             f"（可选 {sorted(LIGHTING_MODES)}）")
        return LIGHTING_MODES[key]
    return int(mode)


def lightingModeName(mode) -> str:
    """把 ``lighting_mode`` 归一成规范名字（``"full"`` / ``"ambient"`` / ``"none"``）。"""
    flags = lightingModeFlags(mode)
    for name, value in LIGHTING_MODES.items():
        if value == flags:
            return name
    return "full"


def _asBool(value) -> bool:
    """宽松布尔：``"false"`` / ``"0"`` / ``"no"`` / ``"off"`` 也算 ``False``。

    外部工具（Tiled 的自定义属性如果选成 string 类型、手写的 JSON）常把布尔写成字符串 ——
    ``bool("false")`` 是 ``True``（会让人以为开关没生效），这里替调用方兜住。
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off", "none")
    return bool(value)


def specularModeFlag(mode) -> int:
    """把 ``specular_mode``（名字或标志整数）转成 ``u_specularMode`` 要的标志。

    名字大小写不敏感；名字写错会立刻抛 ``ValueError``（而不是画到一半才知道）。
    """
    if isinstance(mode, str):
        key = mode.strip().lower()
        if key not in SPECULAR_MODES:
            raise ValueError(f"未知的高光贴图语义：{mode!r}"
                             f"（可选 {sorted(SPECULAR_MODES)}）")
        return SPECULAR_MODES[key]
    return int(mode)


class Material2D:
    """一个 2D 精灵的材质（法线贴图 + 高光贴图 + 参数）。

    ::

        material = Material2D(normal_map=tex_n, specular_map=tex_spec, shininess=64.0)
        sprite = Sprite2D(albedo, material=material)

    字段：

    - ``normal_map`` / ``specular_map``：:class:`~pyglgame.render.Texture.Texture`
      （``None`` = 该特性关闭 → 顶点格式位也跟着去掉）
    - ``normal_strength``：法线强度，``0`` = 平面、``1`` = 贴图原样、``>1`` 夸张
    - ``specular_color`` / ``specular_strength`` / ``shininess``：高光颜色/强度/锐度
    - ``specular_contrast``：高光贴图的**对比度**，以 ``a=0.5`` 为中点拉对比（``1`` = 原样；
      ``>1`` 更锐利、``<1`` 更平）。作用于 ``tex4`` 的 ``a`` 通道（强度 / 倍率）
    - ``scale`` / ``offset``：材质贴图的 UV 变换（默认与顶点 UV 一致）；
      ``scale`` 为负 = UV 镜像（法线也会跟着镜像）
    - ``rotation``：物体在屏幕里的旋转（度）—— 让法线跟着图形一起转
    - ``specular_mode``：高光贴图（``tex4``）的语义，``"highlight"``（默认）= 镜面光斑、
      ``"brightness"`` = 直接光亮度倍率（见模块文档）
    - ``lighting_mode``：**这一批三角形吃不吃光照**，逐 draw call 生效（顶点格式位）：
      ``"full"``（默认）= 环境光 + 直接光 + 高光；
      ``"ambient"`` = **只吃环境光**（直接光 / 高光 / 阴影整段跳过）；
      ``"none"`` = **完全不受光照**（环境光也不加，只输出颜色 / 贴图）。
      别名也认：``unlit`` / ``no_light`` / ``off`` → ``"none"``，``ambient_only`` → ``"ambient"``。
      Tiled 里给**图层**写自定义属性 ``lighting_mode`` 就是它（见
      ``docs/2D光照与阴影.md`` 2.1 与 ``docs/2D帧动画与场景导入.md``）
    - ``receive_shadow``：**是否接收 2D 阴影**（默认 ``True``）。2D 场景通常分两层：
      背景层用默认值接收阴影，"光源 + 物体层"自己的精灵设成 ``False``，
      免得同层物体互相压暗（见 ``docs/2D光照与阴影.md`` 2.4）
    """

    def __init__(self, normal_map=None, specular_map=None, normal_strength: float = 1.0,
                 specular_color=(1.0, 1.0, 1.0), specular_strength: float = 1.0,
                 shininess: float = 32.0, specular_contrast: float = 1.0,
                 scale=(1.0, 1.0), offset=(0.0, 0.0),
                 rotation: float = 0.0, specular_mode: str = "highlight",
                 receive_shadow: bool = True, lighting_mode: str = "full") -> None:
        self.normal_map = normal_map
        self.specular_map = specular_map
        self.normal_strength = float(normal_strength)
        self.specular_color = tuple(float(v) for v in specular_color)
        self.specular_strength = float(specular_strength)
        self.shininess = float(shininess)
        #: 高光贴图对比度（以 a=0.5 为中点拉对比；1 = 原样）
        self.specular_contrast = float(specular_contrast)
        self.scale = (float(scale[0]), float(scale[1]))
        self.offset = (float(offset[0]), float(offset[1]))
        self.rotation = float(rotation)
        #: 高光贴图语义（``"highlight"`` / ``"brightness"``，见模块文档）
        self.specular_mode = specular_mode
        self.specularModeFlag()          # 名字写错就立刻抛，而不是画到一半才炸
        #: 光照模式（``"full"`` / ``"ambient"`` / ``"none"``）：走顶点格式位，逐 draw call 生效
        self.lighting_mode = lightingModeName(lighting_mode)
        #: 是否接收 2D 阴影（``False`` = 该"层"不接受遮挡，物体层自己的精灵常用）
        self.receive_shadow = _asBool(receive_shadow)
        #: 上次上传的状态（逐 draw call 跳过重复上传）
        self._last_state = None

    # === 顶点格式 ===

    def formatFlags(self) -> int:
        """本材质需要的格式位（``N2D`` / ``S2D`` / ``AMB2D`` / ``UNLIT2D``）。

        光照模式的两个位是**整批开关**：只吃环境光 / 完全不受光照都由这一批三角形的
        ``formatType`` 带进 draw call —— 所以图层之间互不影响，不需要额外的 uniform。
        """
        flags = lightingModeFlags(self.lighting_mode)
        if self.normal_map is not None:
            flags |= N2D
        if self.specular_map is not None:
            flags |= S2D
        return flags

    def hasNormalMap(self) -> bool:
        return self.normal_map is not None

    def hasSpecularMap(self) -> bool:
        return self.specular_map is not None

    def specularModeFlag(self) -> int:
        """高光贴图的语义标志（与 ``dis.frag`` 的 ``u_specularMode`` 对应）。"""
        return specularModeFlag(self.specular_mode)

    def lightingModeFlag(self) -> int:
        """光照模式的格式位（``0`` / ``AMB2D`` / ``UNLIT2D``），由 ``formatFlags()`` 带进绘制。"""
        return lightingModeFlags(self.lighting_mode)

    # === 上传 ===

    def state(self, rotation: float = None) -> tuple:
        """当前要上传的状态（值相同就跳过上传）。"""
        return (id(self.normal_map), id(self.specular_map), self.normal_strength,
                self.specular_color, self.specular_strength, self.shininess,
                self.specular_contrast, self.scale, self.offset,
                self.rotation if rotation is None else float(rotation),
                self.specularModeFlag(), self.receive_shadow)

    def apply(self, shader=None, rotation: float = None, force: bool = False) -> None:
        """把材质状态传给 uber shader（每个精灵画之前调一次）。

        ``rotation`` 是物体在屏幕里的旋转（度）：传了就覆盖 ``self.rotation``
        —— :class:`~pyglgame.gameobject.Sprite2D.Sprite2D` 就是这么把自己转的角度带进来的。

        ⚠ **不做"值没变就跳过"的缓存**：纹理单元（``tex3``/``tex4``）是**全局 GL 状态**，
        别的精灵随时会把它改掉。按材质实例缓存会导致"这个精灵采样到上一个精灵的法线贴图"，
        所以每次绘制都重新绑定/上传（对 2D 精灵这点开销可以忽略）。
        """
        if shader is None:
            shader = RenderGlobal.instance.using_shader
        if shader is None:
            return
        rotation = self.rotation if rotation is None else float(rotation)
        from OpenGL.GL import GL_TEXTURE0, glActiveTexture
        shader.uniform1f("u_normalStrength", self.normal_strength)
        shader.uniform2f("u_materialScale", self.scale[0], self.scale[1])
        shader.uniform2f("u_materialOffset", self.offset[0], self.offset[1])
        shader.uniform1f("u_materialRotation", rotation)
        shader.uniform3f("u_specularColor", *self.specular_color)
        shader.uniform1f("u_specularStrength", self.specular_strength)
        shader.uniform1f("u_specularShininess", max(self.shininess, 1.0))
        shader.uniform1f("u_specularContrast", self.specular_contrast)
        shader.uniform1i("u_specularMode", self.specularModeFlag())
        shader.uniform1f("u_receiveShadow", 1.0 if self.receive_shadow else 0.0)
        if self.normal_map is not None:
            self.normal_map.bind(GL_TEXTURE0 + NORMAL_TEXTURE_UNIT)
        if self.specular_map is not None:
            self.specular_map.bind(GL_TEXTURE0 + SPECULAR_TEXTURE_UNIT)
        # Texture.bind 会把活动纹理单元留在 3/4 上：复位，免得后面的图元绑错单元
        glActiveTexture(GL_TEXTURE0)

    def invalidate(self) -> None:
        """兼容入口（现在 :meth:`apply` 每次都上传，不再需要作废缓存）。"""
        self._last_state = None

    def __repr__(self) -> str:
        return (f"<Material2D normal={'yes' if self.hasNormalMap() else 'no'} "
                f"specular={'yes' if self.hasSpecularMap() else 'no'} "
                f"shininess={self.shininess:g}>")


# === 从高度图生成法线贴图（没有美术资源时的救命工具）===

def heightToNormal(height: np.ndarray, strength: float = 2.0,
                   wrap: bool = True) -> np.ndarray:
    """高度图（亮度 ``0..1`` 的 float 数组）→ 法线贴图（``uint8`` RGB ``0..255``）。

    - 中心差分算梯度，``strength`` 越大凹凸越明显；
    - ``wrap=True`` 时按**环绕**取邻居：贴图可以四方向平铺，接缝处不会出现一道亮边；
    - 返回切线空间法线（``x`` 向右、``y`` 向上、``z`` 朝观察者，且 ``z >= 0``），
      正好是 ``dis.frag`` 的 ``shadeNormal()`` 期待的那种：``N = tex * 2 - 1``。
    """
    height = np.asarray(height, dtype=np.float32)
    if height.ndim == 3:                       # 彩色图 → 取亮度
        height = height[..., :3].mean(axis=2)
    if height.size == 0:
        raise ValueError("高度图为空")
    if wrap:
        left = np.roll(height, 1, axis=1)
        right = np.roll(height, -1, axis=1)
        down = np.roll(height, 1, axis=0)
        up = np.roll(height, -1, axis=0)
    else:
        left = np.pad(height, ((0, 0), (1, 0)), mode="edge")[:, :-1]
        right = np.pad(height, ((0, 0), (0, 1)), mode="edge")[:, 1:]
        down = np.pad(height, ((1, 0), (0, 0)), mode="edge")[:-1, :]
        up = np.pad(height, ((0, 1), (0, 0)), mode="edge")[1:, :]
    dx = (right - left) * float(strength)
    dy = (up - down) * float(strength)
    nx, ny, nz = -dx, -dy, np.ones_like(dx)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    length[length < 1e-8] = 1.0
    normal = np.stack((nx / length, ny / length, nz / length), axis=-1)
    return np.clip((normal * 0.5 + 0.5) * 255.0, 0.0, 255.0).astype(np.uint8)


def normalMapFromImage(image, strength: float = 2.0, wrap: bool = True):
    """PIL 图（或引擎的 :class:`~pyglgame.render.Image.Image`）→ PIL 法线贴图（RGBA）。

    没有美术的 normal map 时这样生成::

        from PIL import Image
        from pyglgame.render.Image import Image as GLImage
        from pyglgame.render.Lighting2D import normalMapFromImage
        from pyglgame.render.TextureCache import loadTextureFromImage

        normal_pil = normalMapFromImage(Image.open("res/brick.png"), strength=3.0)
        normal_tex = loadTextureFromImage(GLImage.fromPIL(normal_pil))
    """
    from PIL import Image as PILImage
    source = getattr(image, "image", image)         # 兼容引擎的 Image 包装
    if not isinstance(source, PILImage.Image):
        raise TypeError(f"需要一个 PIL 图像，收到 {type(image).__name__}")
    array = np.asarray(source.convert("L"), dtype=np.float32) / 255.0
    normal = heightToNormal(array, strength=strength, wrap=wrap)
    return PILImage.fromarray(normal, "RGB").convert("RGBA")


def brightnessToSpecular(image, base_color=(1.0, 1.0, 1.0), contrast: float = 1.0,
                         multiply: float = 1.0):
    """亮度图 → 高光贴图（``rgb = 高光颜色``、``a = 强度``）。

    亮的地方更"光滑"：这是给普通彩色贴图快速造一张高光图的最省事做法
    （``contrast`` 提高对比、``multiply`` 整体压暗/提亮）。
    """
    from PIL import Image as PILImage
    source = getattr(image, "image", image)
    if not isinstance(source, PILImage.Image):
        raise TypeError(f"需要一个 PIL 图像，收到 {type(image).__name__}")
    luminance = np.asarray(source.convert("L"), dtype=np.float32) / 255.0
    strength = np.clip(((luminance - 0.5) * float(contrast) + 0.5) * float(multiply),
                       0.0, 1.0)
    height, width = strength.shape
    array = np.zeros((height, width, 4), dtype=np.float32)
    array[..., 0] = float(base_color[0])
    array[..., 1] = float(base_color[1])
    array[..., 2] = float(base_color[2])
    array[..., 3] = strength
    return PILImage.fromarray((array * 255.0).astype(np.uint8), "RGBA")
