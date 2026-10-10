import glm
from typing import Any, TYPE_CHECKING
from OpenGL.GL import (GL_TEXTURE_2D, GL_TEXTURE0, glActiveTexture,
                       glBindTexture, glViewport)
from .math.Color import Color
from .RenderLayer import RenderLayer
if TYPE_CHECKING:
    from .App import App
    from .BaseWindow import BaseWindow
    from .shader.Shader import Shader
    from .gameobject.GameObject import GameObject
    from .gameobject.camera.Camera import Camera
    from .render.Light import LightManager


class RenderGlobal:
    instance = None

    def __new__(cls, *arg: Any, app: "App" = None, window: "BaseWindow" = None,
                **kw: Any) -> "RenderGlobal":
        if cls.instance is None:
            if app is None or window is None:
                raise TypeError(
                    "RenderGlobal.__new__() missing 2 required positional arguments: 'app' and 'window'")
            cls.instance: RenderGlobal = super().__new__(cls)
            cls.instance.__frist_init__(app, window)
        return cls.instance

    def __frist_init__(self, app: "App", window: "BaseWindow") -> None:
        self.app: "App" = app
        self.window: "BaseWindow" = window
        self.bg: Color = Color(0, 0, 0)
        self.using_shader: "Shader" = None
        self._dis_shader: "Shader" = None
        self._bloom_shader: "Shader" = None
        self._fractal_noise_shader: "Shader" = None
        self._volume_noise_shader: "Shader" = None
        # FFT / 逆 FFT（pyglgame/filler 的 FFTFilter）：和 Bloom 一样懒加载
        self._fft_shader: "Shader" = None
        self.using_layer = RenderLayer()
        self.layers: dict[object, RenderLayer] = {0: self.using_layer}
        self.cameras: set[Camera] = set()
        # 是否正处在 WBOIT 半透明 pass 内（用于给 draw 选择 fuc 模式）
        self.oit_pass: bool = False
        # === 光照 / 环境 ===
        # 光源表懒创建：render.Light 反向依赖本模块，模块顶层互相 import 会成环
        # （与 bloom_shader 的处理方式一致）
        self._light_manager = None
        # 半球环境光：朝上的面偏天空色、朝下的面偏地面反光色。
        # 只在场景里存在光源时参与计算（没有光源 = 完全不做光照，保持旧画面）。
        self.ambient_sky = glm.vec3(0.30, 0.34, 0.42)
        self.ambient_ground = glm.vec3(0.10, 0.10, 0.12)
        # 2D 模式（u_light2D == 1）用的**平环境光**：2D 精灵的法线朝 +Z，
        # 拿 N.y 去混天空/地面没有意义。由 MSimple2D.useLighting 上传。
        self.ambient2D = glm.vec3(0.16, 0.17, 0.22)
        #: 是否正处在**阴影贴图 pass** 内（由 Camera.renderShadowMaps 前后置位）。
        #: 3D 的 depth-only 阴影贴图 pass 的标志（2D 阴影走 shadow2d_pass）。
        self.shadow_pass: bool = False
        #: 是否正处在 **2D 阴影遮罩 pass** 内（由 Camera.renderShadowMasks2D 前后置位）。
        #: 相机在这个 pass 里只挑 ``is_shadow_caster`` 的遮挡体来画，
        #: 遮挡体靠它区分"颜色 pass 什么都不画 / 遮罩 pass 画阴影三角形"。
        self.shadow2d_pass: bool = False
        #: 遮罩 pass 当前正在投影的那盏灯（ShadowCaster2D 按它取方向 / 光源位置）
        self.shadow2d_light = None
        #: 遮罩 pass 里模型矩阵是否已经设成单位阵（相机**每层设一次**，遮挡体就不用各算一遍）
        self.shadow2d_matrix_ready: bool = False
        #: **当前正在渲染的相机**（由 RenderLayer 在每趟相机 pass 前后设置 / 清空）。
        #: 需要的对象是从相机取信息但**拿不到相机参数**的那些（例如
        #: ``pyglgame.particle.QuadRenderer`` 要把四边形朝向相机算广告牌）——
        #: 用 ``getattr(RenderGlobal.instance, "using_camera", None)`` 取，没有就是 None。
        self.using_camera = None
        # 自增帧号：体积渲染/时域降噪做抖动时用（(frame_index, x, y) -> 采样偏移）
        self.frame_index: int = 0
        # === Object ID / Mask / GPU Picking（见 render/ObjectIdBuffer.py）===
        #: 是否正处在 Object ID pass 内（RenderBuffer.draw 靠它强制"不混合 + 写深度"）
        self.id_pass: bool = False
        #: 最近一次跑过 ID pass 的相机（``pickObject`` 优先从它读 ID）
        self._object_id_camera = None
        #: 是否把 Object ID / Mask 纹理当调试画面画到窗口上（GameMainLoop 每帧检查）
        self.object_id_debug: bool = False

        # === Model transforms ===
        self._position = glm.vec3(0.0)
        self._rotation = glm.vec3(0.0)  # in degrees
        self._scale = glm.vec3(1.0)
        self._model_dirty = True
        self._model = glm.mat4(1.0)
        # 缓存的矩阵（避免每帧重新构造 3 个 mat4）
        self._translate_cache = glm.mat4(1.0)
        self._rotate_cache = glm.mat4(1.0)
        self._scale_cache = glm.mat4(1.0)

    def __init__(self, *arg: Any, **kw: Any) -> None:
        pass

    # === Shader（按需加载，2D 游戏不必再依赖 compute shader 文件）===

    def passFuc(self, screen_space: bool = False) -> int:
        """当前 pass 应使用的 ``fuc`` 值。

        - 不透明/普通 pass：``0``（DIS，世界空间）/ ``1``（WID，屏幕空间）
        - WBOIT 半透明 pass：``6``（OIT，世界空间）/ ``7``（OIT_WID，屏幕空间）
        - Object ID pass：``10``（OBJECT_ID，世界空间）/ ``12``（OBJECT_ID_SKIP，
          屏幕空间 —— 全屏背景 / UI 覆盖层不产生 ID，见 Passes.py）

        屏幕空间绘制（例如全屏背景、UI）在半透明 pass 里必须用 7，否则会被
        当成世界空间坐标做矩阵变换。
        """
        if getattr(self, "id_pass", False):
            # Object ID pass：世界空间几何写自己的 ID；屏幕空间的全屏绘制直接跳过
            # （否则整屏都会被写上一个"假 ID"，还会写深度把后面的物体挡掉 → 拾取失效）
            # 懒导入：与 Light / ShadowMask2D 同一套做法，避免模块级循环引用
            from .render.Passes import OBJECT_ID, OBJECT_ID_SKIP
            return OBJECT_ID_SKIP if screen_space else OBJECT_ID
        if self.oit_pass:
            return 7 if screen_space else 6
        return 1 if screen_space else 0

    @property
    def light_manager(self) -> "LightManager":
        """全局光源表（:class:`~pyglgame.render.Light.LightManager`）。"""
        if self._light_manager is None:
            from .render.Light import LightManager
            self._light_manager = LightManager()
        return self._light_manager

    @property
    def dis_shader(self) -> "Shader":
        return self._dis_shader

    @dis_shader.setter
    def dis_shader(self, value: "Shader") -> None:
        self._dis_shader = value

    @property
    def bloom_shader(self) -> "Shader":
        if self._bloom_shader is None:
            from .shader.ShaderManager import ShaderManager
            self._bloom_shader = ShaderManager.loadComputeShader("computeBloom")
        return self._bloom_shader

    @bloom_shader.setter
    def bloom_shader(self, value: "Shader") -> None:
        self._bloom_shader = value

    @property
    def fractal_noise_shader(self) -> "Shader":
        if self._fractal_noise_shader is None:
            from .shader.ShaderManager import ShaderManager
            self._fractal_noise_shader = ShaderManager.loadComputeShader(
                "computeFractalNoise")
        return self._fractal_noise_shader

    @fractal_noise_shader.setter
    def fractal_noise_shader(self, value: "Shader") -> None:
        self._fractal_noise_shader = value

    @property
    def volume_noise_shader(self) -> "Shader":
        """3D 体积噪声的计算着色器（TextureVolumeNoise 用，懒加载）。"""
        if self._volume_noise_shader is None:
            from .shader.ShaderManager import ShaderManager
            self._volume_noise_shader = ShaderManager.loadComputeShader(
                "computeVolumeNoise")
        return self._volume_noise_shader

    @volume_noise_shader.setter
    def volume_noise_shader(self, value: "Shader") -> None:
        self._volume_noise_shader = value

    @property
    def fft_shader(self) -> "Shader":
        """FFT / 逆 FFT 的计算着色器（``pyglgame/filler`` 的 FFTFilter 用，懒加载）。

        和 bloom_shader 同一套处理方式：不用 FFT 的项目不会去读这个文件。
        """
        if self._fft_shader is None:
            from .shader.ShaderManager import ShaderManager
            self._fft_shader = ShaderManager.loadComputeShader("computeFFT")
        return self._fft_shader

    @fft_shader.setter
    def fft_shader(self, value: "Shader") -> None:
        self._fft_shader = value

    def start(self) -> None:
        # 快照迭代：层的 start() 里可能创建新层/对象，直接迭代 values() 会有风险
        for layer in tuple(self.layers.values()):
            layer.start()

    def useLayer(self, layer: int) -> None:
        if layer in self.layers:
            self.using_layer = self.layers[layer]
            return
        self.using_layer = RenderLayer()
        self.layers[layer] = self.using_layer

    def updateLayer(self, dt: float, tps: float) -> None:
        # 用快照迭代：对象/层在 update 过程中可能被增删（例如生成新对象），
        # 直接迭代原字典会抛 RuntimeError: dictionary changed size during iteration
        for layer in tuple(self.layers.values()):
            layer.update(dt, tps)

    def renderLayer(self, dt: float, fps: float) -> None:
        if self.using_shader is None:
            raise RuntimeError(
                "RenderGlobal.using_shader 为空：请先加载并 use() 主着色器（dis_shader）")
        self.updateGlobalUniforms()
        self.using_shader.uniform1i("fuc", 0)
        # ID / Mask 纹理固定用 9 号单元（0..5 = tex0..tex5，6/7/8 已被阴影/AO/2D 遮罩占用）。
        # 见 pyglgame/render/ObjectIdBuffer.py
        from .render.ObjectIdBuffer import OBJECT_ID_TEXTURE_UNIT
        self.using_shader.uniform1i("u_objectIdTex", OBJECT_ID_TEXTURE_UNIT)
        for layer in tuple(self.layers.values()):
            layer.render(dt, fps)
        # 帧号在最后一层渲染完之后自增：体积渲染/时域抖动可以在本帧内稳定读取
        self.frame_index += 1

    def addToLayer(self, obj: "GameObject", is_camera: bool = False) -> RenderLayer:
        if is_camera:
            self.using_layer.addCamera(obj)
        else:
            self.using_layer.addObject(obj)
        obj._render_layer = self.using_layer
        return self.using_layer

    def removeFromLayer(self, obj: "GameObject", is_camera: bool = False) -> None:
        # 无论是相机还是普通对象都能正确移除（不再依赖“当前正在使用的层”）
        self.removeWindowCamera(obj)
        for layer in tuple(self.layers.values()):
            if layer.contains(obj):
                if is_camera or layer.isCamera(obj):
                    layer.removeCamera(obj)
                else:
                    layer.removeObject(obj)
                if getattr(obj, "_render_layer", None) is layer:
                    obj._render_layer = None
                return

    def addWindowCamera(self, camera: "Camera") -> None:
        self.cameras.add(camera)

    def removeWindowCamera(self, camera: "Camera") -> None:
        if camera in self.cameras:
            self.cameras.remove(camera)

    def hasWindowCamera(self, camera: "Camera") -> bool:
        return camera in self.cameras

    # === Model Matrix ===
    def setPosition(self, x: float, y: float, z: float) -> None:
        self._position = glm.vec3(x, y, z)
        self._model_dirty = True
        self._translate_cache = glm.translate(glm.mat4(1.0), self._position)

    def setRotation(self, rot: glm.vec3) -> None:
        self._rotation = rot
        self._model_dirty = True
        r = self._rotation
        mat = glm.mat4(1.0)
        mat = glm.rotate(mat, glm.radians(r.x), glm.vec3(1, 0, 0))
        mat = glm.rotate(mat, glm.radians(r.y), glm.vec3(0, 1, 0))
        mat = glm.rotate(mat, glm.radians(r.z), glm.vec3(0, 0, 1))
        self._rotate_cache = mat

    def setScale(self, scale: glm.vec3) -> None:
        self._scale = scale
        self._model_dirty = True
        self._scale_cache = glm.scale(glm.mat4(1.0), self._scale)

    @property
    def translate(self) -> glm.mat4x4:
        return self._translate_cache

    @property
    def rotate(self) -> glm.mat4x4:
        return self._rotate_cache

    @property
    def scale(self) -> glm.mat4x4:
        return self._scale_cache

    @property
    def model(self) -> glm.mat4x4:
        if self._model_dirty:
            self._model = self._translate_cache * self._rotate_cache * self._scale_cache
            self._model_dirty = False
        return self._model

    # === Uniform Helper ===

    def resetLightingPassUniforms(self, shader: "Shader" = None) -> None:
        """每帧复位"pass 级"光照附加状态（阴影 / 环境光遮蔽 / 2D 材质）。

        GL 的 uniform 是持久状态：上一帧占用的阴影贴图槽位、AO 纹理、2D 法线/高光贴图
        开关如果不复位，本帧的关闭状态就不会生效（表现为关掉阴影后画面仍是黑的、
        或者普通 2D 图元吃到了上一个精灵的法线贴图）。
        由 :meth:`RenderLayer.render` 在**每台相机的 renderStart 之前**调用，随后相机
        上传自己的状态（``MSimple2D`` 的 ``u_light2D`` / ``u_ambientFlat``）、各 pass
        （ShadowMap、AmbientOcclusion）再把自己的状态盖上去 —— 顺序不能颠倒
        （曾经放在 renderStart 之后，2D 相机的 ``u_light2D = 1`` 每帧都被抹掉）。
        """
        if shader == None:
            shader = self.using_shader
        shader.uniform1i("u_shadowLight", -1)
        shader.uniform1f("u_shadowTexel", 1.0 / 1024.0)
        shader.uniform1f("u_shadowBias", 0.0025)
        shader.uniform1f("u_shadowNormalOffset", 0.05)
        shader.uniform1i("u_aoEnabled", 0)
        shader.uniform1f("u_aoStrength", 1.0)
        self.resetShadow2DUniforms(shader)
        self.resetMaterial2DUniforms(shader)

    @staticmethod
    def resetShadow2DUniforms(shader: "Shader" = None) -> None:
        """把 2D 阴影遮罩（Phase 7）的 uniform 复位成"没有任何光源投影"。

        ``u_shadow2DCast[i] = 0`` 时 ``dis.frag`` 的光照循环完全不走遮罩那一路 ——
        所以 3D 场景、以及没开 2D 阴影的画面与加这期之前**逐像素一致**。
        真的渲染遮罩时由 ``Camera.renderShadowMasks2D`` →
        ``ShadowMask2D.upload`` 把采样器与这几个标志覆盖掉。

        写成静态方法是为了在鸭子类型的"假 RenderGlobal"（单测）里也能直接调用。
        """
        if shader is None:
            shader = RenderGlobal.instance.using_shader
        if shader is None:
            return
        # 懒导入：避免 RenderGlobal ↔ render 子包的模块级循环引用
        from .render.ShadowMask2D import MAX_LAYERS
        for index in range(MAX_LAYERS):
            shader.uniform1i(f"u_shadow2DCast[{index}]", 0)

    @staticmethod
    def resetMaterial2DUniforms(shader: "Shader" = None) -> None:
        """把 2D 光照（Phase 7）的 uniform 复位成"3D 中性值"。

        这些默认值让 ``shadeLighting()`` 的计算与加这期之前**逐像素一致**：
        ``u_specularColor = 1``、``u_specularStrength = 1``、
        ``u_specularShininess = 32``（原来硬编码的指数）、``u_specularMode = 0``
        （高光贴图的默认语义 = Blinn-Phong 光斑）、``u_specularContrast = 1``
        （高光贴图对比度原样）、材质 UV 变换是单位变换、
        ``u_receiveShadow = 1``（接收 2D 阴影，但 ``u_shadow2DCast`` 全 0 时
        这一路本身就不参与）。
        材质开关（N2D / S2D 位）走顶点格式，**不在这里**，所以不需要复位。

        写成静态方法是为了在鸭子类型的"假 RenderGlobal"（单测）里也能直接调用。
        """
        if shader is None:
            shader = RenderGlobal.instance.using_shader
        if shader is None:
            return
        shader.uniform1i("u_light2D", 0)
        shader.uniform1f("u_normalStrength", 1.0)
        shader.uniform2f("u_materialScale", 1.0, 1.0)
        shader.uniform2f("u_materialOffset", 0.0, 0.0)
        shader.uniform1f("u_materialRotation", 0.0)
        shader.uniform3f("u_specularColor", 1.0, 1.0, 1.0)
        shader.uniform1f("u_specularStrength", 1.0)
        shader.uniform1f("u_specularShininess", 32.0)
        shader.uniform1f("u_specularContrast", 1.0)
        shader.uniform1i("u_specularMode", 0)
        shader.uniform1f("u_receiveShadow", 1.0)

    def updateGlobalUniforms(self, shader: "Shader" = None) -> None:
        if shader == None:
            shader = self.using_shader
        # 三个矩阵都做了缓存，这里只是上传，不再每帧重新构造 mat4
        shader.uniformMatrix4fv("translate", self._translate_cache)
        shader.uniformMatrix4fv("rotate", self._rotate_cache)
        shader.uniformMatrix4fv("scale", self._scale_cache)
        # === 光照 / 环境 ===
        # 没有光源时 u_lightCount = 0，dis.frag 会整段跳过光照（旧画面不变）
        self.light_manager.upload(shader)
        shader.uniform3f("u_ambientSky",
                         self.ambient_sky.x, self.ambient_sky.y, self.ambient_sky.z)
        shader.uniform3f("u_ambientGround", self.ambient_ground.x,
                         self.ambient_ground.y, self.ambient_ground.z)

    # === Object ID / Mask / GPU Picking（见 render/ObjectIdBuffer.py）===

    def setObjectIdDebug(self, active: bool = True) -> None:
        """把 Object ID / Mask 纹理当调试画面显示到窗口上（见 drawObjectIdDebug）。"""
        self.object_id_debug = bool(active)

    def objectIdCamera(self) -> "Camera":
        """优先返回最近跑过 ID pass 的相机；否则在场景里找一台开着 ID 缓冲的。"""
        camera = getattr(self, "_object_id_camera", None)
        if camera is not None and getattr(camera, "object_id_buffer", None) is not None:
            return camera
        for camera in self.cameras:
            if getattr(camera, "object_id_buffer", None) is not None:
                return camera
        for layer in self.layers.values():
            for camera in tuple(getattr(layer, "cameras", ())):
                if getattr(camera, "object_id_buffer", None) is not None:
                    return camera
        return None

    def enableObjectId(self, active: bool = True) -> None:
        """给场景里所有相机开关 Object ID / Mask pass（``pickObject`` 惰性打开用）。"""
        for layer in self.layers.values():
            for camera in tuple(getattr(layer, "cameras", ())):
                setter = getattr(camera, "useObjectId", None)
                if setter is not None:
                    setter(active)

    def objectIdTexture(self, camera: "Camera" = None) -> int:
        """Object ID / Mask 纹理 id（0 = 还没有，见 ObjectIdBuffer）。"""
        camera = camera or self.objectIdCamera()
        if camera is None:
            return 0
        getter = getattr(camera, "objectIdTexture", None)
        if getter is None:
            return 0
        return getter()

    def bindObjectIdTexture(self, unit: int = None,
                            camera: "Camera" = None) -> int:
        """把 ID / Mask 纹理绑到纹理单元 ``unit``（**单元下标**，默认 9）并同步采样器。

        后处理（``pyglgame.filler`` 的滤镜链等）只要调用它就能拿到 Object ID 掩码：
        ``dis.frag`` 里的 ``objectIdMaskAt(uv)`` 直接可用（配合 ``u_objectIdMask``）。
        返回纹理 id（0 = 没有可用纹理）。
        """
        from .render.ObjectIdBuffer import OBJECT_ID_TEXTURE_UNIT
        texture = self.objectIdTexture(camera)
        if not texture:
            return 0
        if unit is None:
            unit = OBJECT_ID_TEXTURE_UNIT
        unit = int(unit)
        glActiveTexture(GL_TEXTURE0 + unit)
        glBindTexture(GL_TEXTURE_2D, texture)
        shader = self.using_shader
        if shader is not None:
            shader.uniform1i("u_objectIdTex", unit)
        return texture

    def setObjectIdMask(self, object_id: int,
                        color: tuple[float, float, float] = (1.0, 0.4, 0.1),
                        strength: float = 1.0,
                        camera: "Camera" = None) -> int:
        """按 Object ID 高亮：主着色器把命中该 ID 的像素朝 ``color`` 混。

        需求 10 的"指定 Object ID → 高亮"开箱示例（马赛克 / 描边可用同样的
        ``bindObjectIdTexture`` + 自己的后处理 shader 实现）。返回 ID 纹理 id
        （0 = 还没有 ID 纹理，调用无效）。用 :meth:`clearObjectIdMask` 关掉。
        """
        shader = getattr(self, "dis_shader", None) or self.using_shader
        if shader is None:
            return 0
        texture = self.bindObjectIdTexture(None, camera)
        if not texture:
            return 0
        shader.use()
        shader.uniform1i("u_objectIdMask", int(object_id))
        shader.uniform3f("u_objectIdHighlight",
                         float(color[0]), float(color[1]), float(color[2]))
        shader.uniform1f("u_objectIdHighlightStrength", float(strength))
        return texture

    def clearObjectIdMask(self) -> None:
        """关掉"按 Object ID 高亮"（恢复默认，画面回到不加效果的样子）。"""
        shader = getattr(self, "dis_shader", None) or self.using_shader
        if shader is None:
            return
        shader.use()
        shader.uniform1i("u_objectIdMask", 0)
        shader.uniform1f("u_objectIdHighlightStrength", 0.0)

    def objectIdAt(self, mouse_x: float, mouse_y: float,
                   camera: "Camera" = None) -> int:
        """窗口鼠标坐标处的物体 ID（0 = 背景 / 还没有 ID 数据）。只读 1×1 像素。"""
        camera = camera or self.objectIdCamera()
        if camera is None:
            return 0
        buffer = getattr(camera, "object_id_buffer", None)
        if buffer is None:
            return 0
        return buffer.readIdAtScreen(mouse_x, mouse_y, self.window.size)

    def pickObject(self, mouse_x: float, mouse_y: float,
                    camera: "Camera" = None) -> "GameObject | None":
        """GPU Picking：返回鼠标 ``(x, y)`` 处**最前面**的 GameObject（背景 = ``None``）。

        - ``(x, y)`` 是窗口像素坐标、**原点在左上角**（``window.getMouse()`` 的约定）；
          内部会做 Y 翻转与 resize 缩放（见 ``ObjectIdBuffer.screenToBufferPixel``）。
        - 只读该像素的 1×1 ID，不整帧回读。
        - 相机还没开 Object ID pass 时会**惰性打开**它并返回 ``None``：ID 缓冲是渲染
          出来的，所以下一帧起才有可拾取的数据。
        """
        camera = camera or self.objectIdCamera()
        if camera is None or getattr(camera, "object_id_buffer", None) is None:
            if camera is not None:
                setter = getattr(camera, "useObjectId", None)
                if setter is not None:
                    setter(True)
            else:
                self.enableObjectId(True)
            return None
        object_id = self.objectIdAt(mouse_x, mouse_y, camera)
        if object_id <= 0:
            return None
        from .render.ObjectIdRegistry import ObjectIdRegistry
        return ObjectIdRegistry.get(object_id)

    def drawObjectIdDebug(self, camera: "Camera" = None) -> bool:
        """把 Object ID / Mask 纹理画到当前帧缓冲（窗口）上，方便调试。

        由 ``GameMainLoop`` 每帧检查 :attr:`object_id_debug` 后调用
        （``renderer.setObjectIdDebug(True)`` 打开）；也可以自己在一帧最后直接调用。
        ID 按哈希映射成伪彩色，背景是深灰。返回是否真的画了（没有纹理时 False）。
        """
        camera = camera or self.objectIdCamera()
        if camera is None:
            return False
        texture = self.objectIdTexture(camera)
        if not texture:
            return False
        shader = getattr(self, "dis_shader", None) or self.using_shader
        if shader is None:
            return False
        shader.use()
        from .render.Passes import OBJECT_ID_DEBUG
        from .render.RenderBuffer import RenderBuffer
        window = self.window
        glViewport(0, 0, int(window.size.w), int(window.size.h))
        # 绑定 ID 纹理并同步 u_objectIdTex（bindObjectIdTexture 内部用 unit 下标）
        self.bindObjectIdTexture(None, camera)
        shader.uniform2f("win_wh", window.size.w, window.size.h)
        shader.uniform1i("fuc", OBJECT_ID_DEBUG)
        RenderBuffer.getWindowRenderBuffer().draw(False)
        shader.uniform1i("fuc", self.passFuc(False))
        return True


def backgroundColor() -> tuple:
    """相机帧缓冲的清屏色 —— 也就是场景里的"天空"。

    两个容易踩的点：

    1. **窗口的 clear color 是看不到的**：相机把场景渲染进自己的 FBO，天空那部分
       像素就是**这个 FBO 的清屏色**。所以 demo 设 ``RenderGlobal.instance.bg``
       必须落到帧缓冲的清屏上，只设窗口的话永远是一片黑（踩过）。
    2. **alpha 必须留在 0**：不透明 pass 之后还有 WBOIT 合成 / 体积散射在按 alpha
       混，清屏 alpha 改成 1 会改掉所有场景的画面。默认 ``bg`` 是纯黑 → 与旧的
       ``glClearColor(0, 0, 0, 0)`` 逐像素一致（老 demo 画面不变）。

    返回值可以直接 ``glClearColor(*backgroundColor())``。
    """
    instance = RenderGlobal.instance
    if instance is None:
        return (0.0, 0.0, 0.0, 0.0)
    bg = instance.bg
    return (float(bg.r), float(bg.g), float(bg.b), 0.0)
