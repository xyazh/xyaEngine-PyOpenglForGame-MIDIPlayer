import os
import sys
import threading
import traceback
import locale
from typing import TYPE_CHECKING
from OpenGL.GL import *
from OpenGL.GLU import *
from OpenGL.GLUT import *
from .GameMainLoop import GameMainLoop
from .math.Size import Size
from .RenderGlobal import RenderGlobal
from .xyazhServer.ConsoleMessage import ConsoleMessage

if TYPE_CHECKING:
    from .App import App

try:
    import keyboard
except ImportError:  # 可选依赖：没有时退回 GLUT 键盘回调
    keyboard = None


class BaseWindow:
    def __init__(self, app: "App", size: tuple[float], position: tuple[float], full_screen: bool) -> None:
        self.setWindownSize(size)
        self.app = app
        self.render_global = RenderGlobal(app=app, window=self)
        self.pos_x, self.pos_y = position
        self.full_screen: bool = full_screen
        self.initMouse()
        self.key_set: set[str] = set()
        self.key_down_set: set[str] = set()
        # 输入源（keyboard 钩子线程 / GLUT 回调）只往 pending 里塞"刚按下"的键；
        # 每帧 update 之前由 beginInputFrame() 合并进 key_down_set，
        # 这样"渲染期间/上一次 update 期间"到达的按键一次都不会被漏掉。
        self.key_down_pending: set[str] = set()
        # keyboard 库的钩子回调运行在它自己的监听线程里，读写以下集合必须加锁
        self._input_lock = threading.RLock()
        self.game_loop = GameMainLoop()

    def setWindownSize(self, size: tuple[float]) -> None:
        self.size = Size(*size)
        self.size_x, self.size_y = self.size.w, self.size.h

    def setFullScreen(self) -> None:
        self.full_screen = True

    def run(self, title: str) -> None:
        glutInit(sys.argv)
        # 关窗让 glutMainLoop 返回（而非 freeglut 直接 exit），run() 之后的收尾才会执行
        try:
            glutSetOption(GLUT_ACTION_ON_WINDOW_CLOSE,
                          GLUT_ACTION_GLUTMAINLOOP_RETURNS)
        except Exception:
            pass
        # 显示模式必须在建窗之前设置（写在 glutCreateWindow 之后对已建窗口不生效）
        glutInitDisplayMode(GLUT_DOUBLE | GLUT_RGBA |
                            GLUT_DEPTH | GLUT_STENCIL | GLUT_MULTISAMPLE)
        # 显式请求 4.3 兼容上下文：dis.vert/dis.frag 是 #version 430，
        # bloom/fractal 需要 compute shader；进度条仍使用固定管线绘制
        try:
            glutInitContextVersion(4, 3)
            glutInitContextProfile(GLUT_COMPATIBILITY_PROFILE)
        except Exception as e:
            ConsoleMessage.printWarning(f"请求 OpenGL 4.3 上下文失败，使用默认上下文: {e}")
        glutInitWindowPosition(self.pos_x, self.pos_y)
        glutInitWindowSize(self.size.w, self.size.h)
        glutCreateWindow(title.encode(locale.getpreferredencoding()))
        if self.full_screen:
            glutFullScreen()
        glutMouseFunc(self._mouseHit)
        glutMotionFunc(self._onMouseMove)
        glutPassiveMotionFunc(self._mouseMove)
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glDepthMask(GL_TRUE)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_CULL_FACE)
        glEnable(GL_MULTISAMPLE)
        glEnable(GL_VERTEX_PROGRAM_POINT_SIZE)
        glutDisplayFunc(self.displayFunc)
        glutCloseFunc(self.onClose)
        self.initKeyboard()
        self.game_loop.start()
        self.game_loop.run()
        glutMainLoop()
        # 窗口关闭后主动释放音频设备
        try:
            self.app._shutdownAudio()
        except Exception:
            pass
        # freeglut 默认是"关窗即 exit"，上面收尾做完后显式结束进程：
        # 否则某些非守护线程（keyboard 钩子、驱动线程）会让进程挂在退出阶段
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    def initKeyboard(self) -> None:
        """优先使用 keyboard 库（支持全局热键）；不可用时退回 GLUT 键盘回调。"""
        if keyboard is not None:
            try:
                keyboard.hook(self._keyHook)
                return
            except Exception as e:
                ConsoleMessage.printWarning(
                    f"keyboard 钩子安装失败（可能需要管理员权限），改用 GLUT 键盘回调: {e}")
        glutKeyboardFunc(self._onGlutKeyDown)
        glutKeyboardUpFunc(self._onGlutKeyUp)
        glutSpecialFunc(self._onGlutSpecialDown)
        glutSpecialUpFunc(self._onGlutSpecialUp)
        glutIgnoreKeyRepeat(1)

    def onClose(self) -> None:
        print("Bye")

    def displayFunc(self) -> None:
        self.size_x = glutGet(GLUT_WINDOW_WIDTH)
        self.size_y = glutGet(GLUT_WINDOW_HEIGHT)
        self.size.updateSize(self.size_x, self.size_y)

    def _keyDown(self, key: str) -> None:
        """按下：``key_set`` 记"按住"，``key_down_pending`` 记**真正的边沿**。

        - 自动重复的 keydown 必须去重（否则"按住 1 秒"是几十次 getKeyDown）；
        - 边沿只写 pending，由 :meth:`beginInputFrame` 在每帧 update 前合并，
          这样"上一帧清理之后才到达"的按键也不会丢。
        """
        with self._input_lock:
            repeat = key in self.key_set
            self.key_set.add(key)
            if not repeat:
                self.key_down_pending.add(key)

    def _keyUp(self, key: str) -> None:
        # 只清"按住"状态：边沿标记要留给 update 读，由 clearKeyDown 统一收尾
        with self._input_lock:
            self.key_set.discard(key)

    def beginInputFrame(self) -> None:
        """把输入线程/回调攒下的"按下"事件合并进本帧可读集合。

        必须在 update **之前**调用（:class:`GameMainLoop` 每帧开头调用），
        任何时刻到达的按键都会在下一次 update 阶段被完整读到一次。
        """
        with self._input_lock:
            if self.key_down_pending:
                self.key_down_set |= self.key_down_pending
                self.key_down_pending.clear()

    def _keyHook(self, t: "keyboard.KeyboardEvent") -> None: # type: ignore
        if getattr(t, "event_type", None) == "down":
            self._keyDown(t.name)
        else:
            self._keyUp(t.name)

    # === GLUT 键盘回调（没有 keyboard 库时使用）===

    _GLUT_SPECIAL_NAMES = {
        GLUT_KEY_UP: "up",
        GLUT_KEY_DOWN: "down",
        GLUT_KEY_LEFT: "left",
        GLUT_KEY_RIGHT: "right",
        GLUT_KEY_HOME: "home",
        GLUT_KEY_END: "end",
        GLUT_KEY_PAGE_UP: "page up",
        GLUT_KEY_PAGE_DOWN: "page down",
        GLUT_KEY_INSERT: "insert",
        GLUT_KEY_DELETE: "delete",
    }

    def _glutKeyName(self, key: "int | bytes") -> str:
        try:
            if isinstance(key, (bytes, bytearray)):
                char = key.decode("latin-1")
            else:
                char = chr(key)
        except Exception:
            return ""
        if char == " ":
            return "space"
        if char in ("\r", "\n"):
            return "enter"
        if char == "\x1b":
            return "esc"
        if char == "\t":
            return "tab"
        return char.lower()

    def _syncGlutModifiers(self) -> None:
        """GLUT 不把修饰键当按键事件，只能在事件里通过 glutGetModifiers 同步。"""
        try:
            mods = glutGetModifiers()
        except Exception:
            return
        for flag, name in ((GLUT_ACTIVE_SHIFT, "shift"),
                           (GLUT_ACTIVE_CTRL, "ctrl"),
                           (GLUT_ACTIVE_ALT, "alt")):
            if mods & flag:
                self._keyDown(name)
            else:
                self._keyUp(name)

    def _onGlutKeyDown(self, key: "int | bytes", x: int, y: int) -> None:
        name = self._glutKeyName(key)
        if name:
            self._keyDown(name)
        self._syncGlutModifiers()

    def _onGlutKeyUp(self, key: "int | bytes", x: int, y: int) -> None:
        name = self._glutKeyName(key)
        if name:
            self._keyUp(name)
        self._syncGlutModifiers()

    def _onGlutSpecialDown(self, key: int, x: int, y: int) -> None:
        name = self._GLUT_SPECIAL_NAMES.get(key)
        if name:
            self._keyDown(name)
        self._syncGlutModifiers()

    def _onGlutSpecialUp(self, key: int, x: int, y: int) -> None:
        name = self._GLUT_SPECIAL_NAMES.get(key)
        if name:
            self._keyUp(name)
        self._syncGlutModifiers()

    def clearKeyDown(self) -> None:
        """清掉"本帧按下"标记（主循环在 update 阶段之后调用）。

        只清 :attr:`key_down_set`；这期间新到达的按键都在 ``key_down_pending``
        里等下一次 :meth:`beginInputFrame`，不会被丢掉。
        """
        with self._input_lock:
            self.key_down_set.clear()

    def getKey(self, key: str) -> bool:
        with self._input_lock:
            return key in self.key_set

    def getKeyDown(self, key: str) -> bool:
        with self._input_lock:
            return key in self.key_down_set

    def _mouseHit(self, button: int, state: int, x: int, y: int) -> None:
        self.on_mouse_x, self.on_mouse_y = x, y
        if button == 0:
            if state == 0:
                self.mouse_left_on_hit = True
                self.mouse_left_button_on = True
            else:
                self.mouse_left_button_on = False
            return
        if button == 1:
            if state == 0:
                self.mouse_middle_on_hit = True
                self.mouse_middle_button_on = True
            else:
                self.mouse_middle_button_on = False
            return
        if button == 2:
            if state == 0:
                self.mouse_right_on_hit = True
                self.mouse_right_button_on = True
            else:
                self.mouse_right_button_on = False
            return

    def _onMouseMove(self, x: int, y: int) -> None:
        self.on_mouse_x, self.on_mouse_y = x, y

    def _mouseMove(self, x: int, y: int) -> None:
        self.mouse_x, self.mouse_y = x, y

    def initMouse(self) -> None:
        self.mouse_left_button_on = False
        self.mouse_middle_button_on = False
        self.mouse_right_button_on = False
        self.mouse_left_on_hit = False
        self.mouse_middle_on_hit = False
        self.mouse_right_on_hit = False
        self.mouse_x = 0
        self.mouse_y = 0
        self.on_mouse_x = 0
        self.on_mouse_y = 0

    def clearMouseHit(self) -> None:
        self.mouse_left_on_hit = False
        self.mouse_middle_on_hit = False
        self.mouse_right_on_hit = False

    def getMouse(self) -> tuple:
        return (self.mouse_x, self.mouse_y)

    def getOnMouse(self) -> tuple:
        return (self.on_mouse_x, self.on_mouse_y)

    def getMiddleClickMouse(self) -> tuple:
        if self.mouse_middle_on_hit:
            return (self.on_mouse_x, self.on_mouse_y)
        return None

    def getRightClickMouse(self) -> tuple:
        if self.mouse_right_on_hit:
            return (self.on_mouse_x, self.on_mouse_y)
        return None

    def getLeftClickMouse(self) -> tuple:
        if self.mouse_left_on_hit:
            return (self.on_mouse_x, self.on_mouse_y)
        return None

    def getLeftOnMouse(self) -> tuple:
        if self.mouse_left_button_on:
            return (self.on_mouse_x, self.on_mouse_y)
        return None

    def getMiddleOnMouse(self) -> tuple:
        if self.mouse_middle_button_on:
            return (self.on_mouse_x, self.on_mouse_y)
        return None

    def getRightOnMouse(self) -> tuple:
        if self.mouse_right_button_on:
            return (self.on_mouse_x, self.on_mouse_y)
        return None
