"""3D 物理世界：pybullet（Bullet 3）的 GameObject 包装（固定步长 + 事件 + 查询）。

设计要点（细节见 docs/物理引擎-3D.md）
------------------------------------

1. **一个世界 = 一个 pybullet 客户端**：``pybullet.connect(pybullet.DIRECT)``
   （``DIRECT`` = 无窗口，纯计算；渲染还是引擎自己画），互不干扰，也不弹窗。
2. **固定步长**：和 2D 完全一样 —— 引擎 update 是 100Hz，pybullet 按
   ``setTimeStep(fixed_step)`` 走，所以**每次** ``stepSimulation()`` 正好推进
   ``fixed_step`` 秒；累加器 + ``max_steps`` 防死亡螺旋。
3. **接触事件是"轮询出来"的**：pybullet 没有回调，只有 ``getContactPoints()``
   快照。所以每步之后对比"上一帧的接触对"和"这一帧的接触对"来生成
   begin/end 事件（和 2D 一样在步进**之后**派发，回调里可以增删刚体）。
4. **刚体形状在建体时确定**：pybullet 的 ``createMultiBody`` 不接受"事后加形状"，
   所以 :class:`Body3D` 的 ``add*`` 先**记规格**，等第一次真正需要时（世界推步
   或访问 ``body.body``）再一次性建出来（``_ensureBody``）。
5. **销毁推迟到下一步**：``body.delete()`` 立刻从注册表摘掉，``removeBody`` 排到
   下一步开头 —— 与 2D 同一套规则，避免在事件回调里销毁导致悬空引用。
"""

import math

from ..gameobject.GameObject import GameObject
from . import Backend
from .Body3D import BodyType, _pb, _packVector


#: ``getContactPoints`` 的字段顺序 —— **实测**：这个 build（202010061）返回 14 元组，
#: 新版本返回字典；两种都要兼容，所以统一走 :func:`_contactField`。
CONTACT_FIELDS = ("contactFlag", "bodyUniqueIdA", "bodyUniqueIdB", "linkIndexA",
                  "linkIndexB", "positionOnA", "positionOnB", "contactNormalOnB",
                  "contactDistance", "normalForce", "lateralFriction1",
                  "lateralFrictionDir1", "lateralFriction2", "lateralFrictionDir2")


def _contactField(contact, name: str, default=None):
    """从一次接触记录里取字段（14 元组或字典都支持）。"""
    if isinstance(contact, dict):
        return contact.get(name, default)
    try:
        index = CONTACT_FIELDS.index(name)
    except ValueError:
        return default
    if isinstance(contact, (list, tuple)) and len(contact) > index:
        return contact[index]
    return default


class ContactEvent3D:
    """一次接触事件（``begin`` = 开始接触，``end`` = 分开）。

    字段来自 pybullet 的 ``getContactPoints()`` 快照（世界坐标）。
    """

    __slots__ = ("kind", "body_a", "body_b", "point", "normal", "distance",
                 "normal_force", "world")

    def __init__(self, world, kind: str, contact) -> None:
        self.world = world
        self.kind = kind
        self.body_a = world._bodyOfId(_contactField(contact, "bodyUniqueIdA", -1))
        self.body_b = world._bodyOfId(_contactField(contact, "bodyUniqueIdB", -1))
        point = _contactField(contact, "positionOnA") or (0.0, 0.0, 0.0)
        normal = _contactField(contact, "contactNormalOnB") or (0.0, 0.0, 0.0)
        self.point = (float(point[0]), float(point[1]), float(point[2]))
        self.normal = (float(normal[0]), float(normal[1]), float(normal[2]))
        self.distance = float(_contactField(contact, "contactDistance", 0.0))
        self.normal_force = float(_contactField(contact, "normalForce", 0.0))

    @property
    def bodies(self) -> tuple:
        """``(body_a, body_b)``。"""
        return (self.body_a, self.body_b)

    @property
    def isBegin(self) -> bool:
        return self.kind == "begin"

    def other(self, body):
        """``event.other(self)`` —— 拿对面那个刚体。"""
        return self.body_b if body is self.body_a else self.body_a

    def __repr__(self) -> str:
        return (f"<ContactEvent3D {self.kind} {self.body_a} × {self.body_b} "
                f"force={self.normal_force:.1f}>")


class RayHit3D:
    """一次射线命中（:meth:`Physics3D.rayCast` 的返回值）。"""

    __slots__ = ("body", "point", "normal", "fraction")

    def __init__(self, body, point, normal, fraction) -> None:
        self.body = body
        self.point = tuple(float(v) for v in point)
        self.normal = tuple(float(v) for v in normal)
        self.fraction = float(fraction)

    @property
    def distance(self) -> float:
        """命中点在射线上的比例（0 = 起点，1 = 终点）。"""
        return self.fraction

    def __repr__(self) -> str:
        return (f"<RayHit3D {self.body} at ({self.point[0]:.2f}, {self.point[1]:.2f}, "
                f"{self.point[2]:.2f}) f={self.fraction:.3f}>")


class Physics3D(GameObject):
    """3D 物理世界（pybullet 的一个客户端；它自己进渲染层的更新循环）。

    典型用法::

        world = Physics3D(gravity=(0.0, -9.8, 0.0))      # 建好就跟着主循环跑
        Body3D(world, body_type="static", y=-1.0).addBox(50.0, 1.0, 50.0)
        Body3D(world, x=0.0, y=5.0).addBox(1.0, 1.0, 1.0, mass=1.0)

    想暂停：``world.paused = True``（或 ``world.active = False``）。
    """

    is_physics_3d = True

    def __new__(cls, *args, **kwargs):
        # GameObject.__new__ 只认 is_camera：构造参数在这里吃掉（同 Body2D/FogVolume）
        return super().__new__(cls)

    def __init__(self, gravity=(0.0, -9.8, 0.0), fixed_step: float = 1.0 / 60.0,
                 max_steps: int = 5, solver_iterations: int = 50,
                 do_sleep: bool = True, name: str = None) -> None:
        super().__init__()
        pb = _pb()
        self.name = name
        #: pybullet 客户端 id（一个世界一个客户端；``DIRECT`` = 不开小窗口）
        self.client = pb.connect(pb.DIRECT)
        if self.client < 0:
            raise RuntimeError("pybullet.connect(pybullet.DIRECT) 失败（客户端数量超了？）")
        #: 固定步长（秒）；pybullet 的 timeStep 就是这个值
        self.fixed_step = float(fixed_step)
        #: 单帧最多补几步（防死亡螺旋）
        self.max_steps = max(1, int(max_steps))
        #: 求解迭代次数（pybullet 默认 50；调小更快、调大更稳）
        self.solver_iterations = int(solver_iterations)
        #: 新建刚体是否允许休眠（pybullet 的休眠是**逐刚体**的激活状态）
        self.do_sleep = bool(do_sleep)
        #: 重力（pybullet 这个 build 没有 getGravity，引擎自己记一份）
        self._gravity = (0.0, -9.8, 0.0)
        pb.resetSimulation(physicsClientId=self.client)
        pb.setTimeStep(self.fixed_step, physicsClientId=self.client)
        self._applyEngineParams()
        #: 注册的刚体（:class:`Body3D`）
        self.bodies: list = []
        #: 注册的关节（:class:`Joint3D`）
        self.joints: list = []
        #: 暂停开关
        self.paused = False
        #: 模拟时间（秒，只有真的推步才涨）
        self.time = 0.0
        #: 上一次 update 实际推了几步
        self.sub_steps = 0
        #: 世界级接触回调：``world.on_contact = func(event)``
        self.on_contact = None
        #: 最近一次派发的接触事件（只读）
        self.last_events: list = []
        self._accumulator = 0.0
        self._events: list = []
        self._touching: dict = {}       # 上一帧的接触对 {(idA, idB): contact}
        self._pending_destroy: list = []
        self._body_ids: dict = {}       # pybullet 的 body id → Body3D
        self.setGravity(gravity)        # 必须在 bodies/joints 初始化之后（它会唤醒刚体）

    def _applyEngineParams(self) -> None:
        """下发求解器参数（逐个试，老版本 pybullet 缺某个字段时不至于整体失败）。"""
        pb = _pb()
        for key, value in (("fixedTimeStep", self.fixed_step),
                           ("numSolverIterations", self.solver_iterations),
                           ("numSubSteps", 1),
                           ("deterministicOverlappingPairs", 1),
                           ("enableFileCaching", 0)):
            try:
                pb.setPhysicsEngineParameter(**{key: value},
                                            physicsClientId=self.client)
            except Exception:
                pass

    # === 注册表（由 Body3D / Joint3D 调用）===

    def _addBody(self, body) -> None:
        if body not in self.bodies:
            self.bodies.append(body)

    def _registerBodyId(self, body) -> None:
        """刚体真正建出来之后登记 id（查询/事件要靠它反查）。"""
        if getattr(body, "body", None) is not None:
            self._body_ids[int(body.body)] = body

    def _removeBody(self, body) -> None:
        """摘掉刚体并**排队**销毁 pybullet 刚体（下一步开头执行）。

        顺序很重要：**先**把挂在它身上的约束排队删掉，**再**删刚体 ——
        否则 pybullet 会抱怨 ``removeConstraint failed``（约束的两端没了一个）。
        """
        if body in self.bodies:
            self.bodies.remove(body)
        for joint in tuple(self.joints):
            if joint.body_a is body or joint.body_b is body:
                joint.delete()              # 摘掉关节 + 排队删约束
        handle = getattr(body, "body", None)
        if handle is not None:
            self._pending_destroy.append(int(handle))

    def _addJoint(self, joint) -> None:
        if joint not in self.joints:
            self.joints.append(joint)

    def _removeJoint(self, joint) -> None:
        if joint in self.joints:
            self.joints.remove(joint)
        handles = list(getattr(joint, "constraints", None) or ())
        if not handles and getattr(joint, "constraint", None) is not None:
            handles = [joint.constraint]
        for handle in handles:
            self._pending_destroy.append(("constraint", int(handle)))

    def _bodyOfId(self, body_id):
        """pybullet 的 body id → :class:`Body3D`（找不到返回 ``None``）。"""
        if body_id is None:
            return None
        return self._body_ids.get(int(body_id))

    def flushPending(self) -> int:
        """真正执行排队中的销毁（每次推步开头调用）。返回销毁了几个对象。"""
        if not self._pending_destroy:
            return 0
        pb = _pb()
        pending, self._pending_destroy = self._pending_destroy, []
        for handle in pending:
            try:
                if isinstance(handle, tuple):
                    pb.removeConstraint(handle[1], physicsClientId=self.client)
                else:
                    pb.removeBody(handle, physicsClientId=self.client)
                    self._body_ids.pop(int(handle), None)
            except Exception:
                pass
        return len(pending)

    def _commitBodies(self) -> None:
        """把所有"还没建出来"的刚体建出来（pybullet 的形状必须建体时给全）。"""
        for body in tuple(self.bodies):
            body._ensureBody()

    # === 推步 ===

    def update(self, dt: float, tps: float) -> None:
        """引擎每个固定步调用一次（默认 100Hz）：攒够 ``fixed_step`` 就推一步。"""
        if self.paused:
            self.sub_steps = 0
            self.flushPending()
            return
        step = self.fixed_step
        if step <= 0.0:
            self.stepBy(dt)
            self.sub_steps = 1
        else:
            self._accumulator += float(dt)
            count = 0
            while self._accumulator >= step and count < self.max_steps:
                self.stepFixed()
                self._accumulator -= step
                count += 1
            if count >= self.max_steps:
                self._accumulator = 0.0        # 卡顿/断点后丢积压，避免连续补帧
            self.sub_steps = count
        super().update(dt, tps)

    def stepFixed(self) -> bool:
        """按 ``fixed_step`` 推进一步（含事件派发与位姿回写）。"""
        return self.stepBy(self.fixed_step)

    def stepBy(self, dt: float) -> bool:
        """用**指定**的 dt 推进一步（手动控制 / 离线复现 / 单测用）。"""
        dt = float(dt)
        if dt <= 0.0:
            raise ValueError(f"dt 必须为正数，收到 {dt}")
        pb = _pb()
        self.flushPending()
        self._commitBodies()
        temporary = abs(dt - self.fixed_step) > 1e-12
        if temporary:
            pb.setTimeStep(dt, physicsClientId=self.client)
        try:
            pb.stepSimulation(physicsClientId=self.client)
        finally:
            if temporary:
                pb.setTimeStep(self.fixed_step, physicsClientId=self.client)
        self.time += dt
        self._collectContacts()
        self._dispatchEvents()
        self.syncBodies()
        return True

    def syncBodies(self) -> None:
        """把物理状态写回所有刚体对象的 ``x/y/z/p/t/r``（每步之后调用）。"""
        for body in tuple(self.bodies):
            body.syncFromBody()

    def _collectContacts(self) -> None:
        """对比两帧的接触对，生成 begin/end 事件（pybullet 只有接触快照，没有回调）。"""
        pb = _pb()
        try:
            contacts = pb.getContactPoints(physicsClientId=self.client)
        except Exception:
            contacts = []
        current = {}
        for contact in contacts or ():
            try:
                id_a = int(_contactField(contact, "bodyUniqueIdA", -1))
                id_b = int(_contactField(contact, "bodyUniqueIdB", -1))
            except Exception:
                continue
            if id_a < 0 or id_b < 0 or id_a == id_b:
                continue
            if id_a not in self._body_ids and id_b not in self._body_ids:
                continue                       # 与我们无关的接触（理论上不会有）
            pair = (min(id_a, id_b), max(id_a, id_b))
            current[pair] = contact
        for pair, contact in current.items():
            if pair not in self._touching:
                self._events.append(ContactEvent3D(self, "begin", contact))
        for pair, contact in self._touching.items():
            if pair not in current:
                self._events.append(ContactEvent3D(self, "end", contact))
        self._touching = current

    def _dispatchEvents(self) -> None:
        """推步之后统一派发接触事件（顺序同 2D：世界 → 世界方法 → 两个刚体）。"""
        events, self._events = self._events, []
        self.last_events = events
        if not events:
            return
        hook = self.on_contact
        for event in events:
            if hook is not None:
                hook(event)
            self.onContact(event)
            for body in event.bodies:
                if body is None:
                    continue
                callback = getattr(body, "on_contact", None)
                if callback is not None:
                    callback(event)
                body.onContact(event)

    def onContact(self, event) -> None:
        """世界级接触回调（子类覆写它，或设 ``world.on_contact``）。"""


    # === 查询（射线 / 区域 / 点）===

    #: ``rayCastAll`` 最多取多少次命中（pybullet 只给"最近命中"，全命中靠步进式重投）
    MAX_RAYCAST_ALL = 16

    def rayCast(self, start, end, closest: bool = True, mask=None, group=None,
                callback=None):
        """从 ``start`` 射到 ``end``。

        - ``closest=True``（默认）→ 最近的 :class:`RayHit3D`，没命中返回 ``None``
        - ``closest=False`` → 全部命中（pybullet 只给最近命中，这里"命中一次往前挪一点
          再投一次"，最多 :data:`MAX_RAYCAST_ALL` 次；``fraction`` 已换算到整条射线上）
        - ``mask`` / ``group``：交给 pybullet 的碰撞过滤（默认都打）
        - ``callback(hit)`` 返回 ``False`` 可提前终止
        """
        pb = _pb()
        start_point = _packVector(start, "射线起点")
        end_point = _packVector(end, "射线终点")
        kwargs = {"physicsClientId": self.client}
        if mask is not None:
            kwargs["collisionFilterMask"] = int(mask)
        if group is not None:
            kwargs["collisionFilterGroup"] = int(group)

        total = math.dist(start_point, end_point)
        if total <= 0.0:
            raise ValueError("射线的起点和终点不能重合")
        direction = tuple((end_point[i] - start_point[i]) / total for i in range(3))

        hits = []
        travelled = 0.0
        cursor = start_point
        attempts = 1 if closest else self.MAX_RAYCAST_ALL
        for _ in range(attempts):
            result = pb.rayTest(list(cursor), list(end_point), **kwargs)
            if not result:
                break
            # ⚠ 实测：这个 build 的 rayTest 返回**嵌套**的一元组：((bodyId, link, frac, pos, normal),)
            entry = result[0] if isinstance(result[0], (list, tuple)) else result
            if not entry or int(entry[0]) < 0:
                break
            object_id, _link, fraction, hit_position, hit_normal = entry[:5]
            distance = travelled + float(fraction) * math.dist(cursor, end_point)
            hit = RayHit3D(self._bodyOfId(object_id), hit_position, hit_normal,
                           distance / total)
            hits.append(hit)
            if callback is not None and callback(hit) is False:
                break
            if closest:
                break
            travelled = distance + max(total * 1.0e-3, 1.0e-4)   # 挪过这一刀，继续找下一个
            if travelled >= total:
                break
            cursor = tuple(start_point[i] + direction[i] * travelled for i in range(3))
        if closest:
            return hits[0] if hits else None
        return hits

    def rayCastAll(self, start, end, **kwargs) -> list:
        """射线上**所有**命中（按远近排序）。"""
        kwargs["closest"] = False
        return self.rayCast(start, end, **kwargs)

    def queryAABB(self, minimum, maximum) -> list:
        """立方体区域查询：返回区域内的**刚体**（pybullet 的 ``getOverlappingObjects``）。"""
        pb = _pb()
        low = _packVector(minimum, "区域最小角")
        high = _packVector(maximum, "区域最大角")
        result = pb.getOverlappingObjects(list(low), list(high),
                                          physicsClientId=self.client)
        bodies = []
        for item in result or ():
            object_id = item[0] if isinstance(item, (list, tuple)) else item
            body = self._bodyOfId(object_id)
            if body is not None and body not in bodies:
                bodies.append(body)
        return bodies

    def queryPoint(self, x, y=None, z=None) -> list:
        """点查询：返回**压在这个点上**的刚体（先 AABB 再精确判定 → 鼠标拾取用）。"""
        if y is None and z is None:
            point = _packVector(x, "点")
        else:
            point = (float(x), float(y), float(z))
        return [body for body in self.queryAABB(point, point)
                if body.containsPoint(point)]

    def wakeBodiesIn(self, area, margin: float = 0.1) -> int:
        """唤醒区域里睡着的动态刚体（``area`` = ``Body3D.aabb()`` 那种 ``(min, max)``）。

        运动学平台每帧移动之后调一次（:meth:`Body3D.syncToBody` 已经帮你调了）：
        睡着的刚体不会被移动的平台自动叫醒，这是物理引擎的通用坑。

        用**引擎自己的 AABB** 判定（不查 pybullet）：对睡着的刚体也一定有效，
        而且不用为每次唤醒做一次跨进程查询。
        """
        low, high = area
        minimum = tuple(min(low[i], high[i]) - margin for i in range(3))
        maximum = tuple(max(low[i], high[i]) + margin for i in range(3))
        count = 0
        for body in tuple(self.bodies):
            if body.body_type != BodyType.DYNAMIC or body.awake:
                continue
            body_low, body_high = body.aabb()
            if all(body_low[i] <= maximum[i] and body_high[i] >= minimum[i]
                   for i in range(3)):
                body.wake()
                count += 1
        return count

    def bodyOf(self, body_id):
        """pybullet 的 body id → :class:`Body3D`（射线/接触回调里反查用）。"""
        return self._bodyOfId(body_id)

    # === 世界级开关 / 清理 ===

    @property
    def gravity(self) -> tuple:
        """重力向量 ``(gx, gy, gz)``。

        ⚠ pybullet 这个 build **没有** ``getGravity``，所以引擎自己记一份
        （``setGravity`` 时更新；默认 ``(0, -9.8, 0)`` —— 引擎是 **y-up**，
        而 pybullet 的默认重力是沿 **-Z**，所以 ``Physics3D`` 初始化时一定会显式设一次）。
        """
        return self._gravity

    @gravity.setter
    def gravity(self, value) -> None:
        self.setGravity(value)

    def setGravity(self, gx, gy=None, gz=None) -> "Physics3D":
        """设置重力（``(0, 0, 0)`` = 失重；y 负值 = 往下掉）。"""
        if gy is None and gz is None:
            vector = _packVector(gx, "gravity")
        else:
            vector = (float(gx), float(gy), float(gz))
        pb = _pb()
        pb.setGravity(vector[0], vector[1], vector[2], physicsClientId=self.client)
        self._gravity = vector
        for body in self.bodies:
            body.wake()
        return self

    @property
    def bodyCount(self) -> int:
        """注册的 :class:`Body3D` 数量。"""
        return len(self.bodies)

    @property
    def jointCount(self) -> int:
        """注册的关节数量。"""
        return len(self.joints)

    def pause(self) -> "Physics3D":
        self.paused = True
        return self

    def resume(self) -> "Physics3D":
        self.paused = False
        return self

    def togglePause(self) -> bool:
        self.paused = not self.paused
        return self.paused

    def clear(self) -> None:
        """清空世界：所有关节与刚体都注销并销毁。"""
        for joint in tuple(self.joints):
            joint.delete()
        for body in tuple(self.bodies):
            body.delete()
        self.flushPending()
        self._touching = {}

    def delete(self) -> None:
        """销毁整个世界（清空 + 断开 pybullet 客户端 + 注销自己）。"""
        if getattr(self, "_deleted", False):
            return
        self.clear()
        try:
            _pb().disconnect(self.client)
        except Exception:
            pass
        super().delete()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return (f"<Physics3D{label} gravity={self.gravity} bodies={len(self.bodies)} "
                f"joints={len(self.joints)} t={self.time:.2f}s"
                f"{' paused' if self.paused else ''}>")
