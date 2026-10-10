"""动画基类（接口）：2D 动画系统的公共时间轴 + 与 ``GameObject`` 的协作式接驳。

为什么是"接口 + 实方法"
---------------------

本包的动画类**都不以 ``GameObject`` 为父类**，而是**可组合的接口**：

- 当"世界里的对象"用 → 多继承：``class Player(IAnimationFrame, Sprite2D)``；
- 当"对象的零件"用 → 持有它：``self.anim = IAnimationFrame(...)``，
  在自己的 ``update`` / ``render`` 里调 ``anim.update(dt, tps)`` / ``anim.render(dt, fps)``；
- 两者都成立，是因为基类里的 ``__init__`` / ``update`` / ``render`` 都走
  **协作式 ``super()``**：不抢宿主的生命周期，只往链上加一段。

MRO 约定（很重要）
-----------------

接口类写在**左边**：

.. code-block:: python

    class Player(IAnimationFrame, Sprite2D):    # ✅ 接口在左
        def __init__(self, texture):
            super().__init__(rows=2, columns=4, frame_count=8, ...)  # 接口 → 宿主
            self.texture = texture
            self.rebuild()

    class Player(Sprite2D, IAnimationFrame):    # ❌ 动画不会动
        ...

原因：``GameObject.update`` / ``Sprite2D.render`` 这类宿主方法**不调用 ``super()``**
（它们是链的终点）。接口排在前面才能"套在"宿主外面，把宿主实现当成链的下游。
写反了不报错，只是动画静默不动 —— 所以这里写清楚（有单测守着这条约定）。

一次 ``update`` 的顺序
--------------------

::

    RenderLayer.update(dt, tps)
        → IAnimationBase.update()          # 接口层
            → super().update(dt, tps)      # 宿主（GameObject / 自定义逻辑）先跑
            → tickAnimation(dt)            # 再推进时间轴
                → updateAnimation(dt)      # 子类钩子：按当前时间摆姿态

``render`` 同理：先让宿主画自己的东西，动画画在它上面。

共享字段：谁先设谁生效
--------------------

``x`` / ``y`` / ``z`` / ``r`` 是"宿主和动画都认"的字段 —— 接口**只在宿主没设过时**
兜底（``hasattr`` 判断），永不覆盖宿主的取值；而 ``transparent`` / ``texture`` /
``width`` 这类**宿主专属**字段接口一概不碰（子类需要时用带前缀的名字，
例如 :class:`~pyglgame.gameobject.animation.IAnimationFrame.IAnimationFrame` 的
``frame_texture`` / ``frame_width``）。
"""

import math

#: 时间轴默认时长（秒）。子类在 ``__init__`` 里按自己的素材把它算出来。
DEFAULT_DURATION = 0.0

#: 接口与宿主共用的变换字段（独立使用时由接口自己兜底，见 :func:`_collaborativeInit`）
TRANSFORM_FIELDS = ("x", "y", "z", "r")


def wrapTime(time: float, duration: float) -> float:
    """把 ``time`` 折回 ``[0, duration)``（支持负数，倒放用）。"""
    duration = float(duration)
    if duration <= 0.0:
        return 0.0
    wrapped = math.fmod(float(time), duration)
    if wrapped < 0.0:
        wrapped += duration
    return wrapped


def _collaborativeInit(cls, instance, *args, **kwargs) -> None:
    """协作式 ``__init__``：把参数交给 MRO 里 ``cls`` 后面的类。

    - 后面还有协作者（``GameObject`` / ``Sprite2D`` / 自定义基类）→ 原样转发参数，
      于是接口**不认识的关键字参数**会落到宿主手里（``x`` / ``width`` / ``material`` …）；
    - 后面只剩 ``object``（独立使用：没继承 ``GameObject``）→ 收下 ``x`` / ``y`` /
      ``z`` / ``r`` 这几个变换字段，其余丢掉 —— 这样 ``IAnimationFrame(..., x=1.0)``
      单独用也符合直觉，而不是默默忽略。

    ⚠ 判断"后面是不是 ``object``"看的是 **MRO 里下一个类**，不能用
    ``parent.__init__ is object.__init__``：``super()`` 代理拿到的 ``__init__`` 是
    绑定过的 method-wrapper，``is`` 比不出来（踩过：独立使用时多传一个 kwarg 就炸）。
    """
    parent = super(cls, instance)
    mro = type(instance).__mro__
    next_class = mro[mro.index(cls) + 1] if cls in mro else None
    if next_class is object:
        object.__init__(instance)
        for name in TRANSFORM_FIELDS:
            if name in kwargs:
                setattr(instance, name, float(kwargs[name]))
        return
    parent.__init__(*args, **kwargs)


def chainCall(instance, name, *args, **kwargs):
    """调 MRO 里 :class:`IAnimationBase` **之后**的下一个同名方法（没有就返回 ``None``）。

    只在 :class:`IAnimationBase` 里定义的方法中调用 —— 锚点就是 ``IAnimationBase``，
    所以"宿主"永远是它下游的类（``Sprite2D`` / ``GameObject`` / 用户的基类）。
    """
    method = getattr(super(IAnimationBase, instance), name, None)
    if method is None:
        return None
    return method(*args, **kwargs)


class IAnimationBase:
    """动画基类 / 接口（见模块文档）。

    子类只要做两件事：① 在 ``__init__`` 里算好 ``self.duration``；
    ② 实现 :meth:`updateAnimation`（按当前时间摆姿态）与 :meth:`renderAnimation`
    （绘制自己的东西）。
    """

    def __init__(self, *args, **kwargs) -> None:
        # 先让宿主初始化（GameObject / Sprite2D 的默认值先落地），再装时间轴
        _collaborativeInit(IAnimationBase, self, *args, **kwargs)
        #: 时间轴位置（秒）
        self.animation_time: float = 0.0
        #: 播放速度倍数（负值 = 倒放；0 = 冻结时间轴但不改播放标志）
        self.animation_speed: float = 1.0
        #: 是否循环（False = 播到端点就 ``finished`` 并自动暂停）
        self.animation_loop: bool = True
        #: 是否在推进时间轴
        self.playing: bool = False
        #: 是否已经播完（只有单次模式会置位；``play`` / ``restart`` / ``seek`` 清除）
        self.finished: bool = False
        #: 累计跨过循环边界的次数（调试 / 回调节流用）
        self.animation_loops: int = 0
        #: 动画总时长（秒）。子类在 ``__init__`` 里设；≤0 表示时间轴不推进。
        self.duration: float = DEFAULT_DURATION
        #: 播完 / 每次循环的回调（``None`` 或 ``callable(动画对象)``；
        #: 与 ``ParticleSystem.on_spawn`` 同风格，直接赋值即可）
        self.on_finish = None
        self.on_loop = None
        # 宿主没提供的共享变换字段兜底（宿主设过的值一律保留）
        if not hasattr(self, "x"):
            self.x = 0.0
        if not hasattr(self, "y"):
            self.y = 0.0
        if not hasattr(self, "z"):
            self.z = 0.0
        if not hasattr(self, "r"):
            self.r = 0.0

    # === 时间轴读数 ===

    @property
    def progress(self) -> float:
        """归一化进度 ``0~1``（``duration <= 0`` 时恒为 0）。"""
        duration = float(self.duration)
        if duration <= 0.0:
            return 0.0
        return min(max(float(self.animation_time) / duration, 0.0), 1.0)

    @property
    def time(self):
        """``animation_time`` 的旧名字（旧 ``AnimationFrame.time`` 兼容用）。"""
        return self.animation_time

    @time.setter
    def time(self, value) -> None:
        self.animation_time = float(value)

    @property
    def max_time(self):
        """``duration`` 的旧名字（旧 ``AnimationFrame.max_time`` 兼容用）。"""
        return self.duration

    @max_time.setter
    def max_time(self, value) -> None:
        self.duration = float(value)

    @staticmethod
    def wrapTime(time: float, duration: float) -> float:
        """把 ``time`` 折回 ``[0, duration)``（支持负数，倒放用）。"""
        return wrapTime(time, duration)

    # === 播放控制（全部返回 self，方便链式）===

    def play(self, restart: bool = False) -> "IAnimationBase":
        """开始（或继续）播放；``restart=True`` 则先从 0 开始。"""
        if restart:
            self.animation_time = 0.0
            self.animation_loops = 0
        self.playing = True
        self.finished = False
        self.refreshAnimation()
        return self

    def pause(self) -> "IAnimationBase":
        """暂停：时间轴停在原地（姿态保持当前帧）。"""
        self.playing = False
        return self

    def stop(self) -> "IAnimationBase":
        """停止并回到 0（姿态刷新到第 0 帧）。"""
        self.playing = False
        self.finished = False
        self.animation_time = 0.0
        self.refreshAnimation()
        return self

    def restart(self) -> "IAnimationBase":
        """回到 0 并继续播放（= ``reset()`` + ``play()``）。"""
        self.reset()
        self.playing = True
        return self

    def reset(self) -> "IAnimationBase":
        """只把时间轴放回 0 并刷新姿态，**不改**播放状态（旧 ``reset()`` 语义）。"""
        self.animation_time = 0.0
        self.animation_loops = 0
        self.finished = False
        self.refreshAnimation()
        return self

    def togglePlay(self) -> bool:
        """切换播放 / 暂停，返回切换**之后**是否在播放。"""
        if self.playing:
            self.pause()
        else:
            self.play()
        return self.playing

    def setSpeed(self, speed: float) -> "IAnimationBase":
        """设置播放速度倍数（负值 = 倒放）。"""
        self.animation_speed = float(speed)
        return self

    def setLoop(self, loop: bool = True) -> "IAnimationBase":
        """设置是否循环。"""
        self.animation_loop = bool(loop)
        return self

    def seek(self, time: float, refresh: bool = True) -> "IAnimationBase":
        """跳到 ``time`` 秒（循环模式自动折回；单次模式夹到 ``[0, duration]``）。"""
        value = float(time)
        duration = float(self.duration)
        if self.animation_loop:
            self.animation_time = self.wrapTime(value, duration)
        elif duration > 0.0:
            self.animation_time = min(max(value, 0.0), duration)
        else:
            self.animation_time = 0.0
        if not self.animation_loop and duration > 0.0 \
                and self.animation_time >= duration:
            # 跳到终点 = 直接算播完
            self.finished = True
            self.playing = False
        if refresh:
            self.refreshAnimation()
        return self

    def refreshAnimation(self, dt: float = 0.0) -> "IAnimationBase":
        """按当前时间立刻重算一次姿态（不推进时间轴）。"""
        self.updateAnimation(float(dt))
        return self

    # === 时间轴推进 ===

    def tickAnimation(self, dt: float) -> float:
        """推进时间轴，返回本帧**实际消耗**的时间（已乘速度；暂停/停止/零时长时为 0）。

        循环模式：折回 ``[0, duration)``；一帧内跨多次边界只回调一次 ``on_loop``
        （``animation_loops`` 里记着真实次数，避免高倍速下回调风暴）。
        单次模式：撞到端点就 ``finished = True``、``playing = False``、回调 ``on_finish``。

        无论是否在推进，最后都会调一次 :meth:`updateAnimation`（暂停时入参是 0）——
        所以暂停/拖动时间轴之后姿态依然是当前时间的姿态。
        """
        duration = float(self.duration)
        if not self.playing or duration <= 0.0:
            self.updateAnimation(0.0)
            return 0.0
        advance = float(dt) * float(self.animation_speed)
        if advance == 0.0:
            self.updateAnimation(0.0)
            return 0.0
        raw = float(self.animation_time) + advance
        if self.animation_loop:
            loops = int(abs(raw) // duration)
            self.animation_time = self.wrapTime(raw, duration)
            if loops:
                self.animation_loops += loops
                if self.on_loop is not None:
                    self.on_loop(self)
        elif advance > 0.0 and raw >= duration:
            self.animation_time = duration
            self._finishAnimation()
        elif advance < 0.0 and raw <= 0.0:
            self.animation_time = 0.0
            self._finishAnimation()
        else:
            self.animation_time = raw
        self.updateAnimation(advance)
        return advance

    def _finishAnimation(self) -> None:
        """单次播放到达端点（只回调一次）。"""
        if self.finished:
            return
        self.finished = True
        self.playing = False
        if self.on_finish is not None:
            self.on_finish(self)

    # === 子类钩子（默认什么都不做）===

    def updateAnimation(self, dt: float) -> None:
        """每帧按当前时间摆姿态（``dt`` = 本帧消耗的时间，暂停时为 0）。"""

    def renderAnimation(self, dt: float, fps: float) -> None:
        """绘制动画自己的几何（宿主的方法已经先跑过了）。"""

    def releaseAnimation(self) -> None:
        """释放动画持有的 GPU 资源（注销/换素材时调）。"""

    # === 与 GameObject 接驳：协作式生命周期 ===

    def update(self, dt: float, tps: float) -> None:
        """先让宿主 ``update``（MRO 下游），再推进动画时间轴。"""
        chainCall(self, "update", dt, tps)
        self.tickAnimation(dt)

    def render(self, dt: float, fps: float) -> None:
        """先让宿主 ``render``（MRO 下游），再画动画自己的几何。"""
        chainCall(self, "render", dt, fps)
        self.renderAnimation(dt, fps)

    def onRemoved(self) -> None:
        """注销：释放动画资源，再把通知透给宿主（例如 ``Sprite2D.onRemoved``）。"""
        self.releaseAnimation()
        chainCall(self, "onRemoved")

    def __repr__(self) -> str:
        state = "播放中" if self.playing else ("已播完" if self.finished else "暂停")
        return "<%s %s t=%.3f/%.3f x%.2g %s>" % (
            type(self).__name__, state, float(self.animation_time),
            float(self.duration), float(self.animation_speed),
            "循环" if self.animation_loop else "单次")


#: 旧名字（``pyglgame.gameobject.animation.AnimationBase.AnimationBase`` 继续可用）
AnimationBase = IAnimationBase
