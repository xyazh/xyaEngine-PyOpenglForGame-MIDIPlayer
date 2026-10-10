"""最简单的 2D 投影：世界坐标直接对应屏幕，并且支持 2D 光照（Phase 7）。

两种用法，**默认是第一种**（老代码零改动）
------------------------------------------

1. **NDC 直通**（历史行为）：投影是单位矩阵、view 只有平移 —— 世界坐标 ``x/y ∈ [-1, 1]``
   就是屏幕坐标。``Game1 (2).py`` 用的就是这种。
2. **正交相机**：调用 :meth:`MSimple2D.setBySize` / :meth:`MSimple2D.setOrthographic`
   之后，世界坐标变成"米/像素"尺度，投影由 ``glm.ortho`` 生成；再配
   :meth:`MSimple2D.setZoom` / :meth:`MSimple2D.setRotation` /
   :meth:`MSimple2D.setPosition` 就是一个正经的 2D 相机，并可用
   :meth:`MSimple2D.worldToScreen` / :meth:`MSimple2D.screenToWorld` 做鼠标拾取。

2D 光照：:meth:`MSimple2D.useLighting` 打开 ``u_light2D``（平环境光 + 法线贴图）
并上传 ``u_cameraPos``（高光要视线方向，这个相机以前根本不上传它）。光照仍是引擎的
前向光照，放 :class:`~pyglgame.render.Light.Light` 就行；2D 里**光的 z 是"离精灵
平面的高度"**（``Light.light2D()`` 已经填好）。见 ``docs/2D光照与阴影.md``。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import glm

from .MBase import MBase
from ....RenderGlobal import RenderGlobal

if TYPE_CHECKING:
    from ....shader.Shader import Shader


@dataclass
class OrthoParams2D:
    """正交投影参数（2D 的 near/far 给得很宽松：2D 内容的 z 一般很接近）。"""
    left: float = -1.0
    right: float = 1.0
    bottom: float = -1.0
    top: float = 1.0
    near: float = -1000.0
    far: float = 1000.0


class MSimple2D(MBase):
    """最简单可用的 2D 相机（可选正交范围 / 缩放 / 旋转 / 2D 光照）。

    默认是**NDC 直通**（历史行为）：投影是单位矩阵、view 只有平移 —— 世界坐标
    ``x/y ∈ [-1, 1]`` 就是屏幕坐标。调用 :meth:`setBySize` / :meth:`setOrthographic`
    之后变成真正的正交相机，再配 :meth:`setZoom` / :meth:`setRotation` /
    :meth:`setPosition`，并可用 :meth:`worldToScreen` / :meth:`screenToWorld` 做拾取。
    """

    def __init__(self) -> None:
        # === Projection ===
        # identity_projection=True 时投影是单位矩阵（NDC 直通，历史行为）
        self.identity_projection: bool = True
        self.ortho_params = OrthoParams2D()
        self._projection_dirty = False
        self._projection = glm.mat4(1.0)

        # === View ===
        self._view_dirty = False
        self._view = glm.mat4(1.0)
        self._eye = glm.vec3(0.0, 0.0, 3.0)
        self._target = glm.vec3(0.0, 0.0, 0.0)
        self._up = glm.vec3(0.0, 1.0, 0.0)
        #: True = 用 lookAt 生成 view（pitch/yaw 非 0 时自动切换，做"伪 3D"视差）
        self.use_look_at: bool = False
        self.zoom: float = 1.0
        self.roll: float = 0.0

        # === 2D 光照 ===
        self.lighting: bool = False
        #: None = 用 RenderGlobal.ambient2D
        self.ambient = None

    # === Projection Matrix ===

    def setOrthographic(self, left: float = None, right: float = None,
                        bottom: float = None, top: float = None,
                        near: float = None, far: float = None) -> "MSimple2D":
        """设置正交范围（只写想改的字段）。调用之后投影不再是单位矩阵。"""
        params = self.ortho_params
        for name, value in (("left", left), ("right", right), ("bottom", bottom),
                            ("top", top), ("near", near), ("far", far)):
            if value is not None:
                setattr(params, name, float(value))
        self.identity_projection = False
        self._projection_dirty = True
        return self

    def setBySize(self, width: float, height: float, near: float = None,
                  far: float = None) -> "MSimple2D":
        """按"可见范围"设置正交投影：``width × height`` 个世界单位铺满屏幕。"""
        return self.setOrthographic(left=-float(width) * 0.5, right=float(width) * 0.5,
                                    bottom=-float(height) * 0.5, top=float(height) * 0.5,
                                    near=near, far=far)

    def setIdentityProjection(self, identity: bool = True) -> "MSimple2D":
        """切回/切出"NDC 直通"（默认直通）。"""
        self.identity_projection = bool(identity)
        self._projection_dirty = True
        return self

    def updateAspectFromSize(self, width: float, height: float) -> "MSimple2D":
        """按窗口宽高比调整正交范围（保持高度、改左右）；NDC 直通模式下什么都不做。"""
        if height == 0 or self.identity_projection:
            return self
        params = self.ortho_params
        current_height = params.top - params.bottom
        new_width = current_height * (float(width) / float(height))
        center_x = (params.left + params.right) * 0.5
        return self.setOrthographic(left=center_x - new_width * 0.5,
                                    right=center_x + new_width * 0.5)

    @property
    def projection(self) -> glm.mat4:
        if self.identity_projection:
            return glm.mat4(1.0)
        if self._projection_dirty:
            params = self.ortho_params
            self._projection = glm.ortho(params.left, params.right, params.bottom,
                                         params.top, params.near, params.far)
            self._projection_dirty = False
        return self._projection

    # === View Matrix ===

    def lookAt(self, eye, target, up=glm.vec3(0.0, 1.0, 0.0)) -> "MSimple2D":
        """用"看某点"的方式摆相机（2D 一般不需要，做视差/倾斜时有用）。"""
        self._eye = glm.vec3(eye)
        self._target = glm.vec3(target)
        self._up = glm.vec3(up)
        self.use_look_at = True
        self._view_dirty = True
        return self

    def setZoom(self, zoom: float) -> "MSimple2D":
        """缩放（> 1 = 放大）。"""
        self.zoom = max(float(zoom), 1e-4)
        self._view_dirty = True
        return self

    def setRotation(self, degrees: float) -> "MSimple2D":
        """画面整体旋转（度）：世界跟着转，物体自身的 ``r`` 不用改。"""
        self.roll = float(degrees)
        self._view_dirty = True
        return self

    def setPosition(self, x: float, y: float, z: float = 0.0) -> "MSimple2D":
        """相机位置（2D 里就是"在看世界的哪一点"）。"""
        self._eye = glm.vec3(float(x), float(y), float(z))
        self.use_look_at = False
        self._view_dirty = True
        return self

    @property
    def view(self) -> glm.mat4:
        if self._view_dirty:
            self._view = self._buildView()
            self._view_dirty = False
        return self._view

    def _buildView(self) -> glm.mat4:
        """世界 → 视空间：先平移（-相机位置）、再绕 Z 转、最后缩放。"""
        if self.use_look_at:
            return glm.lookAt(self._eye, self._target, self._up)
        matrix = glm.mat4(1.0)
        matrix = glm.scale(matrix, glm.vec3(self.zoom, self.zoom, 1.0))
        if self.roll:
            matrix = glm.rotate(matrix, glm.radians(self.roll), glm.vec3(0.0, 0.0, 1.0))
        return glm.translate(matrix, -self._eye)

    def setCameraPosAndPYR(self, x: float, y: float, z: float, pitch: float = 0.0,
                           yaw: float = 0.0, roll: float = 0.0) -> glm.vec3:
        """和 :class:`~pyglgame.gameobject.camera.Camera` 约定的入口。

        - ``roll`` = 画面旋转（等价于 :meth:`setRotation`）；
        - ``pitch`` / ``yaw`` 只要有一个非 0，就切成 ``lookAt`` 的"伪 3D 视角"
          （2D 场景做视差、倾斜俯视时用）；
        - 两个都为 0（2D 的常态）时保持原来的纯 2D 平移 —— **老行为不变**。
        """
        self._eye = glm.vec3(float(x), float(y), float(z))
        self.roll = float(roll)
        pitch, yaw = float(pitch), float(yaw)
        if abs(pitch) > 1e-6 or abs(yaw) > 1e-6:
            direction = glm.normalize(glm.vec3(
                glm.cos(glm.radians(pitch)) * glm.sin(glm.radians(yaw)),
                glm.sin(glm.radians(pitch)),
                glm.cos(glm.radians(pitch)) * glm.cos(glm.radians(yaw))))
            right = glm.cross(direction, glm.vec3(0.0, 1.0, 0.0))
            up = (glm.cross(glm.normalize(right), direction)
                  if glm.length(right) > 1e-6 else glm.vec3(0.0, 0.0, 1.0))
            self._target = self._eye + direction
            self._up = up
            self.use_look_at = True
            self._view_dirty = True
            return direction
        self.use_look_at = False
        self._view_dirty = True
        return glm.vec3(0.0, 0.0, -1.0)

    # === 2D 光照 ===

    def useLighting(self, active: bool = True, ambient=None) -> "MSimple2D":
        """开关 2D 光照（``ambient`` 不传就用 ``RenderGlobal.ambient2D``）。"""
        self.lighting = bool(active)
        self.ambient = None if ambient is None else glm.vec3(*ambient)
        return self

    def ambientColor(self) -> glm.vec3:
        """当前 2D 环境光（``lighting`` 打开时上传给着色器）。"""
        if self.ambient is not None:
            return self.ambient
        return glm.vec3(RenderGlobal.instance.ambient2D)

    # === 世界 ⇄ 屏幕 ===

    @staticmethod
    def windowSize() -> tuple:
        """窗口像素尺寸 ``(w, h)``；拿不到返回 ``(0, 0)``。"""
        window = getattr(RenderGlobal.instance, "window", None)
        size = getattr(window, "size", None)
        if size is None:
            return (0, 0)
        return (int(size.w), int(size.h))

    def viewProjection(self) -> glm.mat4:
        """``projection * view``（世界 → 裁剪空间）。"""
        return self.projection * self.view

    def worldToScreen(self, x: float, y: float, z: float = 0.0) -> tuple:
        """世界坐标 → 像素坐标（原点在**左下角**，与 GL 的 gl_FragCoord 一致）。"""
        width, height = self.windowSize()
        clip = self.viewProjection() * glm.vec4(float(x), float(y), float(z), 1.0)
        if abs(clip.w) < 1e-9:
            return (0.0, 0.0)
        ndc_x, ndc_y = clip.x / clip.w, clip.y / clip.w
        return ((ndc_x * 0.5 + 0.5) * width, (ndc_y * 0.5 + 0.5) * height)

    def screenToWorld(self, px: float, py: float, z: float = 0.0) -> tuple:
        """像素坐标 → 世界坐标（把像素投到 ``z = z`` 那张平面上）。

        正交投影下这就是解析解；NDC 直通（单位矩阵投影 + 平移/缩放）同样成立。
        """
        width, height = self.windowSize()
        if width == 0 or height == 0:
            return (0.0, 0.0, float(z))
        ndc_x = float(px) / width * 2.0 - 1.0
        ndc_y = float(py) / height * 2.0 - 1.0
        matrix = self.viewProjection()
        # 先算"z 平面"对应的 NDC 深度（正交下 z 与 x/y 无关，取哪个 x/y 都一样）
        plane = matrix * glm.vec4(0.0, 0.0, float(z), 1.0)
        world = glm.inverse(matrix) * glm.vec4(ndc_x, ndc_y, plane.z / plane.w, 1.0)
        return (world.x, world.y, world.z)

    # === Uniform Helper ===

    def setCameraUniforms(self, shader: "Shader" = None) -> None:
        """上传矩阵 + 光照/相机相关 uniform（每帧由 ``Camera.renderStart`` 调用）。"""
        if shader is None:
            shader = RenderGlobal.instance.using_shader
        shader.uniformMatrix4fv("projection", self.projection)
        shader.uniformMatrix4fv("view", self.view)
        eye = self._eye
        shader.uniform3f("u_cameraPos", eye.x, eye.y, eye.z)
        if self.lighting:
            ambient = self.ambientColor()
            shader.uniform1i("u_light2D", 1)
            shader.uniform3f("u_ambientFlat", ambient.x, ambient.y, ambient.z)
        else:
            shader.uniform1i("u_light2D", 0)

    def __repr__(self) -> str:
        mode = "NDC 直通" if self.identity_projection else "正交"
        return (f"<MSimple2D {mode} 缩放={self.zoom:g} 旋转={self.roll:g}° "
                f"eye=({self._eye.x:.2f}, {self._eye.y:.2f}, {self._eye.z:.2f})"
                f"{' 光照' if self.lighting else ''}>")
