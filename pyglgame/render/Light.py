"""光源与光源管理（前向光照）。

## 为什么是前向（forward）而不是延迟（deferred）

引擎已经有两套顺序无关透明：WBOIT 的 ``accum/reveal`` 走加权混合、A 方案走
对象级深度排序 + 关闭深度写入。它们都需要**光照之后的逐片元颜色**；延迟光照
还要把透明内容再拉回来单独算一遍。而且后续要加的体积渲染（雾 / 体积光）需要
的输入恰好是"颜色 + 深度 + 阴影贴图 + 光源方向"——前向全都有，延迟反而要额外
补一个 pass。

## 数据上传

uniform 数组（``u_lightDir[0]`` 这种下标形式），复用现有 :class:`Shader` API，
不引入 UBO/SSBO。以后要支持大量灯光（clustered / light grid），只要改
:meth:`LightManager.upload` 的上传方式，着色器里的 uniform 名与调用方都不用动。

## 与 ``res/shader/dis.frag`` 的约定（一一对应）

======================  =======  ==================================================
uniform                 类型     含义
======================  =======  ==================================================
``u_lightCount``        int      当前光源数。**0 = 完全不做光照**（保持旧画面）
``u_lightKind[i]``      int      0 方向光 / 1 点光 / 2 聚光
``u_lightDir[i]``       vec3     方向光、聚光的**光线传播方向**（从光源射出，已归一化）
``u_lightPos[i]``       vec3     点光、聚光的位置
``u_lightColor[i]``     vec3     颜色 × 强度（HDR 允许 > 1）
``u_lightParam[i]``     vec4     x=半径(0=按距离衰减) y=聚光半角余弦 z=是否投影阴影 w=高光强度
``u_lightParam2[i]``    vec4     x=距离衰减指数 n(默认 2 = 1/d²) y/z/w=保留
======================  =======  ==================================================

注意：**没有光源的场景渲染结果与加光照之前逐像素完全一致**（dis.frag 里用
``u_lightCount > 0`` 直接跳过整段光照），所以现有 demo 与回归测试不受影响。
"""

import glm

from ..RenderGlobal import RenderGlobal
from ..gameobject.GameObject import GameObject

# 前向光照的光源上限（必须与 dis.frag 里的 ``#define MAX_LIGHTS`` 一致，
# tests/test_engine.py 里有断言守着）。超出的光源会被忽略并警告一次。
MAX_LIGHTS = 8

DIRECTIONAL = 0
POINT = 1
SPOT = 2

#: 2D 光源默认"离精灵平面的高度"（见 docs/2D光照与阴影.md）
DEFAULT_LIGHT2D_HEIGHT = 5.0
#: 2D 方向光（太阳）默认方向：带一点 z 斜率，让"照亮多少"更自然
#: （2D 阴影只看 xy 分量 —— 见 pyglgame/render/Shadow2D.py 的 normalizedDirection2D）
DEFAULT_SUN2D_DIRECTION = (-0.42, -0.74, -0.52)

# === 距离衰减（点光 / 聚光，对应 u_lightParam2[i]）===

#: 距离衰减指数 ``n`` —— 就是"1/dⁿ"里的那个 **n**（任意正数都行，不限于 2）：
#: ``1`` = 线性（≈ 1/d）、``0.5`` = 更慢（≈ 1/√d）、``2`` = 平方反比（默认）、``4``/``8`` 更快收敛。
DEFAULT_ATTENUATION_EXPONENT = 2.0


def light2D(x: float, y: float, color=(1.0, 1.0, 1.0), intensity: float = 1.0,
            radius: float = 12.0, height: float = DEFAULT_LIGHT2D_HEIGHT,
            specular: float = 0.35, cast_shadow: bool = False,
            attenuation_exponent: float = DEFAULT_ATTENUATION_EXPONENT) -> "Light":
    """一盏 2D 点光（快捷构造）：位置 ``(x, y)`` + 高度 ``z = height``。

    ⚠ **2D 里光的 z 是"离精灵平面的高度"**：精灵都躺在 ``z ≈ 0`` 的 XY 平面上，
    所以光必须有高度（``height > 0``）才能照亮它们 —— 正好落在平面上的光
    ``N·L ≈ 0``，几乎不发光（这是 3D 前向光照的正常结果，不是 bug）。

    ``radius`` = 作用范围（``0`` = 不截断，用下面的 ``1/dⁿ`` 反比衰减）；
    ``attenuation_exponent`` = **距离衰减指数** ``n``（"1/dⁿ"，任意正数，默认 ``2``）：
    ``1`` = 线性、``0.5`` = 更慢、``0.25`` = 更慢。详见 :meth:`Light.setAttenuation`。

    ``cast_shadow=True`` 时这盏灯会被物体遮挡：它前方（背光侧）的接收层变暗，
    配合 ``camera.useShadow2D()`` 与 :class:`~pyglgame.gameobject.ShadowCaster2D.ShadowCaster2D`。
    """
    light = Light(POINT, color, intensity)
    light.setPosition(x, y, float(height))
    light.range = float(radius)
    light.specular = float(specular)
    light.attenuation_exponent = float(attenuation_exponent)
    light.cast_shadow = bool(cast_shadow)
    return light


def spot2D(x: float, y: float, direction=(1.0, 0.0, -0.3), angle: float = 28.0,
           color=(1.0, 1.0, 1.0), intensity: float = 1.0, radius: float = 12.0,
           height: float = DEFAULT_LIGHT2D_HEIGHT, specular: float = 0.35,
           cast_shadow: bool = False,
           attenuation_exponent: float = DEFAULT_ATTENUATION_EXPONENT) -> "Light":
    """一盏 2D 聚光（快捷构造）：位置 ``(x, y, height)`` + 光锥方向 ``direction``。

    ``angle`` 是光锥**半角**（度）。遮挡关系与点光完全一样（都是从光源位置径向
    投出去，见 :func:`pyglgame.render.Shadow2D.radialShadowTriangles`），
    光锥只影响"照到哪一片"。距离衰减与点光共用一套（``attenuation_exponent``）。
    """
    light = Light(SPOT, color, intensity)
    light.setPosition(x, y, float(height))
    light.setDirection(*direction)
    light.setSpotAngle(angle)
    light.range = float(radius)
    light.specular = float(specular)
    light.attenuation_exponent = float(attenuation_exponent)
    light.cast_shadow = bool(cast_shadow)
    return light


def sun2D(color=(1.0, 0.96, 0.90), intensity: float = 1.1,
          direction=DEFAULT_SUN2D_DIRECTION, cast_shadow: bool = True,
          specular: float = 0.25) -> "Light":
    """一盏 2D 方向光（快捷构造）：默认投影阴影，长度方向就是 xy 方向。

    2D 场景的平行光阴影就是它投的：配合 ``camera.useShadow2D()`` 与
    :class:`~pyglgame.gameobject.ShadowCaster2D.ShadowCaster2D`，
    物体背光那一侧整片变暗（见 ``docs/2D光照与阴影.md`` 2.4）。
    """
    light = Light(DIRECTIONAL, color, intensity)
    light.setDirection(*direction)
    light.specular = float(specular)
    light.cast_shadow = bool(cast_shadow)
    return light



class Light(GameObject):
    """一盏光源。

    继承 :class:`GameObject`：放进场景就自动拥有生命周期（``delete()`` 会把
    自己从 :class:`LightManager` 和渲染层里注销），不需要另外维护灯光列表。
    光源不产生任何几何，:meth:`render` 是空实现。
    """

    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, kind: int = DIRECTIONAL,
                 color: tuple[float, float, float] = (1.0, 1.0, 1.0),
                 intensity: float = 1.0) -> None:
        super().__init__()
        self.kind: int = int(kind)
        # ⚠ 只取前三个分量：Tiled 的颜色属性是 ``#AARRGGBB``（前两位是 alpha，解析出来 4 个分量），
        # 直接 ``glm.vec3(*color)`` 会炸。多给 alpha 就忽略（光照里的颜色没有 alpha 语义），
        # 3 元组 / list / glm.vec3 全部照旧。
        self.color: glm.vec3 = glm.vec3(*tuple(color)[:3])
        self.intensity: float = float(intensity)
        # 方向光 / 聚光的光线传播方向（从光源射出）
        self.direction: glm.vec3 = glm.vec3(-0.4, -1.0, -0.3)
        # 点光 / 聚光的作用半径；0 = 不按半径截断，退回 1/(1 + dⁿ) 反比衰减
        self.range: float = 0.0
        # 聚光半角的余弦（越小光锥越窄）；默认约 25°
        self.spot_cos: float = 0.906
        # 是否投影阴影：3D 走相机 useShadow()（方向光阴影贴图）；
        # 2D 走相机 useShadow2D()（逐灯几何遮挡遮罩，点光/聚光/方向光都能投）
        self.cast_shadow: bool = False
        # 高光强度（对应 u_lightParam[i].w）
        self.specular: float = 0.25
        # 距离衰减指数 n（"1/dⁿ" 里的 n，对应 u_lightParam2[i]，点光 / 聚光）：
        #   1 = 线性（≈ 1/d）、0.5 = 更慢（≈ 1/√d）、2 = 平方反比（默认）、4/8 更快收敛
        self.attenuation_exponent: float = DEFAULT_ATTENUATION_EXPONENT
        RenderGlobal.instance.light_manager.add(self)

    # === 便捷设置 ===

    def setColor(self, r: float, g: float, b: float,
                 intensity: float = None) -> "Light":
        self.color = glm.vec3(r, g, b)
        if intensity is not None:
            self.intensity = float(intensity)
        return self

    def setIntensity(self, intensity: float) -> "Light":
        self.intensity = float(intensity)
        return self

    def setDirection(self, x: float, y: float, z: float) -> "Light":
        """设置方向光/聚光的光线传播方向（内部归一化，零向量会被忽略）。"""
        direction = glm.vec3(x, y, z)
        if glm.length(direction) > 1e-6:
            self.direction = glm.normalize(direction)
        return self

    def setPosition(self, x: float, y: float, z: float) -> "Light":
        """设置点光/聚光位置（方向光用不到位置）。"""
        self.x, self.y, self.z = x, y, z
        return self

    def setShadow(self, cast: bool = True) -> "Light":
        self.cast_shadow = bool(cast)
        return self

    def setRange(self, radius: float) -> "Light":
        self.range = float(radius)
        return self

    def setAttenuation(self, exponent: float = None, radius: float = None) -> "Light":
        """距离衰减（点光 / 聚光）：指数 ``n`` + 作用半径，``None`` = 不改这一项。

        - ``exponent``：就是 **"1/dⁿ" 里的 n**，任意正数都行、**不限于 2** ——
          ``1`` = 线性（≈ ``1/d``）、``0.5`` = 更慢（≈ ``1/√d``）、``0.25`` = 更慢、
          ``2`` = 平方反比（默认）、``4`` / ``8`` = 更快收敛；
        - ``radius``：作用范围。``> 0`` 时衰减是 ``(1 - (d/r)ⁿ)ⁿ``（到 r 处正好归零，
          不会除零爆亮）；``0`` 时是 ``1 / (1 + dⁿ)``（"1/dⁿ" 的幂律，式子里那个 ``+1``
          只是防止 ``d → 0`` 时爆亮）。``n <= 0`` 没有意义（着色器当成 ``0.001``）。

        ::

            lamp.setAttenuation(exponent=0.5)      # 1/√d：更慢、光照到更远
            lamp.setAttenuation(exponent=1.0)      # 线性
            lamp.setAttenuation(radius=17)         # 到 17 个单位外完全熄灭
        """
        if exponent is not None:
            self.attenuation_exponent = float(exponent)
        if radius is not None:
            self.range = float(radius)
        return self

    def setSpotAngle(self, degrees: float) -> "Light":
        """设置聚光**半角**（度）。"""
        self.spot_cos = float(glm.cos(glm.radians(max(0.1, min(89.9, float(degrees))))))
        return self

    @property
    def radiance(self) -> glm.vec3:
        """颜色 × 强度（HDR，允许 > 1）。"""
        return self.color * self.intensity

    # === 生命周期 ===

    def render(self, dt: float, fps: float) -> None:
        """光源不产生几何。"""

    def onRemoved(self) -> None:
        manager = getattr(RenderGlobal.instance, "_light_manager", None)
        if manager is not None:
            manager.remove(self)


class LightManager:
    """场景光源表：收集光源并在每帧上传到 uber shader。

    光源默认在 ``Light.__init__`` 里自动注册（与 GameObject 自动进入渲染层
    一致），所以绝大多数情况下不需要手动调用 :meth:`add`。
    """

    def __init__(self, max_lights: int = MAX_LIGHTS) -> None:
        self.max_lights: int = max(1, int(max_lights))
        self.lights: list[Light] = []
        self._warned_overflow = False

    # === 收集 ===

    def add(self, light: Light) -> Light:
        if light not in self.lights:
            self.lights.append(light)
            if len(self.lights) > self.max_lights and not self._warned_overflow:
                self._warned_overflow = True
                from ..xyazhServer.ConsoleMessage import ConsoleMessage
                ConsoleMessage.printWarning(
                    f"光源数超过前向光照上限 {self.max_lights}，多余的光源不会被渲染"
                    "（可调大 pyglgame.render.Light.MAX_LIGHTS 与 dis.frag 的 MAX_LIGHTS）")
        return light

    def remove(self, light: Light) -> None:
        if light in self.lights:
            self.lights.remove(light)

    def clear(self) -> None:
        self.lights.clear()

    def __len__(self) -> int:
        return len(self.activeLights())

    # === 查询 ===

    def activeLights(self) -> list[Light]:
        """可用光源（active 且未删除，并截断到上限）。"""
        result = [light for light in self.lights
                  if getattr(light, "active", True)
                  and not getattr(light, "_deleted", False)]
        return result[:self.max_lights]

    def sun(self) -> Light | None:
        """第一盏投影阴影的方向光；没有就退回第一盏方向光。

        阴影贴图与体积渲染（god rays）都用它。
        """
        directional = [light for light in self.activeLights()
                       if light.kind == DIRECTIONAL]
        for light in directional:
            if light.cast_shadow:
                return light
        return directional[0] if directional else None

    def indexOf(self, light: Light) -> int:
        """光源在"本帧上传顺序"里的下标（着色器数组下标）；不在列表里返回 -1。"""
        for index, candidate in enumerate(self.activeLights()):
            if candidate is light:
                return index
        return -1

    def upload(self, shader) -> int:
        """把光源数组传给着色器，返回实际上传的光源数。"""
        lights = self.activeLights()
        shader.uniform1i("u_lightCount", len(lights))
        if not lights:
            # 没有光源：不上传数组（着色器会整段跳过光照，画面与旧版本一致）
            return 0
        for index, light in enumerate(lights):
            direction = light.direction
            length = glm.length(direction)
            if length > 1e-6:
                direction = direction / length
            radiance = light.radiance
            shader.uniform1i(f"u_lightKind[{index}]", light.kind)
            shader.uniform3f(f"u_lightDir[{index}]",
                             direction.x, direction.y, direction.z)
            shader.uniform3f(f"u_lightPos[{index}]", light.x, light.y, light.z)
            shader.uniform3f(f"u_lightColor[{index}]",
                             radiance.x, radiance.y, radiance.z)
            shader.uniform4f(f"u_lightParam[{index}]",
                             float(light.range), float(light.spot_cos),
                             1.0 if light.cast_shadow else 0.0,
                             float(light.specular))
            # 距离衰减：x = 指数 n（"1/dⁿ" 里的 n，默认 2 = 平方反比；1 = 线性、0.5 = 更慢）
            shader.uniform4f(f"u_lightParam2[{index}]",
                             float(light.attenuation_exponent), 0.0, 0.0, 0.0)
        return len(lights)
