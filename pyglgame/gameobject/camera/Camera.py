from .m.MBase import MBase
from ..GameObject import GameObject
from ...render.MSAAFrameBuffer import MSAAFrameBuffer
from ...render.RenderBuffer import RenderBuffer
from ...render.BufferBuilder import *
from ...render import RenderState
from ...render.WBOIT import WBOIT
from ...render.ShadowMap import ShadowMap
from ...render.ShadowMask2D import ShadowMask2D
from ...render.Passes import SHADOW, SHADOW2D, OBJECT_ID
from ...render.AmbientOcclusion import AmbientOcclusion
from ...render.ObjectIdBuffer import ObjectIdBuffer
from ..FogVolume import FogVolume
from ...RenderGlobal import RenderGlobal
from ...filler.Bloom import Bloom
from ...filler.FilterChain import FilterChain
from ...render.TextureStorage2D import TextureStorage2D
from ...xyazhServer.ConsoleMessage import ConsoleMessage
from OpenGL.GL import *
import glm
import math


class Camera(GameObject):
    def __new__(cls, msaa_val: int = 4):
        return super().__new__(cls, is_camera=True)

    def __init__(self, msaa_val: int = 4) -> None:
        super().__init__()
        self._msaa_val = msaa_val
        self.m: MBase = None
        self.use_bloom: bool = False
        self.bloom: Bloom = None
        self.internal_format: int = GL_RGBA16F
        self.frame_buffer: MSAAFrameBuffer = None
        self.render_buffer: RenderBuffer = None
        self.bloomed_texture: TextureStorage2D = None
        # === 滤镜链（挂在相机**最终输出**上的后处理，见 pyglgame.filler.FilterChain）===
        # 加入顺序 = 执行顺序；``use_filters`` 是整条链的开关（滤镜对象仍挂在相机上）。
        # 没挂滤镜时这条链路**零开销**：不建纹理、不多跑 pass、画面逐像素不变。
        # === 滤镜链（挂在相机**最终输出**上的后处理，见 pyglgame.filler.FilterChain）===
        # 加入顺序 = 执行顺序；``use_filters`` 是整条链的开关（滤镜对象仍挂在相机上）。
        # 没挂滤镜时这条链路**零开销**：不建纹理、不多跑 pass、画面逐像素不变。
        # （``filters`` 是只读属性，返回链上的列表；增删用 addFilter/removeFilter）
        self.filter_chain: FilterChain = None
        self.filtered_texture: TextureStorage2D = None
        self.use_filters: bool = True
        #: 本帧还没跑过链（``outputTexture()`` 会补跑一次，见那里的说明）
        self._filters_pending: bool = True
        #: 已经提示过"这一帧拿不到可采样输出"（避免每帧刷警告）
        self._filters_warned: bool = False

        # === 顺序无关透明 ===
        # True 时半透明 pass 走 WBOIT（逐像素顺序无关）；
        # False 时走 A 方案（对象级深度排序 + 关闭深度写入）
        self.use_wboit: bool = False
        self.wboit: WBOIT = None
        # WBOIT 的深度权重缩放：越大越"近处优先"（层次更接近 A 方案），
        # 越小越接近等权平均（更通透，但不同颜色的层会互相冲淡）。
        # 1/200 是 McGuire 论文里适用于单位尺度场景的经验值。
        self.wboit_depth_scale: float = 1.0 / 200.0
        # === 阴影（Phase 2：方向光的 depth-only 阴影贴图）===
        self.use_shadow: bool = False
        self.shadow_map: ShadowMap = None
        self.shadow_size: int = 2048
        self.shadow_radius: float = 45.0
        # === 2D 阴影（Phase 7：逐灯几何遮挡 → 屏幕空间遮罩）===
        # 与上面那套方向光阴影贴图**完全独立**：这里是"每盏 cast_shadow 的灯一层"，
        # 遮挡体算的是沿光方向 / 从光源径向扫出的阴影三角形（见 render.ShadowMask2D）。
        self.use_shadow2d: bool = False
        self.shadow_mask: ShadowMask2D = None
        #: 上一帧 2D 遮罩的签名（见 :meth:`Camera._shadowMaskKey`）：一样就**不用重画**
        #: —— 静止的 2D 场景这是常态，省掉"每盏投影灯 × 每个遮挡体"的重复提交
        self._shadow_key = None
        #: 上一帧"逐光源是否投影"（缓存命中时还要原样交给着色器）
        self._shadow_casting = None
        # === 环境光遮蔽（Phase 3：SSAO，半分辨率）===
        self.use_ssao: bool = False
        self.ssao: AmbientOcclusion = None
        self.ssao_strength: float = 1.0
        self.ssao_scale: float = 0.5
        self.ssao_radius: float = 1.5
        self.ssao_samples: int = 16
        # === 体积渲染（Phase 4/5 → Phase 6：雾是**世界里的对象**）===
        # 雾的参数与 GPU 资源都在 ``FogVolume``（``pyglgame/gameobject/FogVolume.py``）
        # 身上：可以放多个、可以移动、可以删。相机这边只剩下两件事：
        #   1. ``volumetric_texture``：本帧体积合成后的最终颜色（None = 没有体积跑过）。
        #      ``colorTexture()`` 会优先返回它 → Bloom / 窗口输出自动吃到体积散射。
        #      多个体积逐个接到它上面（远→近），所以它始终是"到目前为止的合成结果"。
        #   2. ``prepareVolumeTargets()``：把颜色/深度解析成可采样纹理，供体积 pass 用。
        # ``useVolumetric(...)`` 只是**兼容入口**（内部维护一个不设边界的 FogVolume）。
        self.volumetric_texture: int = None
        self.use_volumetric: bool = False
        # 兼容入口创建的"全局雾"体积（Phase 4/5 的老写法用；新代码请直接用 FogVolume）
        self.fog = None
        self.__level1 = 2
        self.__level2 = 4
        self._size_hook_registered = False
        # === Object ID / Mask（GPU Picking，见 render/ObjectIdBuffer.py）===
        # 默认关闭：打开后每帧多一趟"只写 ID"的几何 pass（拾取 / 后处理掩码要用）。
        # 可用 camera.useObjectId(True) 或在窗口相机上 RenderGlobal.enableObjectId() 打开，
        # 也可以在第一次 pickObject() 时惰性打开。
        self.use_object_id: bool = False
        self.object_id_buffer: ObjectIdBuffer = None
        #: ID pass 里是否按贴图 alpha 丢弃透明像素（默认开：ID 纹理同时当轮廓掩码）
        self.object_id_alpha_test: bool = True

    def start(self) -> None:
        super().start()
        self.render_global = RenderGlobal.instance
        self.size = RenderGlobal.instance.window.size
        self.createFrameBuffer()
        self.createWBOIT()
        self.render_buffer = RenderBuffer.getWindowRenderBuffer()
        self.bloom = None
        self.bloomed_texture = None
        # 窗口尺寸变化时重建帧缓冲/后处理，并同步投影宽高比
        if not self._size_hook_registered:
            self.size.onChange(self.onWindowSizeChanged)
            self._size_hook_registered = True
        if self.use_bloom:
            self.createBloom()

    # === 顺序无关透明（WBOIT）===

    def useWBOIT(self, active: bool = True) -> None:
        """开关 WBOIT（加权混合顺序无关透明）。start() 前后调用都可以。"""
        self.use_wboit = bool(active)
        if self.frame_buffer is None:
            # 还没 start()：开关会在创建帧缓冲时生效（depth_resolve 依赖它）
            return
        # depth_resolve 与累加缓冲都要跟着重建
        self.createFrameBuffer()
        self.createWBOIT()
        if self.use_bloom:
            self.createBloom()

    def setWBOITDepthScale(self, scale: float) -> None:
        """设置 WBOIT 的深度权重缩放（start() 前后都能调用）。

        长焦/近景的可视化场景（深度只有几十个单位）用默认 1/200 会退化成
        等权平均，把不同颜色的半透明层冲淡；调大（例如 1/20）能恢复
        "近处优先"的层次感。
        """
        self.wboit_depth_scale = float(scale)
        if self.wboit is not None:
            self.wboit.depth_scale = self.wboit_depth_scale

    def clampWboitSamples(self, samples: int) -> int:
        """把 MSAA 采样数压到 WBOIT 能承受的上限内。

        WBOIT 的 accum/reveal 是**逐采样**缓冲，采样数越高显存线性增长；而且
        它们必须和帧缓冲同采样数（要直接共用相机的深度附件），所以这里连帧
        缓冲的 MSAA 一起压。显存宽裕可以调大 ``WBOIT.MAX_SAMPLES``。
        """
        samples = int(samples)
        if not self.use_wboit:
            return samples
        limit = WBOIT.MAX_SAMPLES
        if limit and samples > limit:
            ConsoleMessage.printWarning(
                f"WBOIT 开启：MSAA {samples}x 调整为 {limit}x"
                "（accum/reveal 显存随采样数线性增长，可调 WBOIT.MAX_SAMPLES）")
            return limit
        return samples

    def createWBOIT(self) -> None:
        if not self.use_wboit:
            if self.wboit is not None:
                self.wboit.release()
                self.wboit = None
            return
        if self.wboit is not None:
            self.wboit.release()
        samples = self.frame_buffer.samples if self.frame_buffer is not None else 1
        self.wboit = WBOIT(self.size.w, self.size.h, samples=samples,
                           composite_format=self.internal_format)
        self.wboit.depth_scale = self.wboit_depth_scale

    def colorTexture(self) -> int:
        """本帧最终颜色纹理（体积散射 > WBOIT 合成 > MSAA 解析纹理）。"""
        if self.volumetric_texture:
            # 体积散射已经把它作为输入合成过了，所以优先级最高
            return self.volumetric_texture
        if self.use_wboit and self.wboit is not None:
            return self.wboit.color_texture
        return self.frame_buffer.getTexture()

    def wboitDepthParams(self):
        """WBOIT 权重用的 (是否正交, near, far)。

        near/far 退化时（相等、NaN、透视下 near<=0）回退成"直接读 NDC z"，
        否则着色器里的线性化会除零产生 NaN 权重，把整帧打成黑/花屏。
        """
        params = getattr(self.m, "perspective_params", None)
        ortho = False
        if params is None:
            params = getattr(self.m, "ortho_params", None)
            ortho = True
        if params is not None:
            near, far = float(params.near), float(params.far)
            if (far > near and (ortho or near > 0)
                    and math.isfinite(near) and math.isfinite(far)):
                return (ortho, near, far)
        # MSimple2D 等：投影是单位矩阵，NDC z 就等于视空间 z
        return (True, -1.0, 1.0)

    def applyWBOITUniforms(self) -> None:
        """把深度权重参数写给 dis 着色器（每帧调用）。"""
        if not self.use_wboit or self.wboit is None:
            return
        shader = RenderGlobal.instance.dis_shader
        if shader is None:
            return
        shader.use()
        ortho, near, far = self.wboitDepthParams()
        shader.uniform1f("u_near", float(near))
        shader.uniform1f("u_far", float(far))
        shader.uniform1f("u_ortho", 1.0 if ortho else 0.0)
        shader.uniform1f("u_depthScale", float(self.wboit.depth_scale))

    def createBloom(self) -> None:
        if self.bloom is not None:
            self.bloom.release()
            self.bloom = None
        self.bloom = Bloom(self.size.w, self.size.h,
                           level1=self.__level1, level2=self.__level2,
                           internal_format=self.internal_format)
        # 输入必须是本帧最终颜色（WBOIT 开启时是合成结果）
        self.bloom.sefTexture(self.colorTexture(), self.internal_format)
        self.bloomed_texture = None

    def onWindowSizeChanged(self, width, height) -> None:
        """窗口尺寸变化回调（由 Size.onChange 触发）。"""
        if not self.started or self.frame_buffer is None:
            return
        self.frame_buffer.resize(int(width), int(height))
        self.createWBOIT()
        buffer = getattr(self, "object_id_buffer", None)
        if buffer is not None:
            # ID / Mask 缓冲跟随窗口重建（否则拾取坐标会与画面错位）
            buffer.resize(int(width), int(height))
        if self.ssao is not None:
            self.ssao.resize(int(width), int(height))
        if self.m is not None and hasattr(self.m, "updateAspectFromSize"):
            self.m.updateAspectFromSize(width, height)
        # 滤镜链的中间纹理按输入尺寸缓存：这里先丢掉，下一帧按新尺寸重建
        self.filtered_texture = None
        if self.filter_chain is not None:
            self.filter_chain.resize(int(width), int(height))
        if self.use_bloom:
            self.createBloom()

    def onRemoved(self) -> None:
        """从渲染层注销时释放自己持有的 GPU 资源。"""
        if self._size_hook_registered and getattr(self, "size", None) is not None:
            self.size.removeOnChange(self.onWindowSizeChanged)
            self._size_hook_registered = False
        if self.bloom is not None:
            self.bloom.release()
            self.bloom = None
        self.bloomed_texture = None
        if self.filter_chain is not None:
            # 只释放链的中间纹理；滤镜对象是调用方持有的，这里不动它们
            self.filter_chain.release()
        self.filtered_texture = None
        if self.wboit is not None:
            self.wboit.release()
            self.wboit = None
        if self.shadow_map is not None:
            self.shadow_map.release()
            self.shadow_map = None
        if self.shadow_mask is not None:
            self.shadow_mask.release()
            self.shadow_mask = None
        if self.ssao is not None:
            self.ssao.release()
            self.ssao = None
        if self.fog is not None:
            # 兼容入口那个"全局雾"体积是本相机的：一起注销（它同时会释放 GPU 资源、
            # 并通知层里其它相机重新评估"要不要可采样深度"）
            self.fog.delete()
            self.fog = None
        if self.frame_buffer is not None:
            self.frame_buffer.cleanup()
        buffer = getattr(self, "object_id_buffer", None)
        if buffer is not None:
            buffer.cleanup()
            self.object_id_buffer = None

    def delete(self) -> None:
        # GameObject.delete() 会回调 onRemoved()（那里统一释放本相机持有的 GPU 资源），
        # 所以这里不需要再手动调一次（原来重复释放靠各 release 的幂等性兜着）
        super().delete()

    def setProjection(self, m: MBase) -> None:
        self.m = m

    # === 顺序无关透明（A：对象级深度排序 / B：WBOIT）所需的基础能力 ===

    def viewMatrix(self):
        """当前视图矩阵（用于排序与深度权重）；拿不到时返回 None。"""
        m = self.m
        if m is None:
            return None
        view = getattr(m, "view", None)
        if view is None:
            view = getattr(m, "_view", None)
        return view

    def depthOf(self, obj) -> float:
        """对象在相机空间的深度（**值越大越远**）。

        ``MBase`` 子类都有视图矩阵（MPerspective/MOrthographic 有公开的
        ``view``，MSimple2D 只有平移）。拿不到矩阵时退化为到相机位置的距离。
        """
        view = self.viewMatrix()
        if view is not None:
            try:
                p = view * glm.vec4(float(obj.x), float(obj.y), float(obj.z), 1.0)
                # 相机看向 -z：取 -z 作为"距离"，前向为正
                return -float(p.z)
            except Exception:
                pass
        dx = float(obj.x) - float(self.x)
        dy = float(obj.y) - float(self.y)
        dz = float(obj.z) - float(self.z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def beginTransparent(self) -> None:
        """进入半透明 pass。

        - WBOIT 开启：把不透明结果解析成纹理（颜色+深度），切到累加缓冲，
          由 weighted-blended 做到逐像素**顺序无关**
        - 否则：保留混合模式、关闭深度写入，配合对象级深度排序（A 方案）
        """
        if self.use_wboit and self.wboit is not None:
            RenderGlobal.instance.oit_pass = True
            self.applyWBOITUniforms()
            frame_buffer = self.frame_buffer
            # 不透明画面作为合成底图（MSAA 时先解析成纹理）
            frame_buffer.resolveColorNow()
            # 深度附件直接共用相机的（MSAA 时是多重采样深度 renderbuffer）
            self.wboit.attachDepth(frame_buffer.depth_texture_id,
                                   frame_buffer.depth_is_renderbuffer)
            self.wboit.bind()
            RenderGlobal.instance.using_shader.uniform1i(
                "fuc", RenderGlobal.instance.passFuc(False))
            return
        RenderState.override(depth_write=False)

    def endTransparent(self) -> None:
        """退出半透明 pass。"""
        if self.use_wboit and self.wboit is not None:
            RenderGlobal.instance.oit_pass = False
            self.wboit.resolve(self.frame_buffer.getTexture())
            # WBOIT 直接改过 per-draw-buffer 混合与深度写入掩码，先作废缓存，
            # 再把引擎默认状态真正下发一遍：否则 depthMask 会一直停在 FALSE，
            # 下一帧 drawStart() 的 glClear(GL_DEPTH_BUFFER_BIT) 就清不掉深度
            # （深度清除受 glDepthMask 影响），上一帧深度残留会把不透明物体
            # 自己剔除掉。
            RenderState.invalidate()
            RenderState.setDefaults()
            return
        RenderState.setDefaults()

    # === 阴影（Phase 2）===

    def useShadow(self, active: bool = True, size: int = 2048,
                  radius: float = 45.0) -> None:
        """开关方向光阴影贴图（start() 前后都能调用）。

        ``radius`` 是正交阴影区域半径：越小阴影越精细但覆盖范围越小。
        """
        self.use_shadow = bool(active)
        self.shadow_size = int(size)
        self.shadow_radius = float(radius)
        if not self.use_shadow:
            if self.shadow_map is not None:
                self.shadow_map.release()
                self.shadow_map = None
            return
        # 还没 start()：此刻没有 GL 上下文，不能建纹理。
        # 真正创建推迟到第一帧的 renderShadowMaps()（与 useBloom 的 started 约定一致）
        if self.started or self.frame_buffer is not None:
            self._ensureShadowMap()

    def _ensureShadowMap(self) -> ShadowMap:
        """按需创建/重建阴影贴图（尺寸或半径变化时重建）。"""
        if (self.shadow_map is not None
                and self.shadow_map.size == self.shadow_size
                and self.shadow_map.ortho_radius == self.shadow_radius):
            return self.shadow_map
        if self.shadow_map is not None:
            self.shadow_map.release()
        self.shadow_map = ShadowMap(size=self.shadow_size,
                                   ortho_radius=self.shadow_radius)
        return self.shadow_map

    def renderShadowMaps(self, opaque_objects, dt: float, fps: float) -> None:
        """阴影 pass：把不透明物体用 ``fuc = SHADOW`` 重画进 depth-only 贴图。

        由 :meth:`RenderLayer.render` 在写不透明颜色**之前**调用（顺序由
        ``tests/test_engine.py::RenderPassOrderTest`` 锁住）。
        只画不透明物体：半透明物体的遮挡关系本身就与顺序相关，让它们投影
        意义不大（而且它们所在的批次通常没有法线）。
        """
        if not self.use_shadow or self.m is None:
            return
        render_global = RenderGlobal.instance
        shader = render_global.using_shader
        if shader is None:
            return
        sun = render_global.light_manager.sun()
        if sun is None or not sun.cast_shadow:
            return
        shadow_map = self._ensureShadowMap()
        shadow_map.updateLightSpaceMatrix(self, sun)
        # 必须先上传光空间矩阵再绘制：阴影 pass 的顶点着色器要用它
        shadow_map.uploadLightSpace(shader)
        shadow_map.begin()
        shader.uniform1i("fuc", SHADOW)
        # 隐形遮挡体（ShadowCaster2D）靠这个标志区分"颜色 pass 什么都不画 / 阴影 pass 画体积"
        render_global.shadow_pass = True
        try:
            for obj in opaque_objects:
                obj.render(dt, fps)
        finally:
            render_global.shadow_pass = False
            shadow_map.end()
        # 恢复主 pass 的 fuc（不透明 = DIS/WID），再把阴影贴图交给着色器
        shader.uniform1i("fuc", render_global.passFuc(False))
        shadow_map.upload(shader, render_global.light_manager.indexOf(sun))

    # === 2D 阴影（Phase 7：逐灯几何遮挡，见 docs/2D光照与阴影.md）===

    def useShadow2D(self, active: bool = True) -> "Camera":
        """开关 2D 阴影（几何遮挡遮罩）。``start()`` 前后都能调用。

        打开之后：场景里每个 ``cast_shadow=True`` 的光源（点光 / 平行光 / 聚光都行）
        都会被 :class:`~pyglgame.gameobject.ShadowCaster2D.ShadowCaster2D` 挡住 ——
        物体背后没有光。GPU 资源推迟到第一帧的 :meth:`renderShadowMasks2D`
        （与 ``useShadow`` / ``useBloom`` 的约定一致：``start()`` 之前没有 GL 上下文）。
        """
        self.use_shadow2d = bool(active)
        if not self.use_shadow2d and self.shadow_mask is not None:
            self.shadow_mask.release()
            self.shadow_mask = None
        self._shadow_key = None                     # 遮罩没了 / 重开了：缓存作废
        self._shadow_casting = None
        return self

    def _ensureShadowMask(self) -> ShadowMask2D:
        """按需创建遮罩（窗口尺寸变了就重建：遮罩是**屏幕空间**的，必须与主画面同尺寸）。"""
        width, height = int(self.size.w), int(self.size.h)
        if (self.shadow_mask is not None
                and (self.shadow_mask.width, self.shadow_mask.height) == (width, height)):
            return self.shadow_mask
        if self.shadow_mask is not None:
            self.shadow_mask.release()
        self.shadow_mask = ShadowMask2D(width, height)
        self._shadow_key = None                     # 新遮罩是空的：必须重画一遍
        return self.shadow_mask

    def _shadowMaskKey(self, lights, casters) -> tuple:
        """这一帧 2D 遮罩的"长相"签名：视口 / 相机（位置、缩放、旋转）/ 每盏灯 / 每个遮挡体。

        遮罩是**屏幕空间**的，所以相机一动就得重画；灯或遮挡体动了同理。只用来判断
        "上一帧那张遮罩这一帧还能不能用"（见 :meth:`renderShadowMasks2D`）。
        """
        from ..ShadowCaster2D import ShadowCaster2D

        model = self.m

        def matrixKey(matrix):
            """glm 矩阵 → 可比较的 tuple（认不出来就 ``None``）。"""
            try:
                return tuple(round(float(value), 6) for column in matrix
                             for value in column)
            except TypeError:
                return None

        light_keys = tuple((bool(light.cast_shadow),
                            ShadowCaster2D._lightSignature(light)) for light in lights)
        caster_keys = tuple(
            (id(caster), bool(getattr(caster, "cast_shadow", False)),
             bool(getattr(caster, "active", True)),
             round(float(caster.x), 6), round(float(caster.y), 6),
             round(float(getattr(caster, "z", 0.0)), 6),
             round(float(caster.r), 6), round(float(caster.length), 4),
             tuple(caster.points))
            for caster in casters)
        return (int(self.size.w), int(self.size.h),
                round(float(self.x), 6), round(float(self.y), 6),
                round(float(self.z), 6), round(float(self.r), 6),
                round(float(getattr(model, "zoom", 1.0)), 6),
                round(float(getattr(model, "roll", 0.0)), 6),
                # 投影 / 视图矩阵也进来：``setBySize`` / NDC 直通 / 保高改宽 这些换法
                # 不一定改 zoom / roll，但会改屏幕空间遮罩
                matrixKey(getattr(model, "projection", None)),
                matrixKey(getattr(model, "view", None)),
                light_keys, caster_keys)

    def renderShadowMasks2D(self, opaque_objects, dt: float, fps: float) -> None:
        """2D 阴影 pass：给每盏 ``cast_shadow`` 的光源渲染一张屏幕空间遮挡遮罩。

        由 :meth:`RenderLayer.render` 在写不透明颜色**之前**调用（紧跟在 3D 的
        :meth:`renderShadowMaps` 之后，顺序由 ``tests/test_engine.py`` 锁住）。

        每盏投影灯一层（层号 = 光源下标，与 ``dis.frag`` 的数组同宽）：清成 1.0
        （全可见）之后，让场景里的遮挡体把"阴影三角形"画成 0。遮挡体是**世界坐标**
        画进去的（模型矩阵复位成单位阵），用的就是当前相机 —— 所以遮罩与主画面
        逐像素对齐，采样端只要 ``gl_FragCoord.xy / win_wh``。
        """
        if not self.use_shadow2d or self.m is None:
            return
        render_global = RenderGlobal.instance
        shader = render_global.using_shader
        if shader is None:
            return
        lights = render_global.light_manager.activeLights()
        casters = [obj for obj in opaque_objects
                   if getattr(obj, "is_shadow_caster", False)
                   and getattr(obj, "cast_shadow", False)]
        # 没有遮挡体、或者没有一盏灯要投影：整个 pass 都不用跑（也就不用上传遮罩）
        if not casters or not any(light.cast_shadow for light in lights):
            return
        mask = self._ensureShadowMask()
        key = self._shadowMaskKey(lights, casters)
        if key == self._shadow_key:
            # 签名没变 → 上一帧那张遮罩这一帧仍然是对的（静止场景是常态）：
            # 直接复用，省掉"每盏投影灯：换层 + 全屏 clear + 把每个遮挡体再画一遍"。
            # 每帧仍然要把它交给着色器 —— ``resetShadow2DUniforms`` 会把标志清回全 0。
            shader.uniform1i("fuc", render_global.passFuc(False))
            mask.upload(shader, self._shadow_casting)
            return
        mask.begin()
        shader.uniform1i("fuc", SHADOW2D)
        casting = [False] * len(lights)
        from ..ShadowCaster2D import ShadowCaster2D

        render_global.shadow2d_pass = True
        try:
            for index, light in enumerate(lights):
                if not light.cast_shadow:
                    continue
                mask.beginLayer(index)
                # 遮罩里的顶点是**世界坐标** → 模型矩阵保持"当前的全局值"不动（几个矩阵是
                # 整体世界坐标系的调节，谁都不该在对象里覆盖它）。一层上传一次 uniform 就够：
                # 每个遮挡体各算一遍是这条路径最大的一笔 Python 开销
                # （``updateGlobalUniforms`` 每次 ~0.2ms，4 盏灯 × 7 个遮挡体就是 8ms/帧）
                render_global.updateGlobalUniforms()
                render_global.shadow2d_matrix_ready = True
                render_global.shadow2d_light = light
                for caster in casters:
                    caster.render(dt, fps)
                casting[index] = True
        finally:
            render_global.shadow2d_pass = False
            render_global.shadow2d_matrix_ready = False
            render_global.shadow2d_light = None
            mask.end()
        # 恢复主 pass 的 fuc，再把遮罩与"逐光源是否投影"交给着色器
        shader.uniform1i("fuc", render_global.passFuc(False))
        mask.upload(shader, casting)
        self._shadow_key = key
        self._shadow_casting = casting

    # === Object ID / Mask（GPU Picking）===

    def useObjectId(self, active: bool = True) -> "Camera":
        """开关 Object ID / Mask pass（``start()`` 前后都能调用）。

        打开之后每一帧会多跑一趟"只写物体 ID"的几何 pass（``fuc = OBJECT_ID``），
        结果写进 :attr:`object_id_buffer`；``RenderGlobal.pickObject()`` 与后处理
        掩码都从这里取数据。GPU 资源推迟到第一帧的 :meth:`renderObjectIds` 创建
        （与 ``useShadow`` / ``useBloom`` 的约定一致：``start()`` 之前没有 GL 上下文）。
        """
        self.use_object_id = bool(active)
        if not self.use_object_id:
            if self.object_id_buffer is not None:
                self.object_id_buffer.cleanup()
                self.object_id_buffer = None
            return self
        if self.started and self.frame_buffer is not None:
            self._ensureObjectIdBuffer()
        return self

    def _ensureObjectIdBuffer(self) -> ObjectIdBuffer:
        """按需创建 / 重建 ID 缓冲（尺寸跟随窗口）。"""
        width = int(getattr(self.size, "w", 0) or 0)
        height = int(getattr(self.size, "h", 0) or 0)
        if width <= 0 or height <= 0:
            return None
        if self.object_id_buffer is None:
            self.object_id_buffer = ObjectIdBuffer(width, height)
        else:
            self.object_id_buffer.resize(width, height)
        return self.object_id_buffer

    def objectIdTexture(self) -> int:
        """本相机的 Object ID / Mask 纹理（没开 / 还没渲染过 → 0）。"""
        if self.object_id_buffer is None:
            return 0
        return self.object_id_buffer.texture()

    def renderObjectIds(self, objects, dt: float, fps: float) -> None:
        """Object ID pass：把物体 ID 画进 :attr:`object_id_buffer`。

        由 :meth:`RenderLayer.render` 在 ``camera.renderEnd()`` **之后**调用。
        复用主着色器（``fuc = OBJECT_ID``）与相机已经上传的 view/projection，
        只改 fuc 和逐物体的 ``u_objectId`` —— 不新建 program、不重传矩阵。
        深度测试打开 → 重叠物体只留下当前摄像机看到的最前面的那个（需求 3）。
        """
        if not self.use_object_id or self.m is None:
            return
        render_global = RenderGlobal.instance
        # 显式用主着色器（dis）：相机在 renderStart 里已经把 view/projection 上传给它，
        # ID pass 直接复用；也避免被 Bloom / 滤镜链留在 using_shader 上的别的 program 影响。
        shader = getattr(render_global, "dis_shader", None)
        if shader is None:
            shader = render_global.using_shader
        if shader is None:
            return
        shader.use()
        buffer = self._ensureObjectIdBuffer()
        if buffer is None:
            return
        buffer.bind()
        render_global.id_pass = True
        RenderState.override(RenderState.BLEND_NONE, depth_write=True,
                             depth_test=True)
        buffer.clear()
        shader.uniform1f("u_objectIdAlphaTest",
                         1.0 if self.object_id_alpha_test else 0.0)
        shader.uniform1i("fuc", OBJECT_ID)
        try:
            for obj in objects:
                if not getattr(obj, "active", True):
                    continue
                shader.uniform1f("u_objectId",
                                 float(getattr(obj, "object_id", 0)))
                obj.render(dt, fps)
        finally:
            render_global.id_pass = False
            buffer.unbind()
            shader.uniform1i("fuc", render_global.passFuc(False))
            RenderState.setDefaults()
        render_global._object_id_camera = self

    # === 环境光遮蔽（Phase 3：SSAO）===

    def useSSAO(self, active: bool = True, strength: float = 1.0,
                radius: float = 1.5, scale: float = 0.5,
                samples: int = 16) -> None:
        """开关屏幕空间环境光遮蔽（start() 前后都能调用）。

        - ``strength``：AO 影响强度（0 = 保留 pass 但不压暗）
        - ``radius``：采样半径（世界单位），按场景尺度调大调小
        - ``scale``：AO 缓冲的分辨率比例（0.5 = 半分辨率）
        - ``samples``：半球采样数（上限 16，见 AmbientOcclusion.MAX_KERNEL）

        需要可采样的深度纹理，所以会重建帧缓冲（与 useWBOIT 同样的做法）。
        """
        self.use_ssao = bool(active)
        self.ssao_strength = float(strength)
        self.ssao_radius = float(radius)
        self.ssao_scale = float(scale)
        self.ssao_samples = int(samples)
        if not self.started:
            # 还没 start()：没有 GL 上下文，等 start()/第一帧再建
            return
        # depth_resolve 依赖 use_ssao，必须重建帧缓冲
        self.createFrameBuffer()
        self.createWBOIT()
        if self.use_ssao:
            self._ensureAmbientOcclusion()
        elif self.ssao is not None:
            self.ssao.release()
            self.ssao = None
        if self.use_bloom:
            self.createBloom()

    def _ensureAmbientOcclusion(self):
        """按需创建 SSAO（尺寸/半径变化时同步参数）。"""
        if not self.use_ssao:
            return None
        if self.ssao is None:
            self.ssao = AmbientOcclusion(self.size.w, self.size.h,
                                        scale=self.ssao_scale,
                                        samples=self.ssao_samples,
                                        radius=self.ssao_radius)
        else:
            self.ssao.radius = self.ssao_radius
        return self.ssao

    def renderOcclusion(self, opaque_objects, dt: float, fps: float) -> None:
        """SSAO pass：深度预 pass → 解析深度 → 清深度 → 算 AO → 接给主着色器。

        由 :meth:`RenderLayer.render` 在不透明绘制**之前**调用。
        第 1、2 步产出的"可采样深度 + 视空间重建"也正是体积渲染需要的输入，
        所以 Phase 4 能直接复用这条链路。
        """
        if not self.use_ssao or self.m is None or self.frame_buffer is None:
            return
        render_global = RenderGlobal.instance
        shader = render_global.using_shader
        if shader is None:
            return
        ssao = self._ensureAmbientOcclusion()
        if ssao is None:
            return

        frame_buffer = self.frame_buffer
        # 1) 深度预 pass：只写深度（颜色写入关闭）。复用 DIS 路径，不需要新 fuc
        glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE)
        try:
            for obj in opaque_objects:
                obj.render(dt, fps)
        finally:
            glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
        # 2) 深度解析成可采样纹理（MSAA 才有这一步；单采样时深度本身就是纹理）
        frame_buffer.resolveDepthNow()
        depth_texture = frame_buffer.getDepthTexture()
        # 3) **先把相机帧缓冲绑回来**再清深度：
        #    深度解析的 blit 会把 draw 绑定留在解析 FBO 上，此时 glClear 清掉的是
        #    解析纹理而不是相机的深度缓冲（会把 SSAO 的输入清成 1.0）。
        #    清深度的原因：不透明 pass 用 GL_LESS，深度不清掉的话刚画过的物体会被自己剔除；
        #    清深度还受 glDepthMask 影响，这里强制打开，避免任何残留状态。
        frame_buffer.bind()
        glViewport(0, 0, frame_buffer.width, frame_buffer.height)
        glDepthMask(GL_TRUE)
        glClear(GL_DEPTH_BUFFER_BIT)
        # 4) 算 AO + 模糊；AAO 会切自己的 FBO，结束后再把相机帧缓冲绑回来画颜色
        ao_texture = ssao.compute(self, depth_texture)
        frame_buffer.bind()
        glViewport(0, 0, frame_buffer.width, frame_buffer.height)
        if ao_texture:
            ssao.upload(shader, self.ssao_strength, True)
        else:
            shader.uniform1i("u_aoEnabled", 0)

    # === 体积渲染（Phase 4/5 → Phase 6：雾是**世界里的对象**）===

    def useVolumetric(self, active: bool = True, **params) -> None:
        """（**兼容入口**）开关"相机自带的全局雾"。

        Phase 4/5 的雾参数挂在相机上（``camera.volumetric_*``）。Phase 6 把雾做成了
        :class:`~pyglgame.gameobject.FogVolume.FogVolume` —— **世界里的对象**：可以放
        很多份、可以随便放、可以移动、有自己的盒子形状。这个方法留下只是为了
        **老代码不用改**：

        - 它内部维护一个**不设边界**的体积（``self.fog``，见
          :meth:`~pyglgame.gameobject.FogVolume.FogVolume.globalFog`），参数原样
          转发 → 画面与 Phase 4/5 一致；
        - ``self.use_volumetric`` 仍然表示"这台相机要跑体积 pass"（于是它会要求
          帧缓冲有可采样深度）；
        - ``emitters`` 的老语义是**绝对坐标**，这里换算成"相对体积中心"的偏移；
        - 之后直接改 ``self.fog`` 的属性也行（下一帧生效，和改 ``volumetric_*`` 一样）。

        新代码请直接用 FogVolume：

            fog = FogVolume(size=(60.0, 2.5, 60.0), softness=16.0)
            fog.configure(fog_layer=True, noise=noise, density=1.35)

        ``params`` 能写的名字 = :meth:`FogVolume.paramNames`（没写的用默认值，
        与老 ``useVolumetric`` 的默认值一一对应）。
        """
        self.use_volumetric = bool(active)
        params = dict(params)
        bottom = params.pop("fog_bottom", None)
        top = params.pop("fog_top", None)
        # 老语义：喷口是绝对坐标；FogVolume 里存的是相对体积中心的偏移
        emitters = params.pop("emitters", None)
        if self.fog is None:
            if bottom is not None:
                params["fog_bottom"] = bottom
            if top is not None:
                params["fog_top"] = top
            self.fog = FogVolume.globalFog(**params)
        else:
            self.fog.configure(**params)
            # 只有雾层模式的上下沿有意义（老路径里纯高度雾根本不看这两个值）
            if self.fog.fog_layer:
                if bottom is not None:
                    self.fog.fog_bottom = float(bottom)
                if top is not None:
                    self.fog.fog_top = float(top)
        if emitters is not None:
            self.fog.emitters = tuple(
                FogVolume.toOffsets(emitters, self.fog.center()))
        self.fog.setActive(self.use_volumetric)
        if not self.use_volumetric:
            # 立刻作废上一帧的合成结果：否则本帧会去采样已经释放的纹理（全黑）
            self.volumetric_texture = None
        if not self.started:
            return
        # 可采样深度的需求可能变了（开/关雾）→ 按需重建帧缓冲
        self.onVolumeObjectsChanged()

    def hasFogVolumes(self) -> bool:
        """本相机所在的渲染层里有没有"活着的"体积对象（FogVolume）。"""
        layer = self.layer
        if layer is None:
            return False
        return any(getattr(obj, "is_fog_volume", False) and obj.active
                   for obj in layer.objects)

    def needsDepthResolve(self) -> bool:
        """是否需要"可采样深度"（SSAO / **体积对象**要按屏幕像素重建世界坐标）。"""
        return bool(self.use_ssao or self.use_volumetric or self.hasFogVolumes())

    def onVolumeObjectsChanged(self) -> None:
        """层里的体积对象增减（或雾开关变化）：可采样深度的需求可能变了。

        体积 pass 要按屏幕像素重建世界坐标，所以帧缓冲必须有**可采样深度**；
        而这个需求只在 ``useVolumetric`` / ``FogVolume.start()`` /
        ``FogVolume.delete()`` 时变化 —— 由它们调这里**按需重建**一次
        （需求没变就什么都不做，不会每帧重建帧缓冲）。
        """
        if not self.started or self.frame_buffer is None or self._deleted:
            return
        if bool(self.frame_buffer.depth_resolve) == bool(self.needsDepthResolve()):
            return
        self.createFrameBuffer()
        self.createWBOIT()
        if self.use_bloom:
            self.createBloom()

    def prepareVolumeTargets(self):
        """体积 pass 的输入：解析出**可采样**的颜色/深度，并把相机帧缓冲绑回来。

        返回 ``(颜色纹理, 深度纹理, 宽, 高)``；如果这个帧缓冲没有可采样深度
        （MSAA 且没开 ``depth_resolve``）就返回 ``(0, 0, 0, 0)`` 并请求一次重建
        （下一帧就能跑）—— 调用方（FogVolume 的体积 pass）直接跳过这一帧即可，
        不要报错：窗口缩放/开关雾的那一帧正好会走到这里。
        """
        frame_buffer = self.frame_buffer
        if frame_buffer is None:
            return (0, 0, 0, 0)
        depth_texture = frame_buffer.getDepthTexture()
        if not depth_texture:
            self.onVolumeObjectsChanged()
            return (0, 0, 0, 0)
        # 颜色与深度都要**可采样**：MSAA 时先解析。解析的 blit 会把 draw 绑定留在
        # 解析 FBO 上，所以立刻把相机帧缓冲绑回来（否则后续绘制会落到解析纹理上，
        # 这正是 SSAO 那条链路踩过的坑）。
        frame_buffer.resolveColorNow()
        frame_buffer.resolveDepthNow()
        frame_buffer.bind()
        glViewport(0, 0, frame_buffer.width, frame_buffer.height)
        return (self.colorTexture(), depth_texture, frame_buffer.width,
                frame_buffer.height)

    def createFrameBuffer(self) -> None:
        # 注意：WBOIT 直接共用本帧缓冲的深度附件（MSAA 时是深度 renderbuffer，
        # 单采样时是深度纹理），因此不需要 depth_resolve 的额外解析纹理。
        # SSAO / 体积渲染需要**可采样**深度：MSAA 时才会多建一张深度解析纹理
        # 并每帧 blit 一次（不开这两项就不付这个成本）。
        self.frame_buffer = MSAAFrameBuffer(
            self.size.w, self.size.h, use_depth=True,
            samples=self.clampWboitSamples(self._msaa_val),
            depth_resolve=self.needsDepthResolve(),
            internal_format=self.internal_format)

    def beginFrame(self) -> None:
        """帧开始：作废上一帧的中间结果（目前是体积散射合成后的颜色纹理）。

        由 :meth:`RenderLayer.render` 在 ``renderStart()`` **之前**调用。
        独立成方法的原因：demo 经常覆盖 ``renderStart()`` 而不调用 ``super()``，
        放在 ``renderStart`` 里的复位会失效 —— 那样 ``volumetric_texture`` 会一直
        指向已经释放的纹理，窗口输出变成全黑（踩过这个坑）。
        """
        self.volumetric_texture = None
        # 滤镜链的结果同理：上一帧的中间纹理可能已经释放 / 换了尺寸
        self.filtered_texture = None
        # 本帧还没跑过链（有些 demo 覆盖了 renderEnd() 不调 super，靠这个兜底）
        self._filters_pending = True

    def renderStart(self) -> None:
        # 双保险：直接调用 renderStart()（不走 RenderLayer）的代码也会复位
        self.beginFrame()
        if self.m is not None:
            self.m.setCameraPosAndPYR(
                self.x, self.y, self.z, self.p, self.t, self.r)
            self.m.setCameraUniforms()
        self.frame_buffer.drawStart()

    def renderEnd(self) -> None:
        self.frame_buffer.drawEnd()
        if self.use_bloom and self.bloom is not None:
            if self.volumetric_texture:
                # 体积散射把最终颜色写进了**自己的**纹理（不是帧缓冲的），而 Bloom 的输入
                # 只在 createBloom()（start / 缩放）时绑过一次 → 这里指过去，否则 Bloom
                # 读到的是"没有雾"的画面（雾只在 Bloom 里少一层，肉眼很难发现，所以踩过）。
                self.bloom.sefTexture(self.volumetric_texture, self.internal_format)
            self.bloomed_texture = self.bloom.bloom()
        # 最后一道：挂在相机输出上的滤镜链（Bloom / 体积散射之后，窗口输出之前）。
        # 没挂滤镜时它什么都不做（不建纹理、不跑 pass、画面不变）。
        self.applyFilters()

    # === 滤镜链（挂在相机最终输出上的后处理）===

    @property
    def filters(self) -> list:
        """当前链上的滤镜（顺序 = 执行顺序）。

        只读视图：增删请用 :meth:`addFilter` / :meth:`removeFilter` /
        :meth:`setFilters`（避免相机和链各存一份、慢慢跑偏）。
        """
        if self.filter_chain is None:
            return []
        return self.filter_chain.filters

    def addFilter(self, filler, index: int = None):
        """把一个滤镜挂到本相机的输出上（返回它，方便链式调用）。

        .. code-block:: python

            camera.addFilter(GrayscaleFilter())      # 第 1 个跑
            camera.addFilter(EdgeFilter("sobel"))    # 吃到灰度之后的结果

        ``index`` 给定时插到指定位置（顺序 = 执行顺序）。滤镜对象由调用方持有 ——
        同一个滤镜实例可以挂在多台相机上，但**不能**同时挂在同一条链里两次
        （第二次会覆盖第一次的结果，没有意义）。
        """
        self._ensureFilterChain()
        if index is None:
            return self.filter_chain.add(filler)
        return self.filter_chain.insert(index, filler)

    def setFilters(self, *filters) -> None:
        """替换整条链（按参数顺序执行）。``camera.setFilters()`` 等于清空。"""
        self._ensureFilterChain()
        self.filter_chain.clear()
        for filler in filters:
            self.filter_chain.add(filler)

    def removeFilter(self, filler) -> bool:
        """摘掉一个滤镜；不在链里则返回 False。"""
        if self.filter_chain is None:
            return False
        return self.filter_chain.remove(filler)

    def clearFilters(self) -> None:
        """摘掉全部滤镜（中间纹理留着，下次挂上不用重建）。"""
        if self.filter_chain is not None:
            self.filter_chain.clear()

    def hasFilters(self) -> bool:
        return self.filter_chain is not None and bool(self.filter_chain)

    def useFilters(self, active: bool = True) -> None:
        """整条链的开关：滤镜对象仍挂在相机上，只是这一帧不跑。

        和 ``useBloom(False)`` 的语义一致（随时可切，画面立刻回到未滤镜的样子）。
        """
        self.use_filters = bool(active)
        if not active:
            self.filtered_texture = None

    def _ensureFilterChain(self) -> FilterChain:
        if self.filter_chain is None:
            # 中间纹理的格式跟随相机：HDR 场景（RGBA16F）里的 >1 值不会被夹掉
            self.filter_chain = FilterChain(internal_format=self.internal_format)
        return self.filter_chain

    def _sceneOutputTexture(self):
        """滤镜链的输入：本帧的最终颜色（Bloom 结果优先，其次 WBOIT/体积/帧缓冲）。

        ``colorTexture()`` 可能返回裸纹理 id —— ``filler.textureSize`` 两种都认。
        """
        if self.use_bloom and self.bloomed_texture is not None:
            return self.bloomed_texture
        return self.colorTexture()

    def applyFilters(self):
        """跑一遍滤镜链，把结果记在 :attr:`filtered_texture` 上（返回它或 None）。

        由 :meth:`renderEnd` 每帧调用一次；也可以自己调用（例如想把滤镜结果
        存盘 / 喂给别的相机）。没有滤镜或链被关掉时返回 None 且不做任何 GL 调用。
        """
        self.filtered_texture = None
        self._filters_pending = False
        if not self.use_filters or self.filter_chain is None or not self.filter_chain:
            return None
        source = self._sceneOutputTexture()
        try:
            result = self.filter_chain.apply(source)
        except ValueError as error:
            # 拿不到可采样的源纹理（相机直接出屏、帧缓冲还没就绪……）：这一帧跳过，
            # 但**绝不能让渲染循环崩**。只提示一次，避免每帧刷屏。
            if not self._filters_warned:
                self._filters_warned = True
                ConsoleMessage.printWarning(
                    f"相机滤镜链这一帧被跳过（拿不到可采样的输出纹理）: {error}")
            return None
        if result is not source:
            # 链一定会写自己的中间纹理（不写输入），所以这里的比较是可靠的
            self.filtered_texture = result
        return self.filtered_texture

    def outputTexture(self) -> int:
        """窗口 pass 该采样的最终纹理：滤镜结果 > Bloom 结果 > 本帧颜色。"""
        if (self.use_filters and self._filters_pending
                and self.filter_chain is not None):
            # 兜底：有些 demo 覆盖了 renderEnd() 而没调 super()（Game4 就是），链就
            # 没被跑到 —— 这里补一次，保证"挂上滤镜就一定生效"，不看 demo 怎么写。
            self.applyFilters()
        if self.use_filters and self.filtered_texture is not None:
            return self.filtered_texture.id
        if self.use_bloom and self.bloomed_texture is not None:
            return self.bloomed_texture.id
        return self.colorTexture()

    def _bindWindowTexture(self, uint=GL_TEXTURE0) -> None:
        """把本帧最终颜色（滤镜结果 / Bloom 结果 / 原始颜色）绑到采样器上。"""
        texture = self.outputTexture()
        glActiveTexture(uint)
        glBindTexture(GL_TEXTURE_2D, texture)
        RenderGlobal.instance.using_shader.uniformTex(uint)


    def draw(self, fuc: int = 1) -> None:
        self._bindWindowTexture()
        RenderGlobal.instance.using_shader.uniform1i("fuc", fuc)
        self.render_buffer.draw(False)

    def windowCameraSet(self,
                        bottom_left: tuple[float, float, float],
                        bottom_right: tuple[float, float, float],
                        top_left: tuple[float, float, float],
                        top_right: tuple[float, float, float]) -> None:
        self.buf_builder = self.render_buffer.createBuffer(
            GL_TRIANGLES, POS | TEX)
        self.buf_builder.pos(*bottom_left).tex(0, 0).end()
        self.buf_builder.pos(*bottom_right).tex(1, 0).end()
        self.buf_builder.pos(*top_left).tex(0, 1).end()
        self.buf_builder.pos(*bottom_right).tex(1, 0).end()
        self.buf_builder.pos(*top_right).tex(1, 1).end()
        self.buf_builder.pos(*top_left).tex(0, 1).end()

    def windowCameraDraw(self) -> None:
        self._bindWindowTexture()
        RenderGlobal.instance.using_shader.uniform1i("fuc", 1)
        self.render_buffer.draw(False)

    def switch(self, state: bool = None) -> None:
        rg = RenderGlobal.instance
        if state is None:
            if rg.hasWindowCamera(self):
                rg.removeWindowCamera(self)
            else:
                rg.addWindowCamera(self)
        elif state:
            rg.addWindowCamera(self)
        else:
            rg.removeWindowCamera(self)

    def useBloom(self, active: bool = True, level1: float = 2, level2: float = 4) -> None:
        self.use_bloom = active
        self.__level1 = level1
        self.__level2 = level2
        if not active:
            if self.bloom is not None:
                self.bloom.release()
                self.bloom = None
            self.bloomed_texture = None
            return
        if not self.started:
            return
        self.createBloom()
