from typing import TYPE_CHECKING
import glm
from dataclasses import dataclass
from .MBase import MBase
from ....RenderGlobal import RenderGlobal
if TYPE_CHECKING:
    from ....shader.Shader import Shader


@dataclass
class OrthographicParams:
    left: float = -10.0
    right: float = 10.0
    bottom: float = -10.0
    top: float = 10.0
    near: float = 0.1
    far: float = 10000.0


class MOrthographic(MBase):
    def __init__(self) -> None:
        # === Projection ===
        self.ortho_params = OrthographicParams()
        self._projection_dirty = True
        self._projection = glm.mat4(1.0)

        # === View ===
        self._view_dirty = True
        self._view = glm.mat4(1.0)
        self._eye = glm.vec3(0.0, 0.0, 3.0)
        self._target = glm.vec3(0.0, 0.0, 0.0)
        self._up = glm.vec3(0.0, 1.0, 0.0)

    # === Projection Matrix ===
    def setOrthographic(self, left=None, right=None, bottom=None, top=None, near=None, far=None) -> None:
        p = self.ortho_params
        if left is not None:
            p.left = left
        if right is not None:
            p.right = right
        if bottom is not None:
            p.bottom = bottom
        if top is not None:
            p.top = top
        if near is not None:
            p.near = near
        if far is not None:
            p.far = far
        self._projection_dirty = True

    def setBySize(self, width, height, near=None, far=None) -> None:
        """通过宽度和高度设置正交投影"""
        half_w = width / 2.0
        half_h = height / 2.0
        self.setOrthographic(
            left=-half_w,
            right=half_w,
            bottom=-half_h,
            top=half_h,
            near=near,
            far=far
        )

    @property
    def projection(self):
        if self._projection_dirty:
            p = self.ortho_params
            self._projection = glm.ortho(
                p.left, p.right, p.bottom, p.top, p.near, p.far
            )
            self._projection_dirty = False
        return self._projection

    def updateAspectFromSize(self, width, height) -> None:
        """根据窗口尺寸更新正交投影范围，保持宽高比"""
        if height == 0:
            return
        aspect = width / height
        
        # 计算当前高度范围
        current_height = self.ortho_params.top - self.ortho_params.bottom
        # 保持高度不变，根据宽高比调整宽度
        new_width = current_height * aspect
        center_x = (self.ortho_params.left + self.ortho_params.right) / 2.0
        center_y = (self.ortho_params.bottom + self.ortho_params.top) / 2.0
        
        self.setOrthographic(
            left=center_x - new_width / 2.0,
            right=center_x + new_width / 2.0,
            bottom=center_y - current_height / 2.0,
            top=center_y + current_height / 2.0
        )

    # === View Matrix ===
    # 以下视图矩阵方法与透视投影完全一致
    def lookAt(self, eye, target, up=glm.vec3(0.0, 1.0, 0.0)) -> None:
        self._eye = eye
        self._target = target
        self._up = up
        self._view_dirty = True

    def lookAtEuler(self, position: glm.vec3, pitch: float, yaw: float, roll: float = 0.0) -> glm.vec3:
        pitch_rad = glm.radians(pitch)
        yaw_rad = glm.radians(yaw)
        roll_rad = glm.radians(roll)

        direction = glm.vec3(
            glm.cos(pitch_rad) * glm.sin(yaw_rad),
            glm.sin(pitch_rad),
            glm.cos(pitch_rad) * glm.cos(yaw_rad)
        )
        direction = glm.normalize(direction)

        world_up = glm.vec3(0.0, 1.0, 0.0)
        right = glm.normalize(glm.cross(direction, world_up))
        up = glm.cross(right, direction)

        if roll != 0.0:
            roll_matrix = glm.rotate(glm.mat4(1.0), roll_rad, direction)
            up = glm.vec3(roll_matrix * glm.vec4(up, 0.0))

        self.lookAt(position, position + direction, up)
        return direction

    @property
    def view(self):
        if self._view_dirty:
            self._view = glm.lookAt(self._eye, self._target, self._up)
            self._view_dirty = False
        return self._view

    # === Uniform Helper ===
    def setCameraUniforms(self, shader: "Shader" = None) -> None:
        if shader is None:
            shader = RenderGlobal.instance.using_shader
        shader.uniformMatrix4fv("projection", self.projection)
        shader.uniformMatrix4fv("view", self.view)
        # 高光的视线方向需要相机世界坐标（这个相机也能配 2D 光照，由 MSimple2D 开关）
        shader.uniform3f("u_cameraPos", self._eye.x, self._eye.y, self._eye.z)
        # 正交相机默认按 3D 方式着色；要 2D 光照请用 MSimple2D.useLighting()
        shader.uniform1i("u_light2D", 0)

    def setCameraPosAndPYR(self, x: float, y: float, z: float, pitch: float, yaw: float, roll: float = 0.0) -> glm.vec3:
        return self.lookAtEuler(glm.vec3(x, y, z), pitch, yaw, roll)