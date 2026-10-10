import atexit
from typing import Any, Callable

from .BaseWindow import BaseWindow
from .openal.Device import Device
from .openal.Player import Player, NullPlayer
from .openal.synth.Synthesizer import Synthesizer
from .xyazhServer.ConsoleMessage import ConsoleMessage


class App:
    instance = None

    def __new__(cls, size: tuple = (960, 540), position: tuple = (0, 0), is_full_screen: bool = False) -> "App":
        if cls.instance is None:
            cls.instance = super().__new__(cls)
            cls.instance.__first_init__(
                size=size, position=position, is_full_screen=is_full_screen)
        return cls.instance

    def __first_init__(self, size: tuple = (960, 540), position: tuple = (0, 0), is_full_screen: bool = False) -> None:
        self.window: BaseWindow = BaseWindow(
            self,  size, position, is_full_screen)
        # 音频是可选依赖：DLL 缺失/设备不可用时降级为 NullPlayer，
        # 而不是让整个游戏（包括纯图形项目）启动失败
        self.audio_device = None
        self.audio_available = False
        self.audio_player = NullPlayer()
        self._audio_closed = False
        try:
            self.audio_device = Device()
            self.audio_player = Player(self.audio_device)
            self.audio_available = True
        except Exception as e:
            ConsoleMessage.printWarning(f"音频设备初始化失败，将以静音模式运行: {e}")
        Device.app_instance = self.audio_device
        Player.app_instance = self.audio_player
        # 钢琴合成器（音色是纯计算，**不在启动时渲染任何波形**；
        # 想避免第一次弹到某个音时卡一下，用 Synthesizer.prerender() 预热）
        self.synthesizer = Synthesizer(player=self.audio_player,
                                       device=self.audio_device)
        Synthesizer.app_instance = self.synthesizer
        # 正常退出（含 GLUT 主循环返回）时释放 OpenAL 设备，
        # 否则会打印 "AL lib: (EE) alc_cleanup: 1 device not closed"
        atexit.register(self._shutdownAudio)
        self.progress_bar = 0
        self.init_fucs = []

    def _shutdownAudio(self) -> None:
        if self._audio_closed:
            return
        self._audio_closed = True
        try:
            if getattr(self, "synthesizer", None) is not None:
                # 先归还合成器手上的 buffer（还在播/在飞的），再炸设备
                self.synthesizer.release()
            if self.audio_player is not None:
                self.audio_player.release()
            if self.audio_device is not None:
                self.audio_device.cleanup()
        except Exception:
            pass
        Device.app_instance = None
        Player.app_instance = None
        Synthesizer.app_instance = None

    def __init__(self, size: tuple = (960, 540), position: tuple = (0, 0), is_full_screen: bool = False) -> None:
        pass

    def start(self, title: str = "New Functional") -> None:
        self.window.run(title)

    def onLoad(self, *args: Any, call_back: Callable = None,
               **kwargs: Any) -> Callable:
        """注册"窗口 / GL 上下文就绪后"要跑的回调（建纹理、建关卡都放这里）。

        支持 ``@app.onLoad``（不带括号）与 ``@app.onLoad(scale=2)``（参数原样转给
        回调）两种写法。⚠ 不带括号时函数本身会作为位置参数传进来：必须显式识别，
        否则会被当成"要转发的参数"、回调永远不注册（窗口一片黑，踩过）。
        """
        if len(args) == 1 and callable(args[0]) and call_back is None and not kwargs:
            func = args[0]                    # 写法 ①：把函数本身直接注册掉
            self.init_fucs.append((func, None, (), {}))
            return func

        def decorator(func: Callable) -> Callable:
            self.init_fucs.append((func, call_back, args, kwargs))
            return func
        return decorator

    def addInitFunc(self, func: Callable, *args: Any, call_back: Callable = None,
                    **kwargs: Any) -> None:
        self.init_fucs.append((func, call_back, args, kwargs))

    # === 主循环控制 ===

    def setMaxFps(self, fps: float) -> None:
        """限制最大帧率（0 = 不限制）。离线出片时应设成与视频帧率一致。"""
        self.window.game_loop.max_fps = float(fps)

    def setFixedRenderStep(self, dt: float) -> None:
        """渲染使用固定步长（秒）；0 = 使用真实帧时长（默认）。

        ⚠ 固定步长会把 dt 与真实时间**脱钩**（本帧真实花了 25ms，画面仍只前进
        ``dt``）：离线做确定性渲染时有用，但画面会越走越慢于真实时间，按真实时间
        推进的音频（例如整曲混音的 PCM）就会和它错开 —— 要"音画/录像同一根时间轴"
        就不要开。
        """
        self.window.game_loop.render_step = float(dt)

    def setRealTimeUpdate(self, enable: bool = True) -> None:
        """逻辑帧的 ``dt`` 也用**真实消耗时间**（默认 False = 固定步长累加器）。

        打开后每帧跑一次 update，``dt`` = 上一帧到这一帧真实过去了多久（``tps`` 也是
        实测值），所有在 update 里积分的量都与墙上时钟逐帧对齐，和渲染帧/音频/录像
        同轴；代价是物理不再定步长 —— 有物理世界的 demo 别开
        （:class:`pyglgame.physics.Physics2D` 依赖稳定的 100Hz 累加器）。
        """
        self.window.game_loop.real_time_update = bool(enable)

    def setFixedUpdateTps(self, tps: float) -> None:
        """固定更新频率（默认 100Hz）。"""
        self.window.game_loop.FIXED_TPS = float(tps)

    def setAbsoluteFixedStep(self, fps: float = -1) -> None:
        """不论真实帧率为多少，GameObject 拿到的FPS/TPS都是固定值（默认 -1 = 关闭）。"""
        self.window.game_loop.absolute_fixed_fps = float(fps)
