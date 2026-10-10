import sys
import time
from OpenGL.GL import *
from OpenGL.GLU import *
from OpenGL.GLUT import *
from .tools.xyaHelper import FpsStats
from .tools.glDebug import GLDebug
from .openal.synth.Synthesizer import Synthesizer
from .RenderGlobal import RenderGlobal
from .shader.ShaderManager import ShaderManager
from .render.RenderBuffer import *


class GameMainLoop:
    """主循环。

    更新与渲染都在 GL 线程上（单线程固定步长累加器）：
    旧实现用 ``xyaTimerFunc`` 在后台线程里跑 update，会与渲染线程并发读写
    同一批对象，导致 ``RuntimeError: Set changed size during iteration``、
    状态撕裂、以及输入事件丢失。
    """

    FIXED_TPS = 100.0           # 固定更新频率（旧实现是 xyaTimerFunc(10) 即 10ms）
    MAX_STEPS_PER_FRAME = 2     # 单帧最多补几步，避免“死亡螺旋”

    def __init__(self) -> None:
        self.render_last_time = 0.0
        self.render_dt = 0.0
        self.frame_dt = 0.0
        self.render_global = None
        self.window = None
        self.app = None
        self._accumulator = 0.0
        self._last_frame_time = None
        self.fps_stats = FpsStats()
        self.debug_gl = False
        # 限帧（0 = 不限制）。离线出片时必须限制到视频帧率，
        # 否则"渲染时间"和"视频时间轴"会脱钩（音频与画面不同步）。
        self.max_fps = 0.0
        # >0 时渲染用固定步长：dt 与真实帧耗时**脱钩**（离线确定性渲染用；
        # 要画面/声音/录像在同一根真实时间轴上就别开，见 real_time_update）
        self.render_step = 0.0
        # True = 逻辑帧的 dt 也返回真实消耗时间（一帧一步，dt = 真实帧时长）。
        # 默认 False = 固定步长累加器（物理稳定、可复现）。见 _updateByRealTime
        self.real_time_update = False
        self.absolute_fixed_fps = -1

    def start(self) -> None:
        self.nextFrame()
        self.drawProgressBar(-1, -0.8, 2, 0.1, 0.01)
        self.app = RenderGlobal.instance.app
        self.render_global = RenderGlobal.instance
        self.window = self.render_global.window
        self.app = self.render_global.app
        # 主着色器必须最早可用；bloom/fractal noise 的 compute shader 改为
        # 使用到时懒加载（2D 游戏不再依赖 compute 能力）
        self.render_global.dis_shader = ShaderManager.loadShader("dis")
        self.nextFrame()
        self.drawProgressBar(-1, -0.8, 2, 0.1, 0.03)
        for i in range(len(self.app.init_fucs)):
            self.nextFrame()
            self.drawProgressBar(-1, -0.8, 2, 0.1, (i+4)/100)
            func, call_back, args, kwargs = self.app.init_fucs[i]
            r = func(*args, **kwargs)
            if call_back:
                if r is None:
                    call_back()
                else:
                    call_back(r)
        self.drawProgressBar(-1, -0.8, 2, 0.1, 1.0)
        self.nextFrame()
        self.render_global.dis_shader.use()
        self.render_global.start()

    def nextFrame(self) -> None:
        glutSwapBuffers()
        if glutGetWindow() == 0:
            sys.exit(0)
        glutMainLoopEvent()
        glutPostRedisplay()
        glClearColor(0.1, 0.1, 0.12, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

    def drawProgressBar(self, x: float, y: float, w: float, h: float,
                        progress: float) -> None:
        progress = max(0.0, min(1.0, progress))
        glColor3f(0.2, 0.2, 0.2)
        glBegin(GL_QUADS)
        glVertex3f(x, y, 0.2)
        glVertex3f(x + w, y, 0.2)
        glVertex3f(x + w, y + h, 0.2)
        glVertex3f(x, y + h, 0.2)
        glEnd()
        glColor3f(0.8, 0.8, 0.8)
        glBegin(GL_QUADS)
        glVertex3f(x, y, 0.1)
        glVertex3f(x + w * progress, y, 0.1)
        glVertex3f(x + w * progress, y + h, 0.1)
        glVertex3f(x, y + h, 0.1)
        glEnd()

    def doUpdate(self, dt: float, tps: float) -> None:
        self.render_global.updateLayer(dt, tps)
        # 钢琴合成器：处理"松开琴键"的制音淡出与播完 buffer 的回收。
        # 没有正在淡出的音时它只做一次空判断，开销可以忽略。
        synthesizer = Synthesizer.app_instance
        if synthesizer is not None:
            synthesizer.update(dt)

    def doRender(self, dt: float, fps: float) -> None:
        glClearColor(*self.render_global.bg)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        self.render_global.using_shader.uniform2f(
            "win_wh", self.window.size.w, self.window.size.h)
        self.render_global.renderLayer(dt, fps)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        for camera in tuple(self.render_global.cameras):
            camera.windowCameraDraw()
        # Object ID / Mask 调试显示（默认关；renderer.setObjectIdDebug(True) 打开）
        if getattr(self.render_global, "object_id_debug", False):
            self.render_global.drawObjectIdDebug()
        # glFlush()
        glutSwapBuffers()

    def renderLoop(self) -> None:
        now = time.perf_counter()
        if self._last_frame_time is None:
            self._last_frame_time = now
        # 真实帧时长 = 上一帧"开始"到这一帧"开始"的墙上时钟耗时：包含渲染、抓帧、
        # 所有阶段开销以及限帧 sleep。它就是这一帧真实消耗掉的时间。
        frame_dt = now - self._last_frame_time
        self._last_frame_time = now
        self.frame_dt = frame_dt
        self.render_dt = frame_dt
        self.render_last_time = now
        fps = 1.0 / frame_dt if frame_dt > 0 else 0.0
        # 更新（与渲染同线程，不再有跨线程竞态）。两条路都保证"逻辑时间总和 = 真实
        # 时间"，区别只在 dt 的形状：固定步长（默认）/ 真实帧时长（real_time_update）。
        # update 之前把输入线程/回调攒下的按键合并进"本帧可读"集合：
        # 这样上一帧渲染期间（甚至上一次 update 之后）到达的按键都能被这一步读到。
        self.window.beginInputFrame()
        if self.absolute_fixed_fps >= 0:
            fps = self.absolute_fixed_fps
            frame_dt = 1.0 / fps if fps > 0 else 0.0
            self._accumulator = 0.0
            self.doRender(frame_dt, fps)
            self.doUpdate(frame_dt, fps)
            did_update = True
        else:
            if self.real_time_update:
                did_update = self._updateByRealTime(frame_dt)
            else:
                did_update = self._updateByFixedSteps(frame_dt)

        # 更新阶段一结束就清理“本帧输入”，必须在 **update 之后、render 之前**：
        # 渲染占了整帧的绝大部分时间，而 keyboard 钩子的回调跑在它自己的线程里、
        # 随时可能插进来 —— 放在帧末清（旧实现）会把渲染期间到达的按键/点击整个
        # 吞掉，getKeyDown 于是只有极小概率能活着走到下一次 update（表现为“不好使”）。
        # 只在真的跑过 update 时清理：帧率高于 FIXED_TPS 时本帧可能一步都没跑，
        # 那时清掉等于一次 update 都没读到就丢掉事件。
        if did_update:
            self.window.clearMouseHit()
            self.window.clearKeyDown()

        self.fps_stats.tick(fps, frame_dt)
        # 渲染帧 dt：默认就是上面量到的真实帧时长（画面因此与墙上时钟同轴，和按
        # 真实时间推进的音频、以及按真实时间出片的录像一致）；只有显式要求固定步长
        # 时才换成 render_step（那时 dt 与真实时间脱钩，见 setFixedRenderStep）。
        render_dt = self.render_step if self.render_step > 0 else frame_dt
        render_fps = 1.0 / render_dt if render_dt > 0 else 0.0
        self.doRender(render_dt, render_fps)
        if self.debug_gl:
            GLDebug.check("frame end")

        # 限帧：睡到下一帧边界（保持 max_fps 与视频帧率一致，音画才不会漂）
        if self.max_fps > 0:
            target = 1.0 / self.max_fps
            worked = time.perf_counter() - now
            rest = target - worked
            if rest > 0:
                time.sleep(rest)

    def _updateByFixedSteps(self, frame_dt: float) -> bool:
        """固定步长累加器（默认）：一帧补若干步，``dt`` = 每步代表的真实时间。

        步长固定（``1/FIXED_TPS``）→ 物理/数值积分稳定、可复现；逻辑时间的总和仍等于
        真实时间，但**逻辑帧与渲染帧并不重合**：不到一步的余量要留到下一帧，帧时间
        远超预期时（断点/卡顿）超过 ``MAX_STEPS_PER_FRAME`` 的积压会被丢掉 → 逻辑会
        永久落后真实时间一小段（物理 demo 的标准取舍）。

        返回本帧是否真的跑过 update。
        """
        step = 1.0 / self.FIXED_TPS
        self._accumulator += frame_dt
        steps = 0
        while self._accumulator >= step and steps < self.MAX_STEPS_PER_FRAME:
            self.doUpdate(step, self.FIXED_TPS)
            self._accumulator -= step
            steps += 1
        if steps >= self.MAX_STEPS_PER_FRAME:
            # 帧时间远超预期（例如被断点/卡顿阻塞），丢弃积压，避免连续补帧
            self._accumulator = 0.0
        return steps > 0

    def _updateByRealTime(self, frame_dt: float) -> bool:
        """一帧一步，``dt`` = 真实帧时长（``real_time_update`` 打开时走这里）。

        每帧 update 一次、拿到的 ``dt`` 就是"上一帧到这一帧真实过去了多久"，所以
        在 update 里积分的量（位置、寿命、粒子年龄、玩法计时……）与墙上时钟**逐帧
        对齐**，和渲染帧的 dt 是同一个数 —— 画面、声音、录像共用一根时间轴。
        代价：不再是定步长，物理稳定性/可复现性没有保证（有物理世界时别开）。

        返回本帧是否真的跑过 update（首帧 ``frame_dt = 0`` 时跳过）。
        """
        if frame_dt <= 0.0:
            return False
        self._accumulator = 0.0        # 固定步长的余量作废，两种模式不混用
        self.doUpdate(frame_dt, 1.0 / frame_dt)
        return True

    def run(self, fixed_tps: float = None) -> None:
        if fixed_tps is not None:
            self.FIXED_TPS = float(fixed_tps)
        glutIdleFunc(self.renderLoop)
