"""物理引擎（2D：Box2D / pybox2d；3D：pybullet / Bullet 3）。

五行走起来（2D）::

    from pyglgame.physics import Physics2D, Body2D

    world = Physics2D(gravity=(0.0, -9.8))            # 建好就跟着主循环固定步长跑
    Body2D(world, body_type="static", y=-3.0).addBox(30.0, 0.5, friction=0.6)
    crate = Body2D(world, x=0.0, y=3.0)
    crate.addBox(0.8, 0.8, density=1.0, restitution=0.1)

    print(crate.x, crate.y, crate.r)                  # 每步之后自动回写（r 是**度**）
    crate.applyImpulse(2.0, 0.0)                      # 只对 dynamic 有效

五行走起来（3D，**y-up**）::

    from pyglgame.physics import Physics3D, Body3D, DebugDraw3D

    world3 = Physics3D(gravity=(0.0, -9.8, 0.0))
    Body3D(world3, body_type="static", y=-1.0).addBox(60.0, 1.0, 60.0, friction=0.9)
    crate3 = Body3D(world3, x=0.0, y=6.0, yaw=20.0)
    crate3.addBox(1.0, 1.0, 1.0, mass=2.0)
    DebugDraw3D(world3)                               # 形状线框（不查 pybullet）

约定速查
--------

- **单位** = 引擎的世界单位（当"米"用，物体尺寸 0.1~10 最稳）
- 2D：``x / y`` = 质心；``z`` 只给 2D 场景分层（物理不参与）
- 3D：``x / y / z`` 是刚体原点，姿态用 ``p / t / r``（pitch/yaw/roll，**度**），
  与渲染器 ``Rx·Ry·Rz`` 同源；胶囊/圆柱的轴向对外统一是 **Y**（立着）
- 角度：``r`` 是度、``angle`` 是弧度；逆时针为正（3D 的角速度是弧度/秒）
- 动态刚体的位姿归物理所有（瞬移用 ``teleport``）；静态/运动学刚体归引擎这侧
  （每帧自动推给物理）
- 固定步长默认 1/60，单帧最多补 5 步（防死亡螺旋）
- 接触事件都是"推步之后"派发（pybullet 靠轮询快照做 begin/end 差分）→
  回调里增删刚体是安全的

后端
----

- **2D**：Box2D 2.3.10 预编译包随引擎分发在 ``pyglgame/libs/Box2D``；
- **3D**：pybullet 3.2.7 是 ``pyglgame/libs/pybullet.cp310-win_amd64.pyd``
  （PyPI 上没有 cp310-win 的 wheel，这份是**本机源码编译**的，步骤见
  ``docs/物理引擎-3D.md``）。

两者**只有 Python 3.10 能加载**。用别的解释器 ``import pyglgame.physics`` 仍然安全
（后端懒加载），但真的要建世界时会抛 :class:`PhysicsUnavailable`，错误信息里直接给出
该敲的命令 —— 见 :mod:`pyglgame.physics.Backend`。

文档：2D 看 ``docs/物理引擎.md``，3D 看 ``docs/物理引擎-3D.md``（含实测踩坑清单）。
演示：``Game7.py``（2D 沙盒）/ ``Game8.py``（3D 沙盒）。
"""

from .Backend import (PhysicsUnavailable, available, available3D, lastError,
                      lastError3D, load, load3D, modulePath, modulePath3D, version,
                      version3D)
from .Body2D import (ALL_BODY_TYPES, SHAPE_BOX, SHAPE_CHAIN, SHAPE_CIRCLE,
                     SHAPE_EDGE, SHAPE_POLYGON, Body2D, BodyType, Fixture2D)
from .Body3D import (SHAPE_CAPSULE, SHAPE_CONVEX, SHAPE_CYLINDER, SHAPE_PLANE,
                     SHAPE_SPHERE, Body3D, ShapeSpec3D)
from .DebugDraw2D import DebugDraw2D
from .DebugDraw3D import DebugDraw3D
from .Joint2D import DistanceJoint2D, Joint2D, MouseJoint2D, RevoluteJoint2D
from .Joint3D import (BallSocketJoint3D, FixedJoint3D, HingeJoint3D, Joint3D,
                      SliderJoint3D)
from .Physics2D import ContactEvent, Physics2D, RayHit
from .Physics3D import ContactEvent3D, Physics3D, RayHit3D
from .Transform3D import eulerToQuaternion, forwardVector, quaternionToEuler

__all__ = [
    # 后端
    "PhysicsUnavailable", "available", "lastError", "load", "modulePath", "version",
    "available3D", "lastError3D", "load3D", "modulePath3D", "version3D",
    # 世界
    "Physics2D", "ContactEvent", "RayHit",
    "Physics3D", "ContactEvent3D", "RayHit3D",
    # 刚体 / 形状
    "Body2D", "BodyType", "ALL_BODY_TYPES", "Fixture2D",
    "SHAPE_BOX", "SHAPE_CIRCLE", "SHAPE_POLYGON", "SHAPE_EDGE", "SHAPE_CHAIN",
    "Body3D", "ShapeSpec3D",
    "SHAPE_SPHERE", "SHAPE_CAPSULE", "SHAPE_CYLINDER", "SHAPE_PLANE", "SHAPE_CONVEX",
    # 关节
    "Joint2D", "DistanceJoint2D", "RevoluteJoint2D", "MouseJoint2D",
    "Joint3D", "BallSocketJoint3D", "HingeJoint3D", "SliderJoint3D", "FixedJoint3D",
    # 调试
    "DebugDraw2D", "DebugDraw3D",
    # 姿态转换
    "eulerToQuaternion", "quaternionToEuler", "forwardVector",
]
