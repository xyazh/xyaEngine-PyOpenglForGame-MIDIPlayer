"""OpenGL 错误检查工具。

默认关闭（每次都调用 glGetError 会打断驱动的批处理）。调试时：

    from pyglgame.tools.glDebug import GLDebug
    GLDebug.enable()
"""

import ctypes
import sys
import traceback

from OpenGL.GL import *

_ERROR_NAMES = {
    GL_INVALID_ENUM: "GL_INVALID_ENUM",
    GL_INVALID_VALUE: "GL_INVALID_VALUE",
    GL_INVALID_OPERATION: "GL_INVALID_OPERATION",
    GL_STACK_OVERFLOW: "GL_STACK_OVERFLOW",
    GL_STACK_UNDERFLOW: "GL_STACK_UNDERFLOW",
    GL_OUT_OF_MEMORY: "GL_OUT_OF_MEMORY",
    GL_INVALID_FRAMEBUFFER_OPERATION: "GL_INVALID_FRAMEBUFFER_OPERATION",
}


class GLDebug:
    enabled: bool = False
    raise_on_error: bool = False
    _context_callback_registered = False

    @staticmethod
    def enable(raise_on_error: bool = False) -> None:
        """打开错误检查（同时尝试注册 KHR_debug 回调）。"""
        GLDebug.enabled = True
        GLDebug.raise_on_error = raise_on_error
        GLDebug.registerDebugCallback()

    @staticmethod
    def disable() -> None:
        GLDebug.enabled = False

    @staticmethod
    def registerDebugCallback() -> None:
        """尝试注册 KHR_debug 回调（失败则静默忽略，退回 glGetError 检查）。"""
        if GLDebug._context_callback_registered:
            return
        try:
            glEnable(GL_DEBUG_OUTPUT)
            glDebugMessageCallback(GLDebug._on_debug_message, None)
            glEnable(GL_DEBUG_OUTPUT_SYNCHRONOUS)
            GLDebug._context_callback_registered = True
        except Exception:
            pass

    @staticmethod
    def _on_debug_message(source, type_, id_, severity, length, message, userParam) -> None:
        text = message.decode("utf-8", "replace") if isinstance(message, bytes) else str(message)
        sys.stderr.write(f"[GLDebug] {text}\n")

    @staticmethod
    def getError() -> int:
        """返回第一个错误码（并清空错误队列）。"""
        return glGetError()

    @staticmethod
    def check(tag: str = "") -> bool:
        """检查 GL 错误；有问题时打印/抛出。返回是否有错误。"""
        if not GLDebug.enabled:
            return False
        errors = []
        while True:
            err = glGetError()
            if err == GL_NO_ERROR:
                break
            errors.append(err)
        if not errors:
            return False
        names = ", ".join(_ERROR_NAMES.get(e, f"0x{e:x}") for e in errors)
        msg = f"[GLDebug] GL 错误 {names}" + (f" @ {tag}" if tag else "")
        if GLDebug.raise_on_error:
            raise RuntimeError(msg)
        sys.stderr.write(msg + "\n")
        traceback.print_stack(limit=6)
        return True

    @staticmethod
    def checkFramebuffer(tag: str = "") -> int:
        """检查当前绑定的帧缓冲是否完整，不完整时返回 GL 枚举值。"""
        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        if status != GL_FRAMEBUFFER_COMPLETE:
            names = {
                GL_FRAMEBUFFER_UNDEFINED: "UNDEFINED",
                GL_FRAMEBUFFER_INCOMPLETE_ATTACHMENT: "INCOMPLETE_ATTACHMENT",
                GL_FRAMEBUFFER_INCOMPLETE_MISSING_ATTACHMENT: "INCOMPLETE_MISSING_ATTACHMENT",
                GL_FRAMEBUFFER_INCOMPLETE_DRAW_BUFFER: "INCOMPLETE_DRAW_BUFFER",
                GL_FRAMEBUFFER_INCOMPLETE_READ_BUFFER: "INCOMPLETE_READ_BUFFER",
                GL_FRAMEBUFFER_UNSUPPORTED: "UNSUPPORTED",
                GL_FRAMEBUFFER_INCOMPLETE_MULTISAMPLE: "INCOMPLETE_MULTISAMPLE",
                GL_FRAMEBUFFER_INCOMPLETE_LAYER_TARGETS: "INCOMPLETE_LAYER_TARGETS",
            }
            msg = f"[GLDebug] 帧缓冲不完整: {names.get(status, hex(status))} @ {tag}"
            if GLDebug.raise_on_error:
                raise RuntimeError(msg)
            sys.stderr.write(msg + "\n")
        return status


def currentContext():
    """返回当前 GL 上下文的标识（没有可用上下文时返回 ``None``）。

    PyOpenGL 并没有 ``glGetCurrentContext``（它是平台函数），
    这里统一走 ``OpenGL.platform``。
    """
    try:
        from OpenGL.platform import GetCurrentContext, CurrentContextIsValid
    except Exception:
        return None
    try:
        if not CurrentContextIsValid():
            return None
        return GetCurrentContext()
    except Exception:
        return None


def isSameContext(context) -> bool:
    """判断能否在当前上下文里安全释放“创建于 ``context``”的 GL 对象。

    - 当前没有上下文 → 不能调用任何 GL 函数（返回 False）
    - 创建时没能记录上下文 → 尽力按同一上下文处理（返回 True）
    """
    current = currentContext()
    if current is None:
        return False
    if context is None:
        return True
    return context == current


def maxSamples() -> int:
    """当前 GPU 支持的最大采样数（用于 MSAA 夹取）。"""
    try:
        return int(glGetIntegerv(GL_MAX_SAMPLES))
    except Exception:
        return 1


def maxColorTextureSamples() -> int:
    """颜色纹理（非 renderbuffer）支持的最大采样数。"""
    try:
        return int(glGetIntegerv(GL_MAX_COLOR_TEXTURE_SAMPLES))
    except Exception:
        return maxSamples()


def clampSamples(samples: int, allow_renderbuffer: bool = False) -> int:
    """把请求的采样数夹到当前设备支持的范围里。"""
    samples = int(samples)
    if samples <= 1:
        return samples
    limit = maxSamples() if allow_renderbuffer else min(maxSamples(), maxColorTextureSamples())
    limit = max(1, limit)
    return min(samples, limit)
