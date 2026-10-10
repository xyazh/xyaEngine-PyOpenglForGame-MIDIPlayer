from typing import Any, Callable

from OpenGL.GL import *
from ..RenderGlobal import RenderGlobal, backgroundColor
from .RenderBuffer import *
from .FrameBuffer import FrameBuffer


class FrameBufferFake:
    """直接渲染到屏幕（不做后处理时的实验路径，Camera 目前未使用）。

    .. note::
        原实现引用了不存在的 ``dis_shader_1``（会 AttributeError），已修正为
        ``dis_shader``。
    """

    def __init__(self, width: int, height: int, use_depth: bool = False, param: int = GL_NEAREST) -> None:
        self.width = width
        self.height = height
        self.size = RenderGlobal.instance.window.size
        self.window_shader = RenderGlobal.instance.dis_shader
        self.use_depth = use_depth
        self.param = param


    def __del__(self) -> None:
        self.cleanup()

    def bindTexture(self) -> None:
        """绑定纹理对象"""
        pass

    def bindDepthTexture(self) -> None:
        """绑定深度纹理对象"""
        pass

    def bind(self) -> None:
        """绑定帧缓冲对象"""
        glBindFramebuffer(GL_FRAMEBUFFER, 0)

    def unbind(self) -> None:
        """解绑帧缓冲对象"""
        glBindFramebuffer(GL_FRAMEBUFFER, 0)

    def drawStart(self) -> None:
        """将当前帧缓冲对象绑定为渲染目标，并绘制到纹理"""
        self.bind()
        glClearColor(*backgroundColor())
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT)
        if self.use_depth:
            glEnable(GL_DEPTH_TEST)  # 启用深度测试
            glEnable(GL_STENCIL_TEST)  # 启用模板测试

    def draw(self, fuc: Callable, *args: Any, **kw: Any) -> None:
        """绑定屏幕为渲染目标 → 跑一遍 ``fuc(*args, **kw)`` → 解绑。"""
        self.drawStart()
        fuc(*args, **kw)
        self.drawEnd()

    def drawEnd(self) -> None:
        """将当前帧缓冲对象解绑"""
        self.unbind()


    def cleanup(self) -> None:
        """清理帧缓冲对象及其资源"""
        pass

    def drawToWindow(self) -> None:
        """将当前帧缓冲对象绘制到窗口"""
        pass
