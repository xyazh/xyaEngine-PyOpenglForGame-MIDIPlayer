"""3D 姿态转换：引擎欧拉角 ↔ 四元数（渲染与物理必须用**同一套**约定）。

约定（与 ``RenderGlobal.setRotation`` 完全一致）
------------------------------------------------

引擎的 ``p / t / r`` 是**度**，模型矩阵是::

    M = Rx(p) · Ry(t) · Rz(r)

（``pitch`` 绕 X、``yaw`` 绕 Y、``roll`` 绕 Z；和 :meth:`Camera` 的
``setCameraPosAndPYR`` 是同一套 —— 所以``t=90`` 时物体的局部 ``+Z`` 指向世界 ``+X``。）

pybullet 用四元数，顺序是 ``(x, y, z, w)``。这里用 ``glm`` 按上面的矩阵约定
做**双向**转换，保证"物理摆到哪就渲染到哪"：

- :func:`eulerToQuaternion`：``(p, t, r)`` 度 → ``(x, y, z, w)``
- :func:`quaternionToEuler`：``(x, y, z, w)`` → ``(p, t, r)`` 度
- :func:`forwardVector`：从 ``(p, t, r)`` 求"前方"（与相机公式一致）

反解时把 ``t`` 收在 ``[-90°, 90°]``：这样 ``cos(t) >= 0``，公式无歧义（也避开
万向节死锁的分支），于是 ``euler → quat → euler`` 对任意输入都**往返一致**。
"""

import math

import glm


def eulerToQuaternion(pitch: float, yaw: float, roll: float) -> tuple:
    """``(pitch, yaw, roll)``（度）→ 四元数 ``(x, y, z, w)``。"""
    matrix = glm.mat4(1.0)
    matrix = glm.rotate(matrix, glm.radians(float(pitch)), glm.vec3(1.0, 0.0, 0.0))
    matrix = glm.rotate(matrix, glm.radians(float(yaw)), glm.vec3(0.0, 1.0, 0.0))
    matrix = glm.rotate(matrix, glm.radians(float(roll)), glm.vec3(0.0, 0.0, 1.0))
    quaternion = glm.quat_cast(glm.mat3(matrix))
    return (float(quaternion.x), float(quaternion.y), float(quaternion.z),
            float(quaternion.w))


def quaternionToEuler(x, y=None, z=None, w=None) -> tuple:
    """四元数 ``(x, y, z, w)`` → ``(pitch, yaw, roll)``（度）。

    也接受直接传一个长度 4 的序列或 ``glm.quat``：``quaternionToEuler(q)``。
    """
    if y is None and z is None and w is None:
        values = tuple(float(value) for value in x)
    else:
        values = (float(x), float(y), float(z), float(w))
    quaternion = glm.quat(values[3], values[0], values[1], values[2])   # w, x, y, z
    matrix = glm.mat3_cast(glm.normalize(quaternion))

    # ⚠ pyglm 的矩阵是**列主序**：matrix[col][row]，所以 M[row][col] = matrix[col][row]
    def at(row: int, col: int) -> float:
        return float(matrix[col][row])

    # M = Rx(p)·Ry(t)·Rz(r) 的解析形式（见模块文档的推导）
    sin_yaw = max(-1.0, min(1.0, at(0, 2)))
    yaw = math.asin(sin_yaw)
    if abs(sin_yaw) > 1.0 - 1e-6:
        # 万向节死锁（t ≈ ±90°）：p 与 r 只以 (p+r) 或 (p-r) 出现 → 把 r 归零。
        # ⚠ 阈值放过 1e-6（≈0.006°）：判太紧会让 cy≈1e-4 时的除法把噪声放大成几十度误差。
        pitch = math.atan2(at(2, 1), at(1, 1))
        return (math.degrees(pitch), math.degrees(yaw), 0.0)
    pitch = math.atan2(-at(1, 2), at(2, 2))
    roll = math.atan2(-at(0, 1), at(0, 0))
    return (math.degrees(pitch), math.degrees(yaw), math.degrees(roll))


def forwardVector(pitch: float, yaw: float, roll: float = 0.0) -> tuple:
    """从 ``(p, t, r)`` 求"前方"—— 与 :meth:`MPerspective.setCameraPosAndPYR` 同一公式。

    （用它做"物体朝前"的移动：``x += forward[0] * speed``。）
    """
    pitch_rad = math.radians(float(pitch))
    yaw_rad = math.radians(float(yaw))
    return (math.cos(pitch_rad) * math.sin(yaw_rad),
            math.sin(pitch_rad),
            math.cos(pitch_rad) * math.cos(yaw_rad))
