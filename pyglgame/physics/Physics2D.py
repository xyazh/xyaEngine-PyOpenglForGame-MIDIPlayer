"""2D 物理世界：Box2D ``b2World`` 的 GameObject 包装（固定步长 + 事件 + 查询）。

设计要点（细节见 docs/物理引擎.md）
----------------------------------

1. **固定步长**：引擎的 update 是固定 100Hz（``GameMainLoop.FIXED_TPS``），
   而 Box2D 推荐 60Hz 左右。所以这里自己攒一个累加器，按 ``fixed_step``
   推步（默认 1/60），单帧最多补 ``max_steps`` 步 —— 卡顿/断点之后不会
   出现"死亡螺旋"（补帧越补越慢）。
2. **事件在 Step 之后派发**：Box2D 在 Step 内部禁止改世界，接触回调只负责
   *记事件*，Step 返回后再统一派发给 ``world.on_contact`` / ``body.onContact``。
3. **销毁延迟到下一步**：``body.delete()`` 会立刻从注册表摘掉（逻辑上"没了"），
   但真正的 ``DestroyBody`` 排在下一步开头 —— 否则在接触回调里删刚体会让
   Box2D 里的 ``b2Contact`` 变成悬空指针（必崩）。
4. **位姿回写**：每步之后把 ``b2Body`` 的位置/角度写回 ``Body2D.x/y/r``，
   渲染与游戏逻辑读到的永远是这一步的真实状态。
"""

from ..gameobject.GameObject import GameObject
from . import Backend
from .Body2D import BodyType, _b2, _packPoint


class ContactEvent:
    """一次接触事件（``begin`` = 开始接触，``end`` = 分开）。

    Box2D 的两个回调点都发生在 Step 内部，这里只把需要的数据**抄下来**，
    Step 之后再派发。``contact`` 是原始 ``b2Contact``，在本次派发期间有效
    （下一次 Step 可能回收它，别留到下一帧用）。
    """

    __slots__ = ("kind", "body_a", "body_b", "fixture_a", "fixture_b", "sensor",
                 "normal", "points", "contact", "world")

    def __init__(self, world, kind: str, contact) -> None:
        self.world = world
        self.kind = kind
        self.contact = contact
        fixture_a, fixture_b = contact.fixtureA, contact.fixtureB
        self.fixture_a = getattr(fixture_a, "userData", None)
        self.fixture_b = getattr(fixture_b, "userData", None)
        self.body_a = getattr(fixture_a.body, "userData", None)
        self.body_b = getattr(fixture_b.body, "userData", None)
        self.sensor = bool(fixture_a.sensor or fixture_b.sensor)
        manifold = contact.worldManifold
        points = list(getattr(manifold, "points", []) or [])
        self.points = [(float(p[0]), float(p[1])) for p in points]
        normal = getattr(manifold, "normal", None)
        self.normal = (float(normal.x), float(normal.y)) if normal is not None else (0.0, 0.0)

    @property
    def bodies(self) -> tuple:
        """``(body_a, body_b)``。"""
        return (self.body_a, self.body_b)

    @property
    def isBegin(self) -> bool:
        return self.kind == "begin"

    def other(self, body):
        """``event.other(self)`` —— 拿到对面那个刚体（回调里最常用的写法）。"""
        return self.body_b if body is self.body_a else self.body_a

    def __repr__(self) -> str:
        return (f"<ContactEvent {self.kind} {self.body_a} × {self.body_b}"
                f"{' sensor' if self.sensor else ''}>")


class RayHit:
    """一次射线命中（:meth:`Physics2D.rayCast` 的返回值）。"""

    __slots__ = ("body", "fixture", "point", "normal", "fraction")

    def __init__(self, fixture, point, normal, fraction) -> None:
        self.fixture = getattr(fixture, "userData", None)
        self.body = getattr(fixture.body, "userData", None)
        self.point = (float(point[0]), float(point[1]))
        self.normal = (float(normal[0]), float(normal[1]))
        self.fraction = float(fraction)

    @property
    def distance(self) -> float:
        """命中点到射线起点距离的**比例**（0 = 起点，1 = 终点）。"""
        return self.fraction

    def __repr__(self) -> str:
        return (f"<RayHit {self.body} at ({self.point[0]:.2f}, {self.point[1]:.2f}) "
                f"f={self.fraction:.3f}>")


def _makeListener(world):
    """造一个 ``b2ContactListener`` 实例。

    **必须运行时造**：模块级不能 import Box2D（换解释器时 ``import pyglgame.physics``
    仍要成功），而 ``b2ContactListener`` 是 C++ 侧的类型，只能在拿到后端之后继承。
    """
    b2 = _b2()

    class _Listener(b2.b2ContactListener):
        def BeginContact(self, contact) -> None:
            world._queueEvent("begin", contact)

        def EndContact(self, contact) -> None:
            world._queueEvent("end", contact)

        def PreSolve(self, contact, oldManifold) -> None:
            # 预留：需要"接触期间每步改材质/过滤"时挂在这里
            pass

        def PostSolve(self, contact, impulse) -> None:
            # 预留：拿冲击量（做踩踏伤害、碎裂效果）时挂在这里
            pass

    return _Listener()


def _makeRayCallback(collector, sensors: bool, mask, user_callback=None):
    """造一个 ``b2RayCastCallback``。

    Box2D 的返回值语义（容易搞反，记一下）：

    - ``-1`` = 忽略这个 fixture，射线**原样**继续（收集全部命中就用它）
    - ``1``  = 在这一刀把它们切开，射线变短（只找最近命中用）
    - ``0``  = 立刻终止
    """
    b2 = _b2()

    class _RayCallback(b2.b2RayCastCallback):
        def ReportFixture(self, fixture, point, normal, fraction):
            if mask is not None and not (int(fixture.filterData.categoryBits) & int(mask)):
                return -1.0
            if fixture.sensor and not sensors:
                return -1.0
            hit = RayHit(fixture, point, normal, fraction)
            collector.append(hit)
            if user_callback is not None and user_callback(hit) is False:
                return 0.0
            return -1.0

    return _RayCallback()


def _makeQueryCallback(collector, sensors: bool, mask, user_callback=None):
    """造一个 ``b2QueryCallback``（返回 ``True`` = 继续查询）。"""
    b2 = _b2()

    class _QueryCallback(b2.b2QueryCallback):
        def ReportFixture(self, fixture):
            if mask is not None and not (int(fixture.filterData.categoryBits) & int(mask)):
                return True
            if fixture.sensor and not sensors:
                return True
            owner = getattr(fixture.body, "userData", None)
            if owner is not None and owner not in collector:
                collector.append(owner)
                if user_callback is not None and user_callback(owner) is False:
                    return False
            return True

    return _QueryCallback()


class _AnchorBody:
    """鼠标关节用的**隐形锚点**（无形状、不进 ``world.bodies``、不渲染）。

    Box2D 的 ``b2MouseJointDef`` 要求 ``bodyA`` 存在，但关节**完全用不到**它
    （鼠标关节只约束 bodyB 上的一个点）。这里给的是引擎自己建的静态空刚体，
    对外不暴露。
    """

    is_body_2d = True

    def __init__(self, world, b2body) -> None:
        self.world = world
        self._body = b2body
        self._destroyed = False
        self.active = True
        self.x = self.y = self.z = self.r = 0.0

    @property
    def awake(self) -> bool:
        return self._body is not None and bool(self._body.awake)

    @awake.setter
    def awake(self, value: bool) -> None:
        if self._body is not None:
            self._body.awake = bool(value)

    def wake(self) -> None:
        self.awake = True

    def syncFromBody(self) -> None:
        """锚点永远不会动，没有要同步的东西。"""

    def __repr__(self) -> str:
        return "<鼠标锚点刚体>"


class Physics2D(GameObject):
    """2D 物理世界（它自己就是"世界对象"，进渲染层的更新循环）。

    典型用法::

        world = Physics2D(gravity=(0.0, -9.8))        # 建好就已经在跑
        Body2D(world, body_type="static", y=-3.0).addBox(30.0, 0.5)
        Body2D(world, x=0.0, y=2.0).addCircle(0.4, density=1.0)

    想暂停：``world.paused = True``（或 ``world.active = False``，对象级的开关）。
    """

    is_physics_2d = True

    def __new__(cls, *args, **kwargs):
        # GameObject.__new__ 只认 is_camera：这里把构造参数吃掉（同 FogVolume/Body2D）
        return super().__new__(cls)

    def __init__(self, gravity=(0.0, -9.8), fixed_step: float = 1.0 / 60.0,
                 max_steps: int = 5, velocity_iterations: int = 8,
                 position_iterations: int = 3, do_sleep: bool = True,
                 name: str = None) -> None:
        super().__init__()
        b2 = _b2()
        self.name = name
        #: 原始 Box2D 世界 —— 要用 Box2D 原生能力（关节类型、调试绘制回调……）就从这儿拿
        self.b2world = b2.b2World(gravity=(float(gravity[0]), float(gravity[1])),
                                  doSleep=bool(do_sleep))
        self._listener = _makeListener(self)
        self.b2world.contactListener = self._listener
        #: 固定步长（秒）；``0`` = 不攒累加器，直接用引擎给的 dt 推一步
        self.fixed_step = float(fixed_step)
        #: 单帧最多补几步（防"死亡螺旋"：补满就丢弃积压）
        self.max_steps = max(1, int(max_steps))
        self.velocity_iterations = int(velocity_iterations)
        self.position_iterations = int(position_iterations)
        #: 注册的刚体（:class:`Body2D`）；原始数量看 ``b2world.bodyCount``
        self.bodies: list = []
        #: 注册的关节（:class:`Joint2D`）
        self.joints: list = []
        #: 暂停开关（Step 不再推进，事件也不派发）
        self.paused = False
        #: 模拟时间（秒，只有真的推步才涨 —— 卡顿/暂停都不会让它跑快）
        self.time = 0.0
        #: 上一次 update 实际推了几步（调参/单测用）
        self.sub_steps = 0
        #: 接触回调（世界级）：``world.on_contact = func(event)``
        self.on_contact = None
        #: 最近一次派发的接触事件（只读；调试/测试看一眼很方便）
        self.last_events: list = []
        self._accumulator = 0.0
        self._events: list = []
        self._pending_destroy: list = []

    # === 注册表（由 Body2D / Joint2D 调用，游戏代码不用管）===

    def _addBody(self, body) -> None:
        if body not in self.bodies:
            self.bodies.append(body)

    def _removeBody(self, body) -> None:
        """摘掉刚体并**排队**销毁 ``b2Body``。

        真正的 ``DestroyBody`` 要等下一步开头（:meth:`flushPending`）：接触回调
        与 Step 期间 Box2D 不允许销毁，硬来会让 ``b2Contact`` 变悬空指针。
        """
        if body in self.bodies:
            self.bodies.remove(body)
        # Box2D 销毁刚体时会**连带销毁**它身上的关节：引擎这边的记录必须先失效，
        # 否则世界列表里留的就是悬空指针（下次读 joint.length 会崩）。
        for joint in tuple(self.joints):
            if joint.body_a is body or joint.body_b is body:
                self.joints.remove(joint)
                joint.joint = None
        b2body = getattr(body, "_body", None)
        if b2body is not None:
            self._pending_destroy.append(("body", b2body))

    def _addJoint(self, joint) -> None:
        if joint not in self.joints:
            self.joints.append(joint)

    def _mouseAnchor(self):
        """鼠标关节的隐形静态锚点（懒创建；无形状）。"""
        anchor = getattr(self, "_anchor_body", None)
        if anchor is None or anchor._body is None:
            b2 = _b2()
            definition = b2.b2BodyDef(type=b2.b2_staticBody, position=(0.0, 0.0))
            anchor = _AnchorBody(self, self.b2world.CreateBody(definition))
            self._anchor_body = anchor
        return anchor

    def _removeJoint(self, joint) -> None:
        if joint in self.joints:
            self.joints.remove(joint)
        b2joint = getattr(joint, "joint", None)
        if b2joint is not None:
            self._pending_destroy.append(("joint", b2joint))

    def _queueEvent(self, kind: str, contact) -> None:
        """Step 内部的接触回调只做这一件事：把事件**抄下来**（此时禁止改世界）。"""
        try:
            self._events.append(ContactEvent(self, kind, contact))
        except Exception:
            # 抄事件失败（多半是刚体正好被销毁）不能把整步拖崩
            pass

    def flushPending(self) -> int:
        """真正执行排队中的销毁（每次推步开头调用）。返回销毁了几个对象。"""
        if not self._pending_destroy:
            return 0
        if self.b2world.locked:
            return 0                        # Step 内部：留到下一步
        pending, self._pending_destroy = self._pending_destroy, []
        for kind, handle in pending:
            try:
                if kind == "body":
                    self.b2world.DestroyBody(handle)
                else:
                    self.b2world.DestroyJoint(handle)
            except Exception:
                pass
        return len(pending)


    # === 推步 ===

    def update(self, dt: float, tps: float) -> None:
        """引擎每个固定步调用一次（默认 100Hz）：攒够 ``fixed_step`` 就推一步。"""
        if self.paused:
            self.sub_steps = 0
            self.flushPending()
            return
        step = self.fixed_step
        if step <= 0.0:
            # 不攒累加器：引擎的 update 本来就是固定步长，直接跟着走
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
                # 卡顿/断点之后积压了很多：直接丢掉，否则会连续补帧（死亡螺旋）
                self._accumulator = 0.0
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
        self.flushPending()
        if self.b2world.locked:
            return False                    # 已经在 Step 里面（不该发生，兜底）
        self.b2world.Step(dt, self.velocity_iterations, self.position_iterations)
        self.time += dt
        self._dispatchEvents()
        self.syncBodies()
        return True

    def syncBodies(self) -> None:
        """把物理状态写回所有刚体对象的 ``x/y/r``（每步之后调用）。"""
        for body in tuple(self.bodies):
            body.syncFromBody()

    def _dispatchEvents(self) -> None:
        """Step 之后统一派发接触事件。

        顺序：``world.on_contact`` → ``world.onContact`` → 事件里两个刚体的
        ``on_contact`` / :meth:`Body2D.onContact`。回调里可以安心增删刚体、施力
        —— 此时已不在 Step 内部，销毁会排到下一步开头。
        """
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

    def rayCast(self, start, end, closest: bool = True, sensors: bool = False,
                mask=None, callback=None):
        """从 ``start`` 射到 ``end``。

        - ``closest=True``（默认）→ 最近的 :class:`RayHit`，没命中返回 ``None``
        - ``closest=False`` → **全部**命中，按远近排序（同 :meth:`rayCastAll`）
        - ``sensors``：传感器算不算命中（默认不算 —— 它们本来就不挡路）
        - ``mask``：只命中 ``category & mask != 0`` 的 fixture（做"只打敌人"很方便）
        - ``callback(hit)`` 返回 ``False`` 可以提前终止
        """
        b2 = _b2()
        hits: list = []
        ray = _makeRayCallback(hits, sensors, mask, callback)
        self.b2world.RayCast(ray, _packPoint(start, "射线起点"), _packPoint(end, "射线终点"))
        hits.sort(key=lambda hit: hit.fraction)
        if closest:
            return hits[0] if hits else None
        return hits

    def rayCastAll(self, start, end, **kwargs) -> list:
        """射线上**所有**命中，按远近排序。"""
        kwargs["closest"] = False
        return self.rayCast(start, end, **kwargs)

    def queryAABB(self, minimum, maximum, sensors: bool = True, mask=None,
                  callback=None) -> list:
        """矩形区域查询：返回区域内的**刚体**（已去重，顺序 = Box2D 的遍历顺序）。"""
        b2 = _b2()
        found: list = []
        aabb = b2.b2AABB(lowerBound=_packPoint(minimum, "区域左下角"),
                         upperBound=_packPoint(maximum, "区域右上角"))
        self.b2world.QueryAABB(_makeQueryCallback(found, sensors, mask, callback), aabb)
        return found

    def queryPoint(self, x, y=None, sensors: bool = True, mask=None) -> list:
        """点查询：返回**压在这个点上**的刚体（先 AABB 再精确 ``TestPoint``）。

        鼠标点击拾取就用它：``for body in world.queryPoint(mouse_x, mouse_y)``。
        """
        point = _packPoint(x if y is None else (x, y), "点")
        bodies = self.queryAABB(point, point, sensors=sensors, mask=mask)
        return [body for body in bodies if body.containsPoint(point[0], point[1])]

    @staticmethod
    def bodyOf(fixture):
        """从 b2 fixture / :class:`Fixture2D` 反查 :class:`Body2D`（回调里常用）。"""
        if fixture is None:
            return None
        owner = getattr(fixture, "body", None)
        if owner is not None and getattr(owner, "is_body_2d", False):
            return owner                            # 传进来的是 Fixture2D
        record = getattr(fixture, "userData", None)
        if record is not None and getattr(record, "body", None) is not None:
            return record.body                      # 传进来的是 b2Fixture
        return getattr(getattr(fixture, "body", None), "userData", None)

    def wakeBodiesIn(self, area, margin: float = 0.1) -> int:
        """唤醒矩形区域 ``(min_x, min_y, max_x, max_y)`` 里睡着的动态刚体，返回个数。

        为什么要它：**Box2D 不会因为"另一个刚体挪过来"而自动叫醒睡着的刚体** ——
        运动学电梯会直接从睡着的箱子里穿过去（实测：箱子停在原地不动）。
        运动学刚体每帧移动之后调一次就好（:meth:`Body2D.syncToBody` 已经帮你调了）。
        """
        minimum = (float(area[0]) - float(margin), float(area[1]) - float(margin))
        maximum = (float(area[2]) + float(margin), float(area[3]) + float(margin))
        count = 0
        for body in self.queryAABB(minimum, maximum):
            if body.body_type == BodyType.DYNAMIC and not body.awake:
                body.wake()
                count += 1
        return count

    @property
    def allowSleeping(self) -> bool:
        """是否允许刚体休眠（Box2D 侧的开关；注意本版 pybox2d 即使关掉，
        ``body.awake`` 仍可能变 False，靠 :meth:`wakeBodiesIn` 兜底）。"""
        return bool(self.b2world.allowSleeping)

    @allowSleeping.setter
    def allowSleeping(self, value: bool) -> None:
        self.b2world.allowSleeping = bool(value)

    # === 世界级开关 / 清理 ===

    @property
    def gravity(self) -> tuple:
        """重力向量 ``(gx, gy)``（单位：米/秒²）。"""
        value = self.b2world.gravity
        return (float(value.x), float(value.y))

    @gravity.setter
    def gravity(self, value) -> None:
        point = _packPoint(value, "gravity")
        self.setGravity(point[0], point[1])

    def setGravity(self, gx, gy=None) -> "Physics2D":
        """设置重力（0 重力做俯视/太空场景，负 y 是"往下掉"）。"""
        x, y = _packPoint(gx if gy is None else (gx, gy), "gravity")
        self.b2world.gravity = (x, y)
        for body in self.bodies:                     # 唤醒：不唤醒的话睡着的刚体不动
            body.wake()
        return self

    @property
    def bodyCount(self) -> int:
        """注册的 :class:`Body2D` 数量（Box2D 侧的数量看 ``b2world.bodyCount``）。"""
        return len(self.bodies)

    @property
    def jointCount(self) -> int:
        """注册的关节数量。"""
        return len(self.joints)

    def pause(self) -> "Physics2D":
        self.paused = True
        return self

    def resume(self) -> "Physics2D":
        self.paused = False
        return self

    def togglePause(self) -> bool:
        self.paused = not self.paused
        return self.paused

    def clear(self) -> None:
        """清空世界：所有关节与刚体都注销并销毁（连带它们的渲染层注册）。"""
        for joint in tuple(self.joints):
            joint.delete()
        for body in tuple(self.bodies):
            body.delete()
        anchor = getattr(self, "_anchor_body", None)
        if anchor is not None and anchor._body is not None:
            self._pending_destroy.append(("body", anchor._body))
            anchor._body = None
        self.flushPending()

    def delete(self) -> None:
        """销毁整个世界（先把里面的东西清干净，再注销自己）。"""
        if getattr(self, "_deleted", False):
            return
        self.clear()
        super().delete()

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return (f"<Physics2D{label} gravity={self.gravity} bodies={len(self.bodies)} "
                f"joints={len(self.joints)} t={self.time:.2f}s"
                f"{' paused' if self.paused else ''}>")
