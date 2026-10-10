"""Object ID / Mask 渲染目标 + 屏幕坐标换算（GPU Picking）。

- **颜色附件**：``GL_R32F``（单通道 float）—— 每个像素写"当前摄像机看到的、
  最前面物体的唯一 ID"，``0`` = 背景。float32 能精确表示 ``< 2^24`` 的整数，
  正好覆盖 :data:`~pyglgame.render.ObjectIdRegistry.MAX_OBJECT_ID`。
- **深度附件**：``GL_DEPTH_COMPONENT24`` —— ID pass 开着深度测试，所以重叠物体
  只会留下最前面的那个 ID（需求 3）。
- **读取**：只读鼠标那一个像素（``glReadPixels`` 1×1），绝不整帧回读（需求 7）。

为什么用浮点 ID 而不是 ``GL_R32UI`` 整数纹理：

    本项目的渲染管线是"单一 uber shader（dis）+ 单个 ``vec4`` 输出"。整数输出
    要求独立的 program、``uint`` 输出与 ``sampler2D`` 无法复用；而浮点 ID 可以
    直接复用 dis 的顶点/片元分支（``fuc == OBJECT_ID``，见 ``render/Passes.py``）
    和相机的矩阵上传，改动最小 —— 这也是需求 8 里"否则选择当前项目最合适的
    实现"的取舍。ID 精度由 :data:`ObjectIdRegistry.MAX_OBJECT_ID` 保证。
"""

from OpenGL.GL import *
import numpy as np

from ..RenderGlobal import RenderGlobal
from ..tools.glDebug import currentContext, isSameContext

#: ID / Mask 纹理占用的纹理单元：0..5 = dis.frag 的 tex0..tex5，
#: 6 = 方向光阴影贴图，7 = SSAO，8 = 2D 阴影遮罩，所以 ID 从 9 开始
OBJECT_ID_TEXTURE_UNIT = 9

#: 背景（没有物体）的 ID
BACKGROUND_ID = 0


def screenToBufferPixel(mouse_x: float, mouse_y: float,
                        window_width: int, window_height: int,
                        buffer_width: int, buffer_height: int) -> tuple:
    """窗口鼠标坐标 → framebuffer 像素坐标（纯函数，方便单测）。

    - 输入原点在**左上角**、``y`` 向下（GLUT 的鼠标回调约定）；
    - 输出原点在**左下角**、``y`` 向上（OpenGL 的 framebuffer 约定）；
    - 同时按 ``buffer / window`` 的比例缩放，所以窗口 Resize 后仍然对得上
      （ID 缓冲尺寸跟随窗口，但这里仍显式换算一次，避免两者尺寸不同的场合出错）；
    - 结果夹到 ``[0, w-1] × [0, h-1]``，鼠标移出窗口也不会越界。

    返回 ``(x, y)``。
    """
    window_width = int(window_width)
    window_height = int(window_height)
    buffer_width = int(buffer_width)
    buffer_height = int(buffer_height)
    if window_width <= 0 or window_height <= 0:
        return (0, 0)
    if buffer_width <= 0 or buffer_height <= 0:
        return (0, 0)
    px = int(float(mouse_x) / float(window_width) * float(buffer_width))
    # Y 翻转：鼠标在顶部（y=0）→ 缓冲区最后一行
    py = int((1.0 - float(mouse_y) / float(window_height)) * float(buffer_height))
    if px < 0:
        px = 0
    elif px >= buffer_width:
        px = buffer_width - 1
    if py < 0:
        py = 0
    elif py >= buffer_height:
        py = buffer_height - 1
    return (px, py)


class ObjectIdBuffer:
    """记录"每个屏幕像素对应哪个 GameObject"的离屏帧缓冲（见模块文档）。"""

    def __init__(self, width: int, height: int) -> None:
        self.width = int(width)
        self.height = int(height)
        self.render_global = RenderGlobal.instance
        self._context = currentContext()
        self.is_dead = False
        self.framebuffer = 0
        self.texture_id = 0
        self.depth_texture_id = 0
        self._create()

    # === 创建 / 释放 ===

    def _create(self) -> None:
        self.framebuffer = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)

        # 颜色附件：单通道 float ID
        self.texture_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, self.width, self.height,
                     0, GL_RED, GL_FLOAT, None)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, self.texture_id, 0)

        # 深度附件：ID pass 的深度测试用它，重叠物体只留最前面的 ID
        self.depth_texture_id = glGenRenderbuffers(1)
        glBindRenderbuffer(GL_RENDERBUFFER, self.depth_texture_id)
        glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24,
                              self.width, self.height)
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                                  GL_RENDERBUFFER, self.depth_texture_id)

        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        glBindRenderbuffer(GL_RENDERBUFFER, 0)
        glBindTexture(GL_TEXTURE_2D, 0)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(
                f"ObjectIdBuffer 帧缓冲不完整 (status=0x{status:x}, "
                f"{self.width}x{self.height})")

    def __del__(self) -> None:
        try:
            self.cleanup()
        except Exception:
            pass

    def cleanup(self) -> None:
        if self.is_dead:
            return
        self.is_dead = True
        if not isSameContext(self._context):
            # 不在创建它的上下文里（GC / 别的线程）：交给上下文销毁时回收
            self.framebuffer = self.texture_id = self.depth_texture_id = 0
            return
        if self.framebuffer:
            glDeleteFramebuffers(1, [self.framebuffer])
            self.framebuffer = 0
        if self.texture_id:
            glDeleteTextures(1, [self.texture_id])
            self.texture_id = 0
        if self.depth_texture_id:
            glDeleteRenderbuffers(1, [self.depth_texture_id])
            self.depth_texture_id = 0

    def resize(self, width: int, height: int) -> None:
        """窗口尺寸变化时重建（纹理与深度附件都要跟着变）。"""
        width, height = int(width), int(height)
        if width == self.width and height == self.height:
            return
        context = self._context
        self.cleanup()
        self.is_dead = False
        self.width, self.height = width, height
        self._context = context
        self._create()

    # === 渲染 ===

    def bind(self) -> None:
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        glViewport(0, 0, self.width, self.height)

    def clear(self) -> None:
        """清成"背景"：颜色全 0（ID=0）、深度全 1。"""
        glDepthMask(GL_TRUE)
        glClearBufferfv(GL_COLOR, 0, [0.0, 0.0, 0.0, 0.0])
        glClear(GL_DEPTH_BUFFER_BIT)

    def unbind(self) -> None:
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        size = getattr(getattr(self.render_global, "window", None), "size", None)
        if size is not None:
            glViewport(0, 0, int(size.w), int(size.h))

    # === 读取（只读 1×1 像素）===

    def readId(self, x: int, y: int) -> int:
        """读取 framebuffer 坐标 ``(x, y)``（左下原点）处的物体 ID。"""
        if self.is_dead or not self.framebuffer:
            return BACKGROUND_ID
        pixel = np.zeros(1, dtype=np.float32)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        glReadBuffer(GL_COLOR_ATTACHMENT0)
        glPixelStorei(GL_PACK_ALIGNMENT, 1)
        glReadPixels(int(x), int(y), 1, 1, GL_RED, GL_FLOAT, pixel)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        value = int(round(float(pixel[0])))
        return value if value > 0 else BACKGROUND_ID

    def readIdAtScreen(self, mouse_x: float, mouse_y: float,
                       window_size) -> int:
        """把"窗口鼠标坐标"换算成 framebuffer 像素后读取 ID（背景 = 0）。"""
        px, py = screenToBufferPixel(
            mouse_x, mouse_y,
            getattr(window_size, "w", 0), getattr(window_size, "h", 0),
            self.width, self.height)
        return self.readId(px, py)

    # === 给后处理 / 调试用 ===

    def texture(self) -> int:
        """可采样的 ID 纹理 id（后处理可以按它做马赛克 / 描边 / 高亮）。"""
        return self.texture_id
