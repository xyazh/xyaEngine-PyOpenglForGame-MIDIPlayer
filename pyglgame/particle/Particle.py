"""粒子的数据与对象池。

.. code-block:: python

    pool = ParticlePool(256)
    particle = pool.acquire()              # 没有空闲就返回 None（绝不无限增长）
    if particle is not None:
        particle.reset(x=0.0, y=0.0, z=0.0, vx=0.0, vy=1.0, vz=0.0,
                       lifetime=1.0, size=0.1, color=(1.0, 0.6, 0.2, 1.0))
    particle.integrate(1.0 / 60.0, gravity=(0.0, -9.8, 0.0))
    pool.release(particle)                 # 死掉的还回池子（不新建对象）

设计要点
--------

- :class:`Particle` 只做"自己这一帧怎么动"（重力 / 阻尼 / 湍流），**不认识 GL，也不认识
  曲线**：尺寸与颜色由 :class:`~pyglgame.particle.ParticleEffect.ParticleEffect` 按
  **归一化年龄**算 —— 于是"池 / 模拟 / 外观 / 绘制"四块各自能单测。
- 池是**定长**的：``acquire()`` 拿不到空闲就返回 ``None``（超上限时丢新粒子），
  所以爆发式发射不会每帧 new 一批对象、也不会无界增长（帧时间稳定）。
- 回收走 :meth:`ParticlePool.release`：只标记 + 记一笔，真正的"从列表里摘掉"由
  :meth:`ParticlePool.compact` 每帧做一次 —— 于是 ``update`` 里边遍历边回收是安全的
  （引擎里所有遍历都讲究快照迭代，见 ``RenderLayer``）。
"""

import math

#: 湍流的三个频率（互相不成整数倍 → 三条正弦叠加看起来不像周期运动）
TURBULENCE_FREQUENCIES = (0.70, 1.13, 0.47)


class Particle:
    """一个粒子（**池化复用**：构造一次，之后靠 :meth:`reset` 复活）。

    字段都是普通属性（没有 ``__slots__``）：需要挂自己的状态时就写
    ``particle.user = {...}``，或者在 ``ParticleSystem.onSpawn`` 钩子里加自定义字段。
    """

    def __init__(self) -> None:
        self.alive: bool = False
        #: 世界坐标
        self.x = self.y = self.z = 0.0
        #: 世界速度（单位 / 秒）
        self.vx = self.vy = self.vz = 0.0
        #: 已存活时长 / 总寿命（秒）—— ``age01`` 是归一化年龄
        self.age: float = 0.0
        self.lifetime: float = 1.0
        #: 尺寸（写进顶点属性的原始值；点模式 = ``SIZ``，四边形模式 = 世界单位直径）
        self.size: float = 1.0
        #: 颜色（RGBA；HDR 时可以 > 1，配合加性混合很好看）
        self.r = self.g = self.b = self.a = 1.0
        #: 每个粒子自己的随机相位（湍流用；由系统的 RNG 采样 → 给定 seed 可复现）
        self.seed: float = 0.0
        #: 出生时的速度 / 尺寸（做"随时间放大"这类自定义效果时有用）
        self.spawn_speed: float = 0.0
        self.spawn_size: float = 1.0
        #: 使用者自定义数据的槽位（引擎不碰它）
        self.user = None

    # === 生命周期 ===

    def reset(self, x: float, y: float, z: float, vx: float = 0.0, vy: float = 0.0,
              vz: float = 0.0, lifetime: float = 1.0, size: float = 1.0,
              color=(1.0, 1.0, 1.0, 1.0), seed: float = 0.0) -> "Particle":
        """复活一个粒子（池里拿到的粒子都是"用过"的，必须走这里重置）。"""
        self.alive = True
        self.x, self.y, self.z = float(x), float(y), float(z)
        self.vx, self.vy, self.vz = float(vx), float(vy), float(vz)
        self.age = 0.0
        self.lifetime = max(float(lifetime), 1e-6)
        self.size = float(size)
        self.r, self.g, self.b, self.a = (float(c) for c in color)
        self.seed = float(seed)
        self.spawn_speed = math.sqrt(self.vx * self.vx + self.vy * self.vy
                                     + self.vz * self.vz)
        self.spawn_size = self.size
        return self

    @property
    def age01(self) -> float:
        """归一化年龄 ``0 → 1``（出生 = 0，寿命走完 = 1）。"""
        if self.lifetime <= 0.0:
            return 1.0
        return min(max(self.age / self.lifetime, 0.0), 1.0)

    @property
    def remaining(self) -> float:
        """还能活多久（秒）。"""
        return max(self.lifetime - self.age, 0.0)

    @property
    def speed(self) -> float:
        """当前速率（``|v|``）。"""
        return math.sqrt(self.vx * self.vx + self.vy * self.vy + self.vz * self.vz)

    # === 模拟 ===

    def integrate(self, dt: float, gravity=(0.0, 0.0, 0.0), drag: float = 0.0,
                  turbulence: float = 0.0, time: float = 0.0) -> "Particle":
        """一帧的运动积分（重力 → 湍流 → 阻尼 → 位移）。纯数学，可单测。

        - ``gravity``：恒定加速度（世界单位/秒²）；
        - ``turbulence``：随机扰动加速度的幅度，用三条不同频率的正弦叠出来
          （``self.seed`` 当相位 → 同 seed 同结果）；
        - ``drag``：线性阻尼，``v *= clamp(1 - drag·dt, 0, 1)``
          （``drag = 1`` 大约"一秒把速度磨没"；大到 10 也只是更快，不会炸）；
        - 积分顺序是"先加速度后位移"（半隐式欧拉），足够稳且不依赖上一帧。
        """
        dt = float(dt)
        if dt <= 0.0:
            return self
        self.age += dt
        gx, gy, gz = _vec3(gravity)
        ax, ay, az = gx, gy, gz
        if turbulence:
            phase = self.seed * math.tau
            rate = float(turbulence)
            ax += rate * math.sin(time * TURBULENCE_FREQUENCIES[0] * math.tau + phase)
            ay += rate * math.cos(time * TURBULENCE_FREQUENCIES[1] * math.tau + phase)
            az += rate * math.sin(time * TURBULENCE_FREQUENCIES[2] * math.tau + phase)
        self.vx += ax * dt
        self.vy += ay * dt
        self.vz += az * dt
        if drag:
            decay = max(1.0 - float(drag) * dt, 0.0)
            self.vx *= decay
            self.vy *= decay
            self.vz *= decay
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.z += self.vz * dt
        return self

    def computeAge(self, dt: float) -> bool:
        """只推进年龄（不动物理），返回是否还活着。

        给"位置由别处驱动"的用法（例如粒子挂在骨骼/曲线上，只想让寿命自己走）。
        """
        self.age += float(dt)
        if self.age >= self.lifetime:
            self.alive = False
        return self.alive

    def __repr__(self) -> str:
        return ("<Particle%s pos=(%.3f, %.3f, %.3f) v=(%.3f, %.3f, %.3f) "
                "age=%.2f/%.2f size=%.3f>" % (" 存活" if self.alive else " 死亡",
                                              self.x, self.y, self.z,
                                              self.vx, self.vy, self.vz,
                                              self.age, self.lifetime, self.size))


def _vec3(value) -> tuple:
    """把 ``(x, y, z)`` / ``glm.vec3`` 统一成三元组。"""
    if value is None:
        return (0.0, 0.0, 0.0)
    if isinstance(value, (int, float)):
        return (float(value), 0.0, 0.0)
    return (float(value[0]), float(value[1]), float(value[2]))


class ParticlePool:
    """定长粒子池（见模块文档）。

    ``Particle`` 对象在构造时就全部建好（``capacity`` 个），之后**只借不 new**：
    发射密度再高也只是复用，帧时间不会被 GC 抖到。
    """

    def __init__(self, capacity: int = 1000) -> None:
        capacity = int(capacity)
        if capacity < 1:
            raise ValueError("粒子池容量必须 >= 1，收到 %r" % (capacity,))
        self.capacity = capacity
        #: 活着的粒子（顺序 = 出生顺序；``release`` 过的会在这里留到下次 ``compact``）
        self.alive: list = []
        self._free: list = [Particle() for _ in range(capacity)]
        self._scratch: list = []
        self._dirty = False

    # === 查询 ===

    @property
    def freeCount(self) -> int:
        """池里还有多少空闲粒子。"""
        return len(self._free)

    @property
    def activeCount(self) -> int:
        """当前活着的粒子数（会顺手把死掉的摘掉，代价 O(n)）。"""
        self.compact()
        return len(self.alive)

    @property
    def usage(self) -> float:
        """占用率 ``0~1``（调试 / 自适应发射量用）。"""
        return len(self.alive) / float(self.capacity)

    def __len__(self) -> int:
        return self.activeCount

    def __iter__(self):
        """迭代活着的粒子（先整理一遍，保证不会迭代到已死的）。"""
        self.compact()
        return iter(self.alive)

    # === 借 / 还 ===

    def acquire(self) -> "Particle":
        """借一个空闲粒子；池满了返回 ``None``（= 这个粒子没生成出来）。

        借出来的粒子已经 **reset 成中性状态**（1 秒寿命、尺寸 0、alpha 0 → 完全看不见），
        所以"借了忘了初始化"也不会把上一次的位置/颜色留在画面里；标准用法是紧接着
        ``particle.reset(...)`` 填真正的初始值（:meth:`ParticleSystem.spawnParticle`
        就是这么做的）。
        """
        if self._dirty:
            self.compact()
        if not self._free:
            return None
        particle = self._free.pop()
        # 中性状态：看不见、也不会赖着不走（acquire 之后 alive 立即为真，
        # 这样 len(alive) == activeCount 的 O(1) 不变量一直成立）
        particle.reset(x=0.0, y=0.0, z=0.0, lifetime=1.0, size=0.0,
                       color=(1.0, 1.0, 1.0, 0.0))
        self.alive.append(particle)
        return particle

    def release(self, particle: "Particle") -> bool:
        """回收一个粒子（重复回收是安全的，返回是否真的回收了）。

        ``release`` 只打标记 + 记一笔：从 ``alive`` 里摘掉是 :meth:`compact` 的事，
        所以在遍历 ``alive`` 的循环里回收是安全的。
        """
        if particle is None or not particle.alive:
            return False
        particle.alive = False
        self._free.append(particle)
        self._dirty = True
        return True

    def releaseAll(self) -> int:
        """回收所有活着的粒子（``stop(clear=True)`` / 换场景时用），返回回收数量。"""
        released = 0
        for particle in tuple(self.alive):
            if self.release(particle):
                released += 1
        self.compact()
        return released

    def compact(self) -> int:
        """整理活着列表（每帧一次，O(n)）；返回摘掉了几个。"""
        if not self._dirty:
            return 0
        self._dirty = False
        scratch = self._scratch
        del scratch[:]
        scratch.extend(p for p in self.alive if p.alive)
        removed = len(self.alive) - len(scratch)
        # 两块列表互换复用（避免每帧新建 list）
        self.alive, self._scratch = scratch, self.alive
        del self._scratch[:]
        return removed

    def __repr__(self) -> str:
        return ("<ParticlePool %d/%d 活着%s>"
                % (len(self.alive), self.capacity,
                   "" if not self._dirty else "（待整理）"))

