"""粒子系统：**世界里的对象**（和 ``Light`` / ``Sprite2D`` 一样放进场景就能用）。

.. code-block:: python

    from pyglgame.particle import ParticleEffect, ParticleSystem

    fire = ParticleSystem(ParticleEffect.fire(), x=0.0, y=-1.0)     # 建好就在发射
    fire.stop(clear=True)                                          # 立刻熄灭
    fire.play(restart=True)                                        # 重新点着

    ParticleSystem.preset("spark", x=2.0, y=0.0)                   # 预设一步到位

2D / 3D 都能用（顶点是**世界坐标**，对象自己算进顶点、不碰那几个全局模型矩阵）：

- 2D 相机（``MSimple2D``）：让 ``z`` 大一点点（例如 0.05）压在精灵上面；
- 3D 透视：``gl_PointSize = size / gl_Position.z``，所以粒子天然**远小近大**。

每帧做四件事（:meth:`update`）
-----------------------------

1. **跟随**：:meth:`attachTo` 之后原点跟着目标对象走（可选继承它的速度，
   这样"边跑边冒烟"不会把烟留在原地）；
2. **发射**：``rate`` 用**累加器**换算成整数个（帧率无关），``duration`` 到了就停发，
   ``burst`` / :meth:`burst` 是"一次性来一把"；
3. **模拟**：每个粒子按效果表做重力 / 阻尼 / 湍流 / 径向扩散
   （:meth:`~pyglgame.particle.Particle.Particle.integrate`），
   可选跟 2D 物理世界做点碰撞（:meth:`usePhysics`）；
4. **回收**：寿命到了就还回池子（:class:`~pyglgame.particle.Particle.ParticlePool`），
   全程不新建对象 —— 帧时间不会被 GC 抖到。

渲染（:meth:`render`）
---------------------

所有粒子**一次 draw call** 画完：顶点每帧重建（位置/颜色/尺寸一直在变），图元与顶点
格式由绘制模式决定（``"points"`` / ``"quads"``，见
:mod:`pyglgame.particle.ParticleRenderer`）。粒子默认 ``transparent = True``：走半透明
pass（关深度写入 + 按相机深度排序），混合模式取效果表的 ``blend_mode``
（火焰/火花用 ``"add"``，烟/雪用 ``"alpha"``）—— 批次级状态，不会泄漏给别的物体。

像素大小怎么算
--------------

点模式下 ``gl_PointSize = size / gl_Position.z``（``dis.vert`` 的 ``SIZ`` 语义）：

- 3D 透视：``z`` 就是到相机的距离 → 想占 30 像素、物体在 8 单位远，就填
  ``size ≈ 30 × 8 = 240``；
- 2D（``MSimple2D`` 的 NDC 直通，``z ≈ 0``）：分母是 ``max(z, 0.001)`` → 尺寸被放大
  一千倍，所以 2D 里 ``size`` 取 ``0.01 ~ 0.05`` 这一档（也可以给粒子一个小的正 z，
  分母跟着变大）。

拿不准就用四边形模式（``render_mode="quads"``）：那里的 ``size`` 是**世界单位的直径**，
与投影无关，还能贴一张圆形柔边纹理。
"""

import math
import random

from ..gameobject.GameObject import GameObject
from ..render.RenderBuffer import RenderBuffer
from ..RenderGlobal import RenderGlobal
from .Particle import Particle, ParticlePool
from .ParticleEffect import ParticleEffect
from .ParticleRenderer import ParticleRenderer, makeRenderer

#: ``prewarm()`` 内部空转用的固定步长（与物理固定步长的约定一致）
PREWARM_STEP = 1.0 / 60.0


class ParticleSystem(GameObject):
    """一个粒子系统（见模块文档）。"""

    def __new__(cls, *args, **kwargs):
        # ⚠ GameObject.__new__ 的第二个位置参数是 is_camera：构造参数必须在这里吃掉，
        # 否则 ``ParticleSystem(effect, x)`` 会被注册成相机 → RenderLayer 调
        # renderStart() 时炸掉（DebugDraw2D / ShadowCaster2D 都是同样的处理）。
        return super().__new__(cls)

    def __init__(self, effect: ParticleEffect = None, x: float = 0.0,
                 y: float = 0.0, z: float = 0.0, capacity: int = None,
                 render_mode="points", renderer: ParticleRenderer = None,
                 texture=None, size_mode=None, seed=None, auto_play: bool = True,
                 name: str = None) -> None:
        super().__init__()
        self.name = name
        self.x, self.y, self.z = float(x), float(y), float(z)
        self.effect = effect if effect is not None else ParticleEffect()
        if not hasattr(self.effect, "sampleLifetime"):
            raise TypeError("effect 必须是 ParticleEffect（也可以传 None 用默认值）")
        #: 粒子池容量（默认取效果表的 ``max_particles``）
        self.capacity = int(capacity) if capacity is not None \
            else int(self.effect.max_particles)
        self.pool = ParticlePool(self.capacity)
        self.seed = seed
        #: 本系统自己的随机源（``seed`` 固定 → 每次运行结果一致，回归测试友好）
        self.rng = random.Random(seed)
        #: 绘制层（点 / 广告牌四边形；也可以塞一个自定义的）
        if renderer is None:
            # size_mode 只对点模式有意义（四边形的 size 恒为世界单位）
            renderer_kwargs = {}
            if size_mode is not None:
                if str(render_mode).strip().lower() in ("quads", "quad",
                                                       "billboard", "sprites",
                                                       "sprite"):
                    raise ValueError(
                        "四边形模式的 size 恒为世界单位，不需要 size_mode"
                        "（要换绘制方式就自己传 renderer=）")
                renderer_kwargs["size_mode"] = size_mode
            renderer = makeRenderer(render_mode,
                                    texture=texture or self.effect.texture,
                                    **renderer_kwargs)
        self.renderer = renderer
        self.render_buffer = RenderBuffer()
        self.render_buffer.createBuffer(self.renderer.priType,
                                        self.renderer.formatFlags)
        self.render_buffer.setState(blend_mode=self.effect.blend_mode)
        # 粒子基本都是半透明内容：默认走半透明 pass（关深度写入 + 按深度排序）
        self.transparent = True

        # === 运行状态 ===
        #: 是否正在发射（暂停后已出生的粒子仍会走完自己的寿命）
        self.playing: bool = False
        #: 系统时间（湍流的相位）
        self.time: float = 0.0
        #: 累计发射过多少粒子（统计 / 测试用）
        self.emitted_total: int = 0
        #: 本系统"发过多久"（``duration`` 用）
        self.emit_time: float = 0.0
        #: 上一帧画了多少个粒子 / 顶点（调试 / 测试用）
        self.draw_particles: int = 0
        self.draw_vertices: int = 0
        #: 可选钩子 ``on_spawn(particle)``：给粒子挂自定义字段
        self.on_spawn = None
        self._played_before = False
        self._rate_accumulator = 0.0

        # === 跟随 / 碰撞 ===
        self._target = None
        self._target_offset = (0.0, 0.0, 0.0)
        self._target_previous = None
        self._target_velocity = (0.0, 0.0, 0.0)
        self._inherit_velocity = False
        #: 可选的 2D 物理世界（``usePhysics`` 之后有效）
        self.world = None
        self.physics_restitution = 0.4
        self.physics_friction = 0.2
        self.physics_mask = None
        self.physics_radius = 0.02

        if auto_play:
            self.play()

    # === 便捷构造 / 统计 ===

    @classmethod
    def preset(cls, name: str, **kwargs) -> "ParticleSystem":
        """按预设名建一个系统：``ParticleSystem.preset("fire", x=0.0, y=-1.0)``。

        ``name`` 见 :attr:`ParticleEffect.PRESETS`（fire / smoke / spark /
        explosion / snow / fountain / pixel_fire）。``kwargs`` 里 ``effect_`` 前缀的给效果表
        （例如 ``effect_rate=120.0``），其余的给系统本身
        （``x`` / ``y`` / ``z`` / ``capacity`` / ``seed`` / ``render_mode`` / ``texture``）。
        """
        name = str(name).strip().lower()
        preset = ParticleEffect.presetFactory(name)     # 认 pixel_fire 这类带下划线的名字
        effect_kwargs = {}
        system_kwargs = {}
        for key, value in kwargs.items():
            if key.startswith("effect_"):
                effect_kwargs[key[len("effect_"):]] = value
            else:
                system_kwargs[key] = value
        return cls(preset(**effect_kwargs), **system_kwargs)

    @property
    def particleCount(self) -> int:
        """当前活着的粒子数（会顺手清掉已死的，O(n)）。"""
        return self.pool.activeCount

    @property
    def usage(self) -> float:
        """池占用率 0~1（调 ``rate`` / ``capacity`` 时看它）。"""
        return self.pool.usage

    @property
    def isPlaying(self) -> bool:
        """是否还有发射能力（``rate`` / ``burst`` 都为 0 的对象只是"粒子播放器"）。"""
        return self.playing and bool(self.effect.rate or self.effect.burst)

    # === 发射控制 ===

    def play(self, restart: bool = False, burst: bool = None) -> "ParticleSystem":
        """开始 / 继续发射。

        - ``restart=True``：先 :meth:`clear`（现有粒子立刻消失），并把发射计时归零；
        - ``burst``：是否补一发 ``effect.burst``（默认"第一次 play 或 restart 时"补）；
        - 第一次 ``play()`` 还会跑一次 ``effect.prewarm`` 秒的空转（若设置了）。
        """
        if restart:
            self.clear()
            self.emit_time = 0.0
        first = not self._played_before
        self._played_before = True
        self.playing = True
        if burst is None:
            burst = first or restart
        if burst and self.effect.burst:
            self.emit(self.effect.burst)
        if first and self.effect.prewarm > 0.0:
            self.prewarm()
        return self

    def pause(self) -> "ParticleSystem":
        """暂停发射（已经出生的粒子继续走完自己的寿命）。"""
        self.playing = False
        return self

    def stop(self, clear: bool = True) -> "ParticleSystem":
        """停止发射；``clear=True``（默认）连现有粒子一起回收 = "立刻熄灭"。"""
        self.playing = False
        if clear:
            self.clear()
        return self

    def toggle(self) -> bool:
        """在"发 / 不发"之间切换，返回切换后的状态。"""
        if self.playing:
            self.pause()
        else:
            self.play(burst=False)
        return self.playing

    def burst(self, count: int = None) -> int:
        """一次性发射 ``count`` 个（默认用效果表的 ``burst``），返回实际数量。"""
        if count is None:
            count = self.effect.burst
        return self.emit(int(count))

    def emit(self, count: int) -> int:
        """强制发射 ``count`` 个粒子，返回**实际**发出来的数量（池满就少）。"""
        spawned = 0
        for _ in range(max(int(count), 0)):
            if self.spawnParticle() is not None:
                spawned += 1
        return spawned

    def clear(self) -> int:
        """回收所有粒子（返回回收数量）；本系统的计时/统计不受影响。"""
        return self.pool.releaseAll()

    def prewarm(self, seconds: float = None) -> "ParticleSystem":
        """空转一段时间：一上来就是"成熟"的粒子团，而不是从零长出来。

        ``seconds`` 默认取效果表的 ``prewarm``；内部按固定步长
        （:data:`PREWARM_STEP`）推进，和真实帧率无关 —— 所以同一个 seed 下
        预热的画面每次都一样。预热期间的发射会照常计入 ``emit_time``
        （``duration`` 短于预热时长时，预热结束就已经停发了）。
        """
        if seconds is None:
            seconds = self.effect.prewarm
        remaining = float(seconds)
        while remaining > 1e-6:
            step = min(PREWARM_STEP, remaining)
            self.advance(step)
            remaining -= step
        return self

    # === 跟随别的对象 ===

    def attachTo(self, target, offset=(0.0, 0.0, 0.0),
                 inherit_velocity: bool = True) -> "ParticleSystem":
        """让发射原点跟着 ``target``（任何有 ``x`` / ``y`` / ``z`` 的对象）走。

        - ``offset`` 是**世界坐标**下的固定偏移（不受目标旋转影响）；
        - ``inherit_velocity=True`` 时新粒子会带上目标的速度 —— "边跑边冒烟"时烟不会
          被落在原地（速度用相邻两帧的位移差分算，目标瞬移时会给出一大坨速度）。

        想自己算位置就别用这个，直接每帧写 ``system.x / y / z`` 更灵活。
        """
        self._target = target
        self._target_offset = (float(offset[0]), float(offset[1]), float(offset[2]))
        self._inherit_velocity = bool(inherit_velocity)
        self._target_previous = None
        self._target_velocity = (0.0, 0.0, 0.0)
        self._syncTarget(0.0)
        return self

    def detach(self) -> "ParticleSystem":
        """停止跟随（保留当前位置）。"""
        self._target = None
        self._target_previous = None
        self._target_velocity = (0.0, 0.0, 0.0)
        return self

    def _syncTarget(self, dt: float) -> None:
        """跟随目标：把原点摆过去，并按需算出目标速度（相邻两帧差分）。"""
        target = self._target
        if target is None:
            return
        offset_x, offset_y, offset_z = self._target_offset
        position = (float(getattr(target, "x", 0.0)) + offset_x,
                    float(getattr(target, "y", 0.0)) + offset_y,
                    float(getattr(target, "z", 0.0)) + offset_z)
        if self._inherit_velocity and dt > 0.0 and self._target_previous is not None:
            previous = self._target_previous
            self._target_velocity = ((position[0] - previous[0]) / dt,
                                     (position[1] - previous[1]) / dt,
                                     (position[2] - previous[2]) / dt)
        elif not self._inherit_velocity:
            self._target_velocity = (0.0, 0.0, 0.0)
        self._target_previous = position
        self.x, self.y, self.z = position

    # === 与 2D 物理世界的碰撞（可选） ===

    def usePhysics(self, world, restitution: float = 0.4, friction: float = 0.2,
                   mask=None, radius: float = 0.02) -> "ParticleSystem":
        """让粒子跟一个 2D 物理世界碰撞（点碰撞近似：粒子当半径 ``radius`` 的圆点）。

        - ``restitution``：法向反弹系数（0 = 贴住不弹，1 = 完全弹回）；
        - ``friction``：切向速度的损失比例（0.2 = 每次接触削掉 20%）；
        - ``mask``：只和 ``category & mask != 0`` 的 fixture 碰撞（同
          :meth:`Physics2D.queryPoint`）。

        实现是"每个粒子每帧一次 ``queryPoint``"：粒子很多时这是主要开销，
        ``rate`` 上千就考虑调小容量、或者只给"火花溅到地面"这类效果开。
        """
        self.world = world
        self.physics_restitution = float(restitution)
        self.physics_friction = float(friction)
        self.physics_mask = mask
        self.physics_radius = max(float(radius), 0.0)
        return self

    def detachPhysics(self) -> "ParticleSystem":
        """关掉与物理世界的碰撞。"""
        self.world = None
        return self

    # === 模拟 ===

    def update(self, dt: float, tps: float):
        """每帧：跟随目标 → 发射 → 模拟 → 回收（见模块文档）。"""
        if not self.active:
            return
        dt = max(float(dt), 0.0)
        self._syncTarget(dt)
        self.advance(dt)
        return super().update(dt, tps)

    def advance(self, dt: float, emitting: bool = None) -> int:
        """推进一帧（``prewarm`` 也走这里）；返回这一帧回收了多少粒子。

        ``emitting=False`` 只走模拟不发射（"粒子已经发完了，让它们飘完"）。
        """
        dt = float(dt)
        if dt <= 0.0:
            return 0
        self.time += dt
        should_emit = self.playing if emitting is None else bool(emitting)
        if should_emit:
            self.emit_time += dt
            self._emitStep(dt)
        released = self._advanceParticles(dt)
        self.pool.compact()
        return released

    def _emitStep(self, dt: float) -> int:
        """按 ``rate`` 发射：用**累加器**换算成整数个（60fps 和 20fps 出来一样密）。"""
        effect = self.effect
        if effect.duration and self.emit_time > effect.duration:
            return 0
        if effect.rate <= 0.0:
            return 0
        self._rate_accumulator += effect.rate * dt
        whole = int(self._rate_accumulator)
        if whole <= 0:
            return 0
        self._rate_accumulator -= whole
        return self.emit(whole)

    def spawnParticle(self) -> "Particle":
        """生成一个粒子（池满返回 ``None``）—— 发射的全部随机都发生在这里。

        步骤：形状采样出**偏移 + 方向** → 抽速度 → 叠上径向扩散与跟随目标的速度 →
        :meth:`~pyglgame.particle.Particle.Particle.reset` 复活一个池里的粒子。
        """
        particle = self.pool.acquire()
        if particle is None:
            return None
        effect = self.effect
        rng = self.rng
        offset_x, offset_y, offset_z, direction = effect.shape.sample(rng)
        if direction is None:
            direction = effect.direction
        speed = effect.sampleSpeed(rng)
        velocity_x = direction[0] * speed
        velocity_y = direction[1] * speed
        velocity_z = direction[2] * speed
        if effect.expansion:
            # 径向扩散：从发射原点往外推（"炸开 / 膨胀"感）
            distance = math.sqrt(offset_x * offset_x + offset_y * offset_y
                                 + offset_z * offset_z)
            if distance > 1e-6:
                factor = effect.expansion / distance
                velocity_x += offset_x * factor
                velocity_y += offset_y * factor
                velocity_z += offset_z * factor
        if self._inherit_velocity:
            velocity_x += self._target_velocity[0]
            velocity_y += self._target_velocity[1]
            velocity_z += self._target_velocity[2]
        particle.reset(x=self.x + offset_x, y=self.y + offset_y,
                       z=self.z + offset_z, vx=velocity_x, vy=velocity_y,
                       vz=velocity_z, lifetime=effect.sampleLifetime(rng),
                       size=effect.sampleSize(rng), color=effect.colorAt(0.0),
                       seed=rng.random())
        if self.on_spawn is not None:
            self.on_spawn(particle)
        self.emitted_total += 1
        return particle

    def _advanceParticles(self, dt: float) -> int:
        """积分所有粒子 + 可选碰撞 + 回收（返回回收数量）。"""
        effect = self.effect
        gravity = effect.gravity
        drag = effect.drag
        turbulence = effect.turbulence
        world = self.world
        time = self.time
        released = 0
        # pool.alive 在 release 时只会被打标记（不立刻摘除）→ 这里遍历是安全的
        for particle in self.pool.alive:
            if not particle.alive:
                continue
            particle.integrate(dt, gravity=gravity, drag=drag,
                               turbulence=turbulence, time=time)
            if world is not None:
                self._collideWithWorld(particle)
            if particle.age >= particle.lifetime:
                self.pool.release(particle)
                released += 1
        return released

    def _collideWithWorld(self, particle: "Particle") -> bool:
        """2D 物理世界的**点碰撞近似**（见 :meth:`usePhysics`）。"""
        hits = self.world.queryPoint(particle.x, particle.y, sensors=False,
                                     mask=self.physics_mask)
        if not hits:
            return False
        hit = hits[0]
        normal = getattr(hit, "normal", None)
        if normal is None:
            return False
        normal_x, normal_y = float(normal[0]), float(normal[1])
        length = math.sqrt(normal_x * normal_x + normal_y * normal_y)
        if length < 1e-6:
            return False
        normal_x, normal_y = normal_x / length, normal_y / length
        # 1) 先沿法线推出去一点：不然下一帧还在形状里，会连着触发好多次
        push = self.physics_radius + 1e-3
        particle.x += normal_x * push
        particle.y += normal_y * push
        # 2) 法向反弹（只在"撞进去"的时候）
        along = particle.vx * normal_x + particle.vy * normal_y
        if along < 0.0:
            bounce = (1.0 + self.physics_restitution) * along
            particle.vx -= bounce * normal_x
            particle.vy -= bounce * normal_y
        # 3) 切向摩擦
        if self.physics_friction:
            tangent_x, tangent_y = -normal_y, normal_x
            tangent = particle.vx * tangent_x + particle.vy * tangent_y
            keep = max(0.0, 1.0 - self.physics_friction) - 1.0
            particle.vx += tangent_x * tangent * keep
            particle.vy += tangent_y * tangent * keep
        return True

    # === 外观查询（绘制层问系统，而不是自己去翻效果表 → 便于自定义）===

    def colorOf(self, particle: "Particle") -> tuple:
        """粒子当前的颜色（默认按归一化年龄查效果表的颜色曲线，可覆盖）。"""
        return self.effect.colorAt(particle.age01)

    def sizeOf(self, particle: "Particle") -> float:
        """粒子当前的尺寸（同上，默认查尺寸曲线）。"""
        return self.effect.sizeAt(particle.age01)

    def setEffect(self, effect: ParticleEffect,
                  reset_blend: bool = True) -> "ParticleSystem":
        """换一份效果表（火焰 → 烟雾这种切换）。

        池容量不跟着变（换 ``max_particles`` 要自己改 ``capacity`` 并重建系统）；
        ``reset_blend=True`` 时把批次混合模式也换成新效果表的。
        """
        self.effect = effect
        if reset_blend:
            self.render_buffer.setState(blend_mode=effect.blend_mode)
        if self.renderer.texture is None:
            self.renderer.texture = effect.texture
        return self

    # === 渲染（所有粒子一次 draw call）===

    def render(self, dt: float, fps: float) -> None:
        """把当前所有粒子写进一个顶点缓冲并画出来（见模块文档）。

        顶点每帧重建：粒子的位置 / 颜色 / 尺寸都在变。没有粒子时**一个 GL 调用都不发**
        （连缓冲都不上传），所以场景里可以放心摆很多"没在用"的粒子系统。
        """
        if not self.active or not self.pool.alive:
            self.draw_particles = 0
            self.draw_vertices = 0
            return
        builder = self.render_buffer.buffer_builder
        builder.clear()
        count = self.renderer.buildVertices(self, builder)
        self.draw_particles = count
        self.draw_vertices = builder.size
        if count == 0:
            return
        self.renderer.prepare()
        self.renderer.bindTexture()
        self.render_buffer.build()
        # 顶点已经是世界坐标：只上传光照 / 环境等的 uniform，**不碰**那几个全局模型矩阵
        # （它们是整体世界坐标系的调节；对象覆盖它们的话，谁也设不了"整个世界转 30°"）
        RenderGlobal.instance.updateGlobalUniforms()
        self._drawParticles()
        super().render(dt, fps)

    def _drawParticles(self) -> None:
        """发一次 draw call。

        ⚠ **WBOIT pass 里不能动批次混合状态**：WBOIT 在 ``beginTransparent`` 里用
        ``glBlendFunci`` 给两个颜色附件分别设了混合（accum ``ONE/ONE``、
        reveal ``ZERO/ONE_MINUS_SRC_COLOR``），而 :class:`RenderBuffer` 的批次混合
        走的是**全局** ``glBlendFunc`` —— 一旦覆盖，粒子就会整批被丢掉
        （实测：WBOIT 开着时粒子完全不显示，关掉就正常）。OIT 本身就是逐像素
        顺序无关的，也不需要按批次换混合模式，所以这里临时跳过批次状态。
        """
        buffer = self.render_buffer
        blend_mode, depth_write = buffer.blend_mode, buffer.depth_write
        if getattr(RenderGlobal.instance, "oit_pass", False):
            buffer.blend_mode = None
            buffer.depth_write = None
        try:
            buffer.draw(False)
        finally:
            buffer.blend_mode = blend_mode
            buffer.depth_write = depth_write

    def onRemoved(self) -> None:
        """注销时释放 VBO/VAO（要在 GL 线程里；贴图由资源缓存管）。"""
        if self.render_buffer is not None:
            self.render_buffer.release()
            self.render_buffer = None
        if self.renderer is not None:
            self.renderer.release()

    def __repr__(self) -> str:
        label = " %r" % (self.name,) if self.name else ""
        return ("<ParticleSystem%s %d/%d 个 %s 画%d顶点 原点(%.2f, %.2f, %.2f)>"
                % (label, len(self.pool.alive), self.capacity,
                   "发射中" if self.playing else "已停", self.draw_vertices,
                   self.x, self.y, self.z))




