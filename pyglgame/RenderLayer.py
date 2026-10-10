from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .gameobject.GameObject import GameObject
    from .gameobject.camera.Camera import Camera

from .render import RenderState


class RenderLayer:
    """渲染层。

    用 **有序 list + set 索引** 代替纯 set：
    - 迭代顺序确定（透明物体叠加顺序不再随机）
    - update/render 使用快照迭代，对象在回调里增删不会抛
      ``RuntimeError: Set changed size during iteration``
    """

    def __init__(self) -> None:
        self.objects: list["GameObject"] = []
        self._object_set: set["GameObject"] = set()
        self.cameras: list["Camera"] = []
        self._camera_set: set["Camera"] = set()
        self.__inited = False
        self.__starts: list["GameObject"] = []
        # 运行时新增的对象：__new__ 阶段就被注册进来了，此时 __init__ 还没跑完，
        # 所以 start() 必须推迟到下一帧边界，否则子类在 start() 里访问
        # __init__ 设置的属性会 AttributeError。
        self.__pending_starts: list["GameObject"] = []

    def start(self) -> None:
        for obj in tuple(self.__starts):
            obj.start()
        self.__starts.clear()
        self.__inited = True

    def flushStarts(self) -> None:
        """在帧边界调用：启动所有“运行时新增”的对象。"""
        if not self.__pending_starts:
            return
        pending = self.__pending_starts
        self.__pending_starts = []
        for obj in pending:
            obj.start()

    def isCamera(self, obj: "GameObject") -> bool:
        return obj in self._camera_set

    def contains(self, obj: "GameObject") -> bool:
        return obj in self._object_set

    def addObject(self, obj: "GameObject") -> None:
        if obj in self._object_set:
            return
        self._object_set.add(obj)
        self.objects.append(obj)
        if self.__inited:
            if obj not in self.__pending_starts:
                self.__pending_starts.append(obj)
            return
        self.__starts.append(obj)

    def removeObject(self, obj: "GameObject") -> None:
        if obj in self._object_set:
            self._object_set.discard(obj)
            try:
                self.objects.remove(obj)
            except ValueError:
                pass
        if obj in self.__starts:
            self.__starts.remove(obj)
        if obj in self.__pending_starts:
            self.__pending_starts.remove(obj)

    def removeAny(self, obj: "GameObject") -> None:
        if obj in self._camera_set:
            self.removeCamera(obj)
        else:
            self.removeObject(obj)

    def addCamera(self, camera: "Camera") -> None:
        if camera not in self._camera_set:
            self._camera_set.add(camera)
            self.cameras.append(camera)
        self.addObject(camera)

    def removeCamera(self, camera: "Camera") -> None:
        if camera in self._camera_set:
            self._camera_set.discard(camera)
            try:
                self.cameras.remove(camera)
            except ValueError:
                pass
        self.removeObject(camera)

    def update(self, dt: float, tps: float) -> None:
        self.flushStarts()
        for obj in tuple(self.objects):
            if obj.active:
                obj.update(dt, tps)

    def render(self, dt: float, fps: float) -> None:
        """两趟渲染（顺序无关透明的关键）：

        1. **不透明 pass**：维持引擎默认状态（混合 alpha + 深度写入开），
           深度测试保证结果与顺序无关。
        2. **半透明 pass**：``camera.beginTransparent()`` 关闭深度写入，
           对象按相机深度**从远到近**排序后再画（对象级顺序无关）；
           若相机启用了 WBOIT，则由相机把这趟重定向到累加缓冲，
           由 weighted-blended 合成做到逐像素顺序无关。

        光照相关的 pass（阴影贴图 → SSAO → 体积散射）由引擎按固定顺序驱动：
        ``renderShadowMaps`` / ``renderOcclusion`` 是**相机**的钩子；体积散射则是
        **世界里的对象**（``is_fog_volume`` 的对象，见
        :class:`~pyglgame.gameobject.FogVolume.FogVolume`）—— 引擎核心只负责"按正确
        的顺序调用它们"，具体算法都在各自的模块里。
        """
        self.flushStarts()
        objects = tuple(self.objects)
        # 世界里的"体积对象"（FogVolume）：不参与几何 pass，只在半透明之后跑体积 pass
        volumes = tuple(obj for obj in objects
                        if getattr(obj, "is_fog_volume", False))
        # 延迟导入：RenderGlobal 在模块顶层 import 本类，顶层互相 import 会成环
        from .RenderGlobal import RenderGlobal
        for obj in objects:
            obj._render(dt, fps)
        for camera in tuple(self.cameras):
            # 记下"当前正在渲染的相机"：粒子系统之类的对象拿不到相机参数，
            # 但需要它（例如广告牌四边形要按相机的右/上方向张开）。pass 结束会清掉。
            render_global = RenderGlobal.instance
            if render_global is not None:
                render_global.using_camera = camera
            opaque = []
            transparent = []
            for obj in objects:
                if not obj.active or getattr(obj, "is_fog_volume", False):
                    # 体积对象不是几何：它由 renderVolumes 在半透明之后统一驱动
                    continue
                if getattr(obj, "transparent", False):
                    transparent.append(obj)
                else:
                    opaque.append(obj)
            camera_begin = getattr(camera, "beginFrame", None)
            if camera_begin is not None:
                camera_begin()
            # pass 级光照附加状态复位（阴影/AO/2D 材质默认关闭）**必须在 renderStart 之前**：
            # 相机的 setCameraUniforms() 会上传它自己的 per-camera 状态（u_light2D /
            # u_ambientFlat），放在复位之后才不会被抹掉（曾经放在 renderStart 后面，
            # 结果 2D 相机的 u_light2D=1 每帧都被复位成 0，法线贴图整帧失效）。
            # 用 getattr 兼容测试里的假 RenderGlobal（不实现该钩子时直接跳过）
            reset_lighting = getattr(
                RenderGlobal.instance, "resetLightingPassUniforms", None)
            if reset_lighting is not None:
                reset_lighting()
            camera.renderStart()
            # 每帧都从不透明 pass 的基准状态开始（= 引擎原来的全局状态），
            # 这样不透明内容的渲染结果与以前完全一致
            RenderState.setDefaults()
            # === 阴影 pass：必须在写不透明颜色之前（只写深度，不输出颜色）===
            render_shadows = getattr(camera, "renderShadowMaps", None)
            if render_shadows is not None:
                render_shadows(opaque, dt, fps)
            # === 2D 阴影（几何遮挡遮罩，Phase 7）：同样必须在写颜色之前 ===
            # 普通物体在这个 pass 里不画：相机只挑 ``is_shadow_caster`` 的遮挡体，
            # 所以"没有遮挡体 / 没有投影灯"时零开销（相机自己会提前返回）。
            render_shadows2d = getattr(camera, "renderShadowMasks2D", None)
            if render_shadows2d is not None:
                render_shadows2d(opaque, dt, fps)
            # === 环境光遮蔽（SSAO）：吃不透明 pass 的深度，供后面的光照采样 ===
            render_occlusion = getattr(camera, "renderOcclusion", None)
            if render_occlusion is not None:
                render_occlusion(opaque, dt, fps)
            # pass 钩子之后再把不透明 pass 的基准状态兜一遍：自定义 pass 可能直接
            # 改过 GL 状态（SSAO / 体积渲染这类全屏后处理都会临时关掉深度测试、
            # 混合、深度写入）。漏了这一步，不透明物体就退化成"按画序遮挡"——
            # 近处的物体挡不住远处的物体，深度纹理也拿不到真实几何深度。
            RenderState.setDefaults()
            for obj in opaque:
                obj.render(dt, fps)
            if transparent:
                self.sortTransparent(camera, transparent)
                begin = getattr(camera, "beginTransparent", None)
                end = getattr(camera, "endTransparent", None)
                if begin is not None:
                    begin()
                for obj in transparent:
                    obj.render(dt, fps)
                if end is not None:
                    end()
            # === 体积散射：在半透明之后、tone mapping 之前（加性 HDR）===
            # 相机的兼容入口（useVolumetric）先用它自己那个"全局雾"体积跑一遍，
            # 然后是世界里所有体积对象（FogVolume）—— 都由本层收集、按远→近合成
            render_volumetric = getattr(camera, "renderVolumetric", None)
            if render_volumetric is not None:
                render_volumetric(dt, fps)
            self.renderVolumes(camera, volumes, dt, fps)
            camera.renderEnd()
            # === Object ID / Mask pass（GPU Picking）：颜色 pass 之后单独跑一趟 ===
            # 只写物体 ID（不做光照/材质）；相机没开 useObjectId 时这一步零开销
            # （相机自己会提前返回）。见 pyglgame/render/ObjectIdBuffer.py
            render_ids = getattr(camera, "renderObjectIds", None)
            if render_ids is not None:
                render_ids(opaque + transparent, dt, fps)
            if render_global is not None and getattr(
                    render_global, "using_camera", None) is camera:
                render_global.using_camera = None

    def renderVolumes(self, camera: "Camera", volumes: list["GameObject"],
                      dt: float, fps: float) -> None:
        """世界里的体积对象（FogVolume）：半透明之后、tone mapping 之前。

        **按相机深度从远到近**依次合成 —— 顺序在这一步是硬约束：体积散射是加性
        HDR 项，近处的雾会把远处的散射再衰减一次
        （``(scene*T_far + s_far) * T_near + s_near``），顺序反了远处的雾就会盖在
        近处的物体上。每个体积自己把结果接到 ``camera.volumetric_texture``。
        """
        active = [obj for obj in volumes if obj.active]
        if not active:
            return
        self.sortByCameraDepth(camera, active)
        for volume in active:
            render_pass = getattr(volume, "renderVolumePass", None)
            if render_pass is not None:
                render_pass(camera, dt, fps)

    @staticmethod
    def sortByCameraDepth(camera: "Camera", objects: list["GameObject"]) -> None:
        """按相机空间深度**从远到近**排序（深度相同用 render_order 兜底）。"""
        depth_of = getattr(camera, "depthOf", None)
        if depth_of is None:
            objects.sort(key=lambda o: getattr(o, "render_order", 0), reverse=True)
            return
        objects.sort(
            key=lambda o: (depth_of(o), getattr(o, "render_order", 0)), reverse=True)

    @staticmethod
    def sortTransparent(camera: "Camera", objects: list["GameObject"]) -> None:
        """半透明对象按相机空间深度从远到近排序（深度相同用 render_order 兜底）。"""
        RenderLayer.sortByCameraDepth(camera, objects)
