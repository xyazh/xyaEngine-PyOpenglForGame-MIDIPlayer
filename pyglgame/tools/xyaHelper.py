import time
import base64
import re
import threading
import warnings
from typing import Any, Callable

from PIL import Image
from io import BytesIO
from OpenGL.GLUT import glutGet, GLUT_ELAPSED_TIME


def xyaTimerFunc(t: int, fuc: Callable) -> None:
    """（已废弃）后台线程按固定间隔调用 ``fuc(dt, tps)``。

    独立线程会和渲染线程（GL 线程）并发读写同一批对象，对象增删时还会抛
    ``RuntimeError: Set changed size during iteration``；引擎已改用
    :class:`pyglgame.GameMainLoop` 的单线程固定步长累加器，仅兼容老代码时可用。
    """
    warnings.warn(
        "xyaTimerFunc 已废弃：请使用 GameMainLoop 的固定步长循环（单线程实现）。",
        DeprecationWarning,
        stacklevel=2,
    )
    if not hasattr(xyaTimerFunc, 'frist') or xyaTimerFunc.frist:
        step = t / 1000.0
        xyaTimerFunc.frist = False

        def wrapper() -> None:
            last = time.perf_counter()
            while True:
                now = time.perf_counter()
                dt = now - last
                last = now
                tps = 1.0 / dt if dt > 0 else 0.0
                fuc(dt, tps)
                sleep_time = step - (time.perf_counter() - now)
                if sleep_time > 0:
                    time.sleep(sleep_time)
        threading.Thread(target=wrapper, daemon=True).start()


class FpsStats:
    """帧率统计：避免在渲染循环里每帧 print（会严重拖慢 IO）。"""

    def __init__(self, interval: float = 1.0, enable_print: bool = True) -> None:
        self.interval = interval
        self.enable_print = enable_print
        self.frames = 0
        self.elapsed = 0.0
        self.last_fps = 0.0
        self.worst_frame_ms = 0.0

    def tick(self, fps: float = 0.0, frame_dt: float = 0.0) -> float:
        """记录一帧，返回最近一次统计出的平均帧率（未到间隔时返回 0）。"""
        self.frames += 1
        self.elapsed += frame_dt
        self.worst_frame_ms = max(self.worst_frame_ms, frame_dt * 1000.0)
        if self.elapsed < self.interval:
            return 0.0
        avg = self.frames / self.elapsed if self.elapsed > 0 else 0.0
        self.last_fps = avg
        if self.enable_print:
            print(f"[FPS] {avg:7.2f}  (worst frame {self.worst_frame_ms:6.2f} ms)")
        self.frames = 0
        self.elapsed = 0.0
        self.worst_frame_ms = 0.0
        return avg


def run_time(fuc: Callable) -> Callable:
    """装饰器：调用后打印耗时（秒），返回值原样透传。"""

    def r_fuc(*args: Any, **kw: Any) -> Any:
        t = time.time()
        r = fuc(*args, **kw)
        print(time.time()-t)
        return r
    return r_fuc


def get_run_time(fuc: Callable) -> Callable:
    """装饰器：返回 ``(耗时秒, 原返回值)`` 二元组。"""

    def r_fuc(*args: Any, **kw: Any) -> tuple[float, Any]:
        t = time.time()
        r = fuc(*args, **kw)
        return time.time()-t, r
    return r_fuc


def timeit(func: Callable) -> Callable:
    """装饰器：打印 ``函数名 + 耗时``，返回值原样透传。"""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        print(f"{func.__name__} took {end - start} seconds to execute.")
        return result
    return wrapper


def base64_to_image(base64_data: str) -> Image.Image:
    """``data:image/...;base64,xxx``（或裸 base64）→ PIL 图像。"""
    base64_data = re.sub('^data:image/.+;base64,', '', base64_data)
    byte_data = base64.b64decode(base64_data)
    image_data = BytesIO(byte_data)
    return Image.open(image_data)

