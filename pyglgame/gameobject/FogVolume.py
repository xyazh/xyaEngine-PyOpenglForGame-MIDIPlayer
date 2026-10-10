"""体积雾（干冰雾 / 雾团 / 低空雾）—— **世界里的对象**，不是相机的属性。

## 为什么要有它

Phase 4/5 的雾挂在相机上（``camera.useVolumetric(...)`` + 一堆 ``camera.volumetric_*``）：

- 整张地图只能有一份，想放两片就得改引擎；
- 只能"铺满地图"或"围着原点一滩"，没法挪；
- 关掉相机就没有雾了。

Phase 6 把同一套体积散射包成 :class:`FogVolume`：**放进场景就有一个可以随便放、
可以有多个、可以移动的对象**（和 :class:`~pyglgame.render.Light.Light` 一样）：

    pond = FogVolume(size=(60.0, 2.5, 60.0), softness=16.0)
    pond.y = 0.65                              # 盒子 = 雾的范围（x±30, y -0.6~1.9, z±30）
    pond.noise = fog_noise                     # TextureVolumeNoise（可多份共享）
    pond.configure(fog_layer=True, density=1.35, edge=0.12, edge_softness=0.40)
    pond.emitters = ((-3.1, 0.85, -3.1, 2.0),)  # 喷口：**相对体积中心**的偏移

    puff = FogVolume(size=(9.0, 3.0, 9.0), softness=3.0)
    puff.x, puff.y, puff.z = 16.0, 1.6, 4.0
    puff.anchor = True                         # 噪声锚定体积 → 移动时雾跟着走

引擎侧（``RenderLayer``）会把所有 ``is_fog_volume`` 对象收集起来，在**半透明之后、
tone mapping 之前**按"远→近"依次合成 —— 顺序不需要用户操心。

## 盒子 = 雾的范围

体积是**轴对齐盒子**（``x/y/z`` 是中心，``size`` 是三个方向的长度）：

- 射线只步进盒内那一段（``u_volumeEnabled``，见 ``volumetric.frag``）；
- 四个侧面按 ``softness`` 米淡出，软边宽度用噪声扰动 → 边界是团块状而不是盒棱；
- 盒子的**上下沿就是雾层的底/顶**（:attr:`fog_bottom` / :attr:`fog_top` 是它的属性
  视图）：所以"雾顶抬高一点"就是 ``pond.fog_top += 0.3``，"雾浮在半空"就让盒子的
  下沿离开地面（上下两侧都有软边，所以底下不会是个硬切口）。
- ``anchor = False``（默认）时噪声图案固定在世界里（风把它吹过体积）；``True`` 时
  图案跟着体积走。``yaw`` 可以旋转采样（同一个噪声贴图放几份时，各转一个角度就
  不会看着像复印）。

## 生命周期 / GPU 资源

- ``start()``（渲染层在帧边界调用）：记录所在层，并让层里的相机重新评估
  "要不要可采样深度"（体积 pass 要按屏幕像素重建世界坐标）。
- ``update(dt, tps)``：推进流动相位（``paused = True`` 冻结 —— 抓图对比用）。
- ``renderVolumePass(camera, dt, fps)``：半透明之后由渲染层调用，跑自己那两趟
  （半分辨率射线步进 + 全分辨率合成），结果接到 ``camera.volumetric_texture``，
  于是 Bloom / 窗口输出自动吃到它。
- ``delete()``：释放自己的 GPU 资源（两张纹理 + 两个 FBO，1600×900 时约 14 MB）。

**每个体积一份自己的缓冲**（散射是加性 HDR 项，逐个合成才不会互相盖住），所以
体积数量直接影响显存与带宽：想放很多份就把 ``scale`` 调大（半分辨率 → 1/3 分辨率）
或调小 ``steps``。
"""

import glm
from OpenGL.GL import GL_RGBA16F

from .GameObject import GameObject
from ..render.Volumetric import Volumetric
from ..RenderGlobal import RenderGlobal


class FogVolume(GameObject):
    """一片可以随便放的体积雾（见模块文档）。

    **标记 ``is_fog_volume``**：渲染层（而不是相机）靠它认出"这是体积对象" ——
    体积对象不参与不透明/半透明几何 pass，只在半透明之后跑一次体积 pass。
    用标记而不是 ``isinstance``：测试与扩展（云层、雾墙）不必继承本类也能接进来。
    """

    is_fog_volume = True

    # === 引擎侧的参数（与 :class:`Volumetric` **同名同义**）===
    # 默认值必须与 ``Volumetric.__init__`` 的默认值一致（tests 里有断言守着）：
    # 这样"什么都不设的 FogVolume"与"什么都不设的全局雾"是同一个画面。
    density = 0.02
    height_falloff = 0.02
    extinction = 1.0
    scattering = 1.0
    ambient_scatter = 0.006
    steps = 32
    max_distance = 120.0
    scale = 0.5
    paused = False
    noise_time = 0.0
    # --- 雾层（Phase 5 那套：3D 噪声驱动的"干冰雾"）---
    fog_layer = False
    noise = None
    noise_scale = 0.06
    noise_strength = 0.9
    noise_detail = 0.45
    noise_grain = 0.15
    noise_tiling = (0.61, 0.29, 0.13)
    noise_warp = 1.0
    noise_speed = 1.0
    wind = (0.35, 0.12)
    wind2 = (-0.22, 0.30)
    edge = 0.30
    edge_softness = 0.15
    sharpen = 1.6
    emitter_power = 1.5
    fog_top_variation = 0.5
    top_softness = 0.35
    # 雾池范围（径向淡出）：世界体积用"盒子的软边"就能做出"一滩"，但这一对
    # 仍然有效 —— 兼容入口原样转发给 Volumetric，老 demo 的观感才不会变。
    fog_center = (0.0, 0.0)
    fog_spread = (0.0, 0.0)
    # --- 形状 ---
    size = glm.vec3(20.0, 4.0, 20.0)
    softness = 4.0            # 四个侧面的软边宽度（米）
    anchor = False            # 噪声是否锚定在体积上（见模块文档）
    yaw = 0.0                 # 噪声采样绕体积中心的旋转（**度**，同相机的 p/t/r）
    # --- 喷口：相对体积中心的 (x, y, z, 半径) ---
    emitters = ()

    #: :meth:`_syncRenderer` 逐帧同步的参数名（两边同名 → 不会漏、不会错位）
    RENDERER_PARAMS = (
        "density", "height_falloff", "extinction", "scattering", "ambient_scatter",
        "steps", "max_distance", "scale", "paused",
        "fog_layer", "noise_scale", "noise_strength", "noise_detail", "noise_grain",
        "noise_tiling", "noise_warp", "noise_speed", "noise_time",
        "wind", "wind2", "edge", "edge_softness", "sharpen",
        "emitter_power", "fog_top_variation", "top_softness",
        "fog_center", "fog_spread",
        # 由盒子/喷口推出来的两个（只读属性 + 换算过的世界坐标）
        "fog_bottom", "fog_top",
    )
    VEC2_PARAMS = ("wind", "wind2", "fog_center", "fog_spread")
    VEC3_PARAMS = ("noise_tiling",)

    def __new__(cls, *args, **kwargs):
        # GameObject.__new__ 只认 is_camera：这里把构造参数吃掉（同 Light 的做法），
        # 否则 FogVolume(size=...) 会把 size 传给 __new__ 直接 TypeError
        return super().__new__(cls)

    def __init__(self, size=(20.0, 4.0, 20.0), **params) -> None:
        super().__init__()
        self.size = glm.vec3(*[float(v) for v in size])
        self.renderer = None
        self._layer_cache = None
        if params:
            self.configure(**params)

    @classmethod
    def globalFog(cls, **params):
        """一个"没有边界"的体积（**兼容入口专用**）。

        ``camera.useVolumetric(...)`` 的老语义是"整张地图一层雾"：用一个大到射线
        必然在里面的盒子 + 软边 0 表示 —— 于是盒子的裁剪不生效，密度场与 Phase 4/5
        逐像素一致：
        - 纯高度雾（``fog_layer=False``）：三个轴都不限（密度只跟高度有关）
        - 雾层模式（``fog_layer=True``）：盒子的上下沿 = ``fog_bottom`` / ``fog_top``
          （于是纵向剖面、平板裁剪、云顶起伏都和老路径一样），xz 仍然不限
        """
        bottom = float(params.pop("fog_bottom", Volumetric.fog_bottom))
        top = float(params.pop("fog_top", Volumetric.fog_top))
        far = 1.0e4
        if bool(params.get("fog_layer", cls.fog_layer)):
            volume = cls(size=(far, max(top - bottom, 1e-3), far), softness=0.0)
            volume.y = (top + bottom) * 0.5
        else:
            volume = cls(size=(far, far, far), softness=0.0)
        volume.configure(**params)
        return volume

    # === 形状（盒子）===

    def center(self) -> glm.vec3:
        """体积中心（世界坐标）。"""
        return glm.vec3(float(self.x), float(self.y), float(self.z))

    def boxMin(self) -> glm.vec3:
        half = self.size * 0.5
        center = self.center()
        return glm.vec3(center.x - half.x, center.y - half.y, center.z - half.z)

    def boxMax(self) -> glm.vec3:
        half = self.size * 0.5
        center = self.center()
        return glm.vec3(center.x + half.x, center.y + half.y, center.z + half.z)

    def setSize(self, size, height: float = None, depth: float = None):
        """设置三个方向的长度（``setSize(60, 2.5, 60)`` 或 ``setSize((60, 2.5, 60))``）。"""
        if height is None and depth is None:
            self.size = glm.vec3(*[float(v) for v in size])
        else:
            self.size = glm.vec3(float(size), float(height), float(depth))
        return self

    def setBox(self, x0, x1, y0, y1, z0, z1):
        """按两个角点设置盒子（``y0/y1`` 就是雾层的底与顶）。"""
        self.x = (float(x0) + float(x1)) * 0.5
        self.y = (float(y0) + float(y1)) * 0.5
        self.z = (float(z0) + float(z1)) * 0.5
        self.size = glm.vec3(abs(float(x1) - float(x0)),
                             abs(float(y1) - float(y0)),
                             abs(float(z1) - float(z0)))
        return self

    def contains(self, x, y=None, z=None) -> bool:
        """点是否在盒子里（``(x, y, z)`` 或一个向量）。"""
        if y is None and z is None:
            point = glm.vec3(*[float(v) for v in x])
        else:
            point = glm.vec3(float(x), float(y), float(z))
        low = self.boxMin()
        high = self.boxMax()
        return bool(low.x <= point.x <= high.x and low.y <= point.y <= high.y
                    and low.z <= point.z <= high.z)

    @property
    def fog_bottom(self) -> float:
        """雾层底面 = 盒子的下沿（``camera.volumetric_fog_bottom`` 的等价物）。"""
        return float(self.y) - float(self.size.y) * 0.5

    @fog_bottom.setter
    def fog_bottom(self, value: float) -> None:
        self._setYRange(float(value), self.fog_top)

    @property
    def fog_top(self) -> float:
        """雾层顶面 = 盒子的上沿（云顶起伏围绕它上下浮动）。"""
        return float(self.y) + float(self.size.y) * 0.5

    @fog_top.setter
    def fog_top(self, value: float) -> None:
        self._setYRange(self.fog_bottom, float(value))

    def _setYRange(self, bottom: float, top: float) -> None:
        """改盒子的上下沿（保持 xz 不变）。"""
        if top - bottom < 1e-3:
            top = bottom + 1e-3
        self.size = glm.vec3(float(self.size.x), top - bottom, float(self.size.z))
        self.y = (bottom + top) * 0.5

    # === 喷口（相对体积中心）===

    @staticmethod
    def toOffsets(emitters, center=(0.0, 0.0, 0.0)) -> list:
        """把**绝对坐标**的喷口列表换算成相对 ``center`` 的偏移（纯函数，可单测）。

        ``camera.useVolumetric(emitters=...)`` 的老语义是绝对坐标，兼容入口用它换算；
        元素可以是 ``(x, y, z)`` / ``(x, y, z, r)`` / 带 ``x/y/z``（``radius``）的对象。
        """
        packed = Volumetric.packEmitters(emitters)
        cx, cy, cz = (float(v) for v in center)
        return [(e[0] - cx, e[1] - cy, e[2] - cz, e[3]) for e in packed]

    def emitterWorld(self) -> list:
        """喷口的**世界坐标**（渲染器要的就是这个 —— 见 ``Volumetric.packEmitters``）。"""
        center = self.center()
        return [(e[0] + center.x, e[1] + center.y, e[2] + center.z, e[3])
                for e in Volumetric.packEmitters(self.emitters)]

    def setEmitter(self, index: int, offset, radius: float = 1.0) -> None:
        """原地改一个喷口（相对体积中心的偏移）。"""
        offsets = [list(e) for e in Volumetric.packEmitters(self.emitters)]
        x, y, z = (float(v) for v in offset)
        offsets[int(index) % len(offsets)] = [x, y, z, float(radius)]
        self.emitters = tuple(tuple(e) for e in offsets)

    def clearEmitters(self) -> None:
        self.emitters = ()

    # === 参数 ===

    @classmethod
    def paramNames(cls) -> tuple:
        """``configure`` 能接受的名字（报错信息与文档共用这一份）。"""
        names = tuple(n for n in cls.RENDERER_PARAMS
                      if n not in ("fog_bottom", "fog_top"))
        return names + ("noise", "emitters", "size", "softness", "anchor", "yaw")

    def configure(self, **params):
        """批量改参数（名字写错**直接报错**：打错字不会静默失效）。

        可用的名字见 :meth:`paramNames`：
        :attr:`RENDERER_PARAMS` 那一组都与 :class:`Volumetric` 同名同义；
        ``noise`` 是 3D 噪声贴图；``emitters`` 是**相对体积中心**的喷口；
        ``size`` / ``softness`` / ``anchor`` / ``yaw`` 是形状与采样。
        ``fog_bottom`` / ``fog_top`` 是盒子的属性视图，直接写
        ``fog.fog_top = 2.2`` 即可。
        """
        for name, value in params.items():
            if name == "size":
                self.setSize(value)
            elif name == "softness":
                self.softness = float(value)
            elif name == "anchor":
                self.anchor = bool(value)
            elif name == "yaw":
                self.yaw = float(value)
            elif name == "noise":
                self.noise = value
            elif name == "emitters":
                self.emitters = tuple(value or ())
            elif name in self.RENDERER_PARAMS and name not in ("fog_bottom", "fog_top"):
                setattr(self, name, value)
            else:
                raise TypeError(
                    f"FogVolume 没有参数 {name!r}；可用：{self.paramNames()}")
        return self

    def _rendererValue(self, name):
        """取渲染器要的值（vec2/vec3 允许传元组、列表或 glm 向量）。"""
        value = getattr(self, name)
        if name in self.VEC2_PARAMS:
            return glm.vec2(*[float(v) for v in value])
        if name in self.VEC3_PARAMS:
            return glm.vec3(*[float(v) for v in value])
        return value

    def _syncRenderer(self, renderer):
        """把参数与形状同步给渲染器（每帧一次 → 改属性下一帧就生效）。"""
        for name in self.RENDERER_PARAMS:
            setattr(renderer, name, self._rendererValue(name))
        renderer.noise_texture = self.noise
        renderer.emitters = self.emitterWorld()
        # 形状 → 盒子：射线裁剪、四个侧面的软边、噪声锚定与旋转
        renderer.volume_enabled = True
        renderer.volume_min = self.boxMin()
        renderer.volume_max = self.boxMax()
        renderer.volume_softness = float(self.softness)
        renderer.volume_anchor = bool(self.anchor)
        renderer.volume_yaw = float(glm.radians(self.yaw))
        return renderer

    # === 生命周期 ===

    def start(self) -> None:
        super().start()
        # 先把"我所在的层"记下来：delete() 时 GameObject 已经把层引用清掉了
        self._layer_cache = self.layer
        self._notifyCameras()

    def update(self, dt: float, tps: float):
        """推进流动相位（体积 pass 由渲染层驱动，这里只走时间）。"""
        self.advance(dt)
        return super().update(dt, tps)

    def advance(self, dt: float) -> None:
        """推进流动相位（``paused = True`` 冻结 —— 抓图对比"雾在动/没动"）。"""
        if self.paused:
            return
        self.noise_time += float(dt) * float(self.noise_speed)

    def onRemoved(self) -> None:
        """从渲染层注销：释放自己的 GPU 资源，并让相机重新评估可采样深度。"""
        renderer = self.renderer
        self.renderer = None
        if renderer is not None:
            renderer.release()
        self._notifyCameras()

    def _notifyCameras(self) -> None:
        """告诉层里的相机"体积对象增减了"（帧缓冲可能要重建才有可采样深度）。"""
        layer = self._layer_cache or self.layer
        if layer is None:
            return
        for camera in tuple(getattr(layer, "cameras", ())):
            if getattr(camera, "_deleted", False):
                continue
            notify = getattr(camera, "onVolumeObjectsChanged", None)
            if notify is not None:
                notify()

    # === 渲染（由渲染层在"半透明之后、tone mapping 之前"调用）===

    def ensureRenderer(self, width: int, height: int, internal_format=None):
        """按渲染尺寸创建/重建自己的渲染器。

        每个体积一份自己的散射/合成缓冲（多个体积要逐个合成，不能共用）；尺寸跟着
        **渲染它的相机**走，所以窗口缩放后下一次 pass 会自动重建。
        """
        width = max(int(width), 1)
        height = max(int(height), 1)
        if self.renderer is None:
            self.renderer = Volumetric(
                width, height, scale=float(self.scale),
                internal_format=(GL_RGBA16F if internal_format is None
                                 else int(internal_format)))
        else:
            self.renderer.resize(width, height)
        return self.renderer

    def isVisibleTo(self, camera) -> bool:
        """粗剔除：整个盒子都在相机后面时不必跑这个 pass（着色器里另有逐像素早退）。"""
        view_of = getattr(camera, "viewMatrix", None)
        if view_of is None:
            return True
        view = view_of()
        if view is None:
            return True
        center = self.center()
        radius = float(glm.length(self.size)) * 0.5
        depth = -float((view * glm.vec4(center, 1.0)).z)   # 相机看向 -z
        return depth > -radius

    def renderVolumePass(self, camera, dt: float, fps: float) -> None:
        """跑自己那两趟（半分辨率步进 + 全分辨率合成），接到相机的最终颜色上。

        结果写进 ``camera.volumetric_texture``（:meth:`Camera.colorTexture` 会优先
        返回它）→ 后面的体积、Bloom、窗口输出自动吃到这一次散射。多个体积因此天然
        是"逐个合成"，而渲染层保证顺序是**远→近**。
        """
        if not self.active or self.steps <= 0 or not self.isVisibleTo(camera):
            return
        prepare = getattr(camera, "prepareVolumeTargets", None)
        if prepare is None:
            return
        targets = prepare()
        color_texture = int(targets[0] or 0)
        depth_texture = int(targets[1] or 0)
        if not color_texture or not depth_texture:
            # 相机的帧缓冲还没有"可采样深度"（本帧刚请求重建）→ 下一帧再来
            return
        width = int(targets[2]) if len(targets) > 2 else 1
        height = int(targets[3]) if len(targets) > 3 else 1
        renderer = self.ensureRenderer(
            width, height, getattr(camera, "internal_format", None))
        self._syncRenderer(renderer)
        render_global = RenderGlobal.instance
        sun = render_global.light_manager.sun() if render_global is not None else None
        camera.volumetric_texture = renderer.render(
            camera, color_texture, depth_texture, sun,
            getattr(camera, "shadow_map", None))
