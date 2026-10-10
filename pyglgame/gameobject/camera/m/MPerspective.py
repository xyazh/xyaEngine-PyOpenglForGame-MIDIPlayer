from typing import TYPE_CHECKING
import glm
from dataclasses import dataclass
from .MBase import MBase
from ....RenderGlobal import RenderGlobal
if TYPE_CHECKING:
    from ....shader.Shader import Shader


@dataclass
class PerspectiveParams:
    fov: float = 45.0
    aspect: float = 1.0
    near: float = 0.1
    far: float = 10000.0


class MPerspective(MBase):
    def __init__(self) -> None:
        # === Projection ===
        self.perspective_params = PerspectiveParams()
        self._projection_dirty = True
        self._projection = glm.mat4(1.0)

        # === View ===
        self._view_dirty = True
        self._view = glm.mat4(1.0)
        self._eye = glm.vec3(0.0, 0.0, 3.0)
        self._target = glm.vec3(0.0, 0.0, 0.0)
        self._up = glm.vec3(0.0, 1.0, 0.0)

    # === Projection Matrix ===

    def setPerspective(self, fov=None, aspect=None, near=None, far=None) -> None:
        p = self.perspective_params
        if fov is not None:
            p.fov = fov
        if aspect is not None:
            p.aspect = aspect
        if near is not None:
            p.near = near
        if far is not None:
            p.far = far
        self._projection_dirty = True

    @property
    def projection(self):
        if self._projection_dirty:
            p = self.perspective_params
            self._projection = glm.perspective(
                glm.radians(p.fov), p.aspect, p.near, p.far)
            self._projection_dirty = False
        return self._projection

    def updateAspectFromSize(self, width, height) -> None:
        if height != 0:
            self.setPerspective(aspect=width / height)

    # === View Matrix ===
    def lookAt(self, eye, target, up=glm.vec3(0.0, 1.0, 0.0)) -> None:
        self._eye = eye
        self._target = target
        self._up = up
        self._view_dirty = True

    def lookAtEuler(self, position: glm.vec3, pitch: float, yaw: float, roll: float = 0.0) -> glm.vec3:
        """
        从相机位置 + 欧拉角 计算出 view 矩阵。
        pitch: 上下（x轴旋转）
        yaw: 左右（y轴旋转）
        roll: 倾斜（z轴旋转）
        """
        # 转换为弧度
        pitch_rad = glm.radians(pitch)
        yaw_rad = glm.radians(yaw)
        roll_rad = glm.radians(roll)

        # 方向向量 (单位向量)
        direction = glm.vec3(
            glm.cos(pitch_rad) * glm.sin(yaw_rad),
            glm.sin(pitch_rad),
            glm.cos(pitch_rad) * glm.cos(yaw_rad)
        )
        direction = glm.normalize(direction)

        # 默认 up 向量
        world_up = glm.vec3(0.0, 1.0, 0.0)

        # 滚转（roll）影响的是右向量与上向量的方向
        right = glm.normalize(glm.cross(direction, world_up))
        up = glm.cross(right, direction)

        if roll != 0.0:
            # 绕方向向量旋转 up
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
        if shader == None:
            shader = RenderGlobal.instance.using_shader
        shader.uniformMatrix4fv("projection", self.projection)
        shader.uniformMatrix4fv("view", self.view)
        # 光照需要相机世界坐标（高光的视线方向）
        eye = self._eye
        shader.uniform3f("u_cameraPos", eye.x, eye.y, eye.z)
        # 这台相机是 3D：明确关掉 2D 光照模式（同一帧里 2D 相机可能先跑过，
        # u_light2D 是 uber shader 的持久 uniform，不覆盖就会让 3D 也用 2D 的环境光）
        shader.uniform1i("u_light2D", 0)

    def setCameraPosAndPYR(self, x: float, y: float, z: float, pitch: float, yaw: float, roll: float = 0.0) -> glm.vec3:
        return self.lookAtEuler(glm.vec3(x, y, z), pitch, yaw, roll)
