"""把纹理内容异步读到 CPU（双 PBO + 工作线程）。

原先 demo 里是每帧同步 ``glGetTextureImage`` + ``cv2`` 滤波 + ``VideoWriter.write``，
全部压在渲染线程上：profile.prof 显示单帧 20ms 里有 17.6ms 花在这条链路上。

这里把“等待 GPU 画完”变成异步（双 PBO + fence），把图像处理与写盘放到工作
线程；渲染线程只做一次 DMA 拷贝。任何一步失败（老驱动/缺少扩展）都会自动
回退到同步路径，不会让游戏挂掉。
"""

import ctypes
import queue
import threading

import numpy as np
from OpenGL.GL import *

from ..xyazhServer.ConsoleMessage import ConsoleMessage


class AsyncFrameGrabber:
    """异步帧抓取器。

    :param callback: ``callback(frame)``，在工作线程中调用；
        ``frame`` 是形状 ``(height, width, 4)`` 的 numpy 视图（默认 float32）。
    :param buffers: PBO 数量（双缓冲=2），越多越不容易丢帧，但越占显存。
    :param queue_size: 待处理帧队列长度，满了直接丢帧（不阻塞渲染）。
    :param read_type: ``GL_FLOAT``（HDR/浮点纹理）或 ``GL_UNSIGNED_BYTE``。
    """

    def __init__(self, width: int, height: int, callback,
                 buffers: int = 3, queue_size: int = 12,
                 read_type: int = GL_FLOAT, use_pbo: bool = True,
                 name: str = "FrameGrabber") -> None:
        self.width = int(width)
        self.height = int(height)
        self.callback = callback
        self.buffer_count = max(1, int(buffers))
        self.read_type = read_type
        self.dtype = np.float32 if read_type == GL_FLOAT else np.uint8
        self.itemsize = np.dtype(self.dtype).itemsize
        self.nbytes = self.width * self.height * 4 * self.itemsize
        self.use_pbo = bool(use_pbo)
        self.name = name

        self._read_fbo = 0
        self._attached_texture = 0
        self._pbos = []
        self._fences = [None] * self.buffer_count
        self._cpu_buffers = []
        self._np_views = []
        self._in_flight = [False] * self.buffer_count
        self._free_slots = queue.Queue()
        for i in range(self.buffer_count):
            self._free_slots.put(i)
            raw = (ctypes.c_float if self.read_type == GL_FLOAT else ctypes.c_ubyte) * (
                self.width * self.height * 4)
            self._cpu_buffers.append(raw())
            self._np_views.append(np.frombuffer(
                self._cpu_buffers[-1], dtype=self.dtype).reshape(self.height, self.width, 4))

        self._frames = queue.Queue(maxsize=queue_size)
        self._stopped = False
        self.pending_pad = 0
        self._worker = threading.Thread(target=self._workerLoop, name=name, daemon=True)
        self._worker.start()

        self.dropped = 0
        self.captured = 0
        self.padded = 0
        self.fallback_warned = False

    # === 渲染线程 ===

    def grab(self, texture_id: int) -> bool:
        """读取纹理内容（异步）。返回是否发起了新的读取。

        若上一帧还没处理完（没有空闲 slot），会把这次“漏掉”的帧记在
        ``pending_pad`` 里，交给回调补写重复帧——绝不静默丢帧，
        否则视频时间轴会短于真实时间（音画错位）。
        """
        if self._stopped or texture_id == 0:
            return False
        self._collectFinished()
        try:
            slot = self._free_slots.get_nowait()
        except queue.Empty:
            self.dropped += 1
            self.pending_pad += 1
            return False
        try:
            self._bindTarget(texture_id)
            if self.use_pbo:
                self._readIntoPbo(slot)
            else:
                self._readSync(slot)
                self._dispatch(slot)
            self.captured += 1
            return True
        except Exception as e:
            if not self.fallback_warned:
                self.fallback_warned = True
                ConsoleMessage.printWarning(
                    f"异步回读不可用（{e}），回退为同步 glReadPixels")
                self.use_pbo = False
            self._free_slots.put(slot)
            return False

    def _bindTarget(self, texture_id: int) -> None:
        if self._read_fbo == 0:
            self._read_fbo = glGenFramebuffers(1)
            self._attached_texture = 0
        if self._attached_texture != texture_id:
            glBindFramebuffer(GL_FRAMEBUFFER, self._read_fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, texture_id, 0)
            self._attached_texture = texture_id
        glBindFramebuffer(GL_READ_FRAMEBUFFER, self._read_fbo)

    def _ensurePbo(self, slot: int) -> int:
        while len(self._pbos) <= slot:
            self._pbos.append(0)
        if self._pbos[slot] == 0:
            self._pbos[slot] = glGenBuffers(1)
            glBindBuffer(GL_PIXEL_PACK_BUFFER, self._pbos[slot])
            glBufferData(GL_PIXEL_PACK_BUFFER, self.nbytes, None, GL_STREAM_READ)
            glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
        return self._pbos[slot]

    def _readIntoPbo(self, slot: int) -> None:
        pbo = self._ensurePbo(slot)
        glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo)
        # 注意：必须传 c_void_p(0) 而不是 None。PyOpenGL 的包装版会把 None
        # 解释成“输出参数”并自己分配一块客户端内存，驱动会把该地址当成
        # PBO 内的偏移使用 → 越界 → GL_INVALID_OPERATION(1282)。
        glReadPixels(0, 0, self.width, self.height,
                     GL_RGBA, self.read_type, ctypes.c_void_p(0))
        glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
        if self._fences[slot] is not None:
            glDeleteSync(self._fences[slot])
        self._fences[slot] = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
        self._in_flight[slot] = True

    def _readSync(self, slot: int) -> None:
        """同步回退路径：直接读进 CPU 缓冲（会等待 GPU）。"""
        glBindFramebuffer(GL_READ_FRAMEBUFFER, self._read_fbo)
        glPixelStorei(GL_PACK_ALIGNMENT, 1)
        # 传 numpy 视图（PyOpenGL 对 numpy 输入数组的处理最稳）
        glReadPixels(0, 0, self.width, self.height, GL_RGBA,
                     self.read_type, self._np_views[slot])
        glPixelStorei(GL_PACK_ALIGNMENT, 4)

    def _collectFinished(self) -> None:
        """检查已完成的 PBO：取回数据并交给工作线程。"""
        if not self.use_pbo:
            return
        for slot in range(self.buffer_count):
            if not self._in_flight[slot]:
                continue
            fence = self._fences[slot]
            try:
                status = glClientWaitSync(fence, GL_SYNC_FLUSH_COMMANDS_BIT, 0)
            except Exception:
                status = GL_CONDITION_SATISFIED
            if status not in (GL_ALREADY_SIGNALED, GL_CONDITION_SATISFIED):
                continue
            glDeleteSync(fence)
            self._fences[slot] = None
            self._in_flight[slot] = False
            glBindBuffer(GL_PIXEL_PACK_BUFFER, self._pbos[slot])
            glGetBufferSubData(GL_PIXEL_PACK_BUFFER, 0, self.nbytes,
                               self._cpu_buffers[slot])
            glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
            self._dispatch(slot)

    def _dispatch(self, slot: int) -> None:
        repeat = 1 + self.pending_pad          # 把之前漏掉的帧补回来（重复上一帧）
        self.padded += self.pending_pad
        self.pending_pad = 0
        try:
            self._frames.put_nowait((slot, repeat))
        except queue.Full:
            self.dropped += 1
            self.pending_pad += repeat
            self._free_slots.put(slot)

    # === 工作线程 ===

    def _workerLoop(self) -> None:
        while True:
            try:
                item = self._frames.get(timeout=0.2)
            except queue.Empty:
                if self._stopped:
                    break
                continue
            if item is None:
                self._frames.task_done()
                break
            slot, repeat = item
            try:
                self.callback(self._np_views[slot], repeat)
            except Exception as e:
                ConsoleMessage.printError(f"{self.name} 回调执行失败: {e}")
            finally:
                self._free_slots.put(slot)
                self._frames.task_done()

    def stop(self, timeout: float = 5.0) -> None:
        """停止工作线程并等待队列处理完（窗口关闭时调用）。"""
        if self._stopped:
            return
        self._stopped = True
        try:
            self._frames.put(None, timeout=timeout)
        except queue.Full:
            pass
        self._worker.join(timeout=timeout)
        for fence in self._fences:
            if fence is not None:
                try:
                    glDeleteSync(fence)
                except Exception:
                    pass
        self._fences = [None] * self.buffer_count

    def release(self) -> None:
        """释放 PBO 与临时 FBO（需要在 GL 上下文里调用）。"""
        for pbo in self._pbos:
            if pbo:
                glDeleteBuffers(1, [pbo])
        self._pbos = []
        if self._read_fbo:
            glDeleteFramebuffers(1, [self._read_fbo])
            self._read_fbo = 0
            self._attached_texture = 0

    @property
    def stats(self) -> dict:
        return {"captured": self.captured, "dropped": self.dropped,
                "padded": self.padded, "pending": self._frames.qsize(),
                "pbo": self.use_pbo}
