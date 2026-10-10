from OpenGL.GL import *
from OpenGL.GLU import *
from OpenGL.GLUT import *
from pyglgame.tools.xyaHelper import *
from ctypes import sizeof, c_float, c_void_p
from .BufferBuilder import *
from ..RenderGlobal import RenderGlobal
from ..tools.glDebug import currentContext, isSameContext
from . import RenderState

global WINDOWN_RENDER_BUFFER
WINDOWN_RENDER_BUFFER: "RenderBuffer" = None


class RenderBuffer:
    """VAO/VBO 包装。

    - 顶点数据用 numpy 直接上传（不再 ``(c_float*n)(*list)`` 展开）
    - 记录最后一次上传的顶点数 ``vertex_count``，CPU 数据释放后仍可绘制
    - 只有 :class:`BufferBuilder` 被改动过（``dirty``）时才重新上传
    """

    def __init__(self, usage: int = GL_DYNAMIC_DRAW,
                 blend_mode: str = None, depth_write: bool = None) -> None:
        self.vbo = -1
        self.vao = -1
        self.usage = usage
        self.buffer_builder = None
        self.is_build = False
        self.vertex_count = 0
        self._attrib_signature = None
        self._context = currentContext()
        self._released = False
        # 批次级渲染状态：None = 跟随当前 pass（保持旧行为）。
        # 见 pyglgame.render.RenderState（混合模式 / 深度写入）
        self.blend_mode = blend_mode
        self.depth_write = depth_write

    def setState(self, blend_mode: str = None, depth_write: bool = None):
        """配置这一批三角形的混合模式/深度写入（None 表示不变）。"""
        self.blend_mode = blend_mode
        self.depth_write = depth_write
        return self

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass

    def release(self) -> None:
        """显式释放 GPU 资源（需要在创建它的 GL 线程/上下文里调用）。"""
        if self._released:
            return
        self._released = True
        if self.vbo == -1 and self.vao == -1:
            return
        if not isSameContext(self._context):
            # 不在创建时的上下文里：直接删除是危险的，交给上下文销毁时回收
            self.vbo = -1
            self.vao = -1
            return
        if self.vbo != -1:
            glDeleteBuffers(1, [self.vbo])
            self.vbo = -1
        if self.vao != -1:
            glDeleteVertexArrays(1, [self.vao])
            self.vao = -1

    def getBuffer(self,):
        return self.buffer_builder

    def createBuffer(self, pri_type: int, format_type: int):
        self.buffer_builder = BufferBuilder(pri_type, format_type)
        return self.buffer_builder

    def build(self, re_build: bool = True) -> bool:
        """把 CPU 顶点数据上传到 VBO。返回是否真的执行了上传。"""
        builder = self.buffer_builder
        if builder is None:
            raise RuntimeError("RenderBuffer.build() 之前没有调用 createBuffer()")

        created = False
        if self.vbo == -1:
            self.vbo = glGenBuffers(1)
            created = True
        if self.vao == -1:
            self.vao = glGenVertexArrays(1)
            created = True
        if not created:
            if not re_build:
                return False
            if not builder.dirty and self.vertex_count == builder.size:
                # 数据没有变化，跳过整段上传（旧实现每帧都会重传）
                return False

        data = builder.vertex_data
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
        glBufferData(GL_ARRAY_BUFFER, data.nbytes, data, self.usage)

        signature = (builder.format_type, builder.row_size)
        if signature != self._attrib_signature:
            glBindVertexArray(self.vao)
            glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
            self.configureVertexAttributes()
            glBindVertexArray(0)
            self._attrib_signature = signature

        glBindBuffer(GL_ARRAY_BUFFER, 0)
        self.vertex_count = builder.size
        builder.markClean()
        self.is_build = True
        return True

    def draw(self, re_build: bool = True) -> None:
        self.build(re_build)
        render_global = RenderGlobal.instance
        if render_global is not None and getattr(render_global, "id_pass", False):
            # Object ID pass：强制"不混合 + 写深度"。ID 是整数语义，被 alpha 混合
            # 一掺就变了；写深度则保证重叠物体只留下最前面的那个
            # （见 render/ObjectIdBuffer.py 与 ObjectIdRegistry.py）
            RenderState.override(RenderState.BLEND_NONE, depth_write=True)
        elif self.blend_mode is not None or self.depth_write is not None:
            RenderState.override(self.blend_mode, self.depth_write)
        shader = render_global.using_shader if render_global is not None else None
        if shader is not None:
            shader.uniform1iCached("formatType", self.buffer_builder.format_type)
        # VAO 里已记录 VBO 绑定与属性指针，不必重复 glBindBuffer
        glBindVertexArray(self.vao)
        glDrawArrays(self.buffer_builder.pri_type, 0, self.vertex_count)

    def configureVertexAttributes(self) -> None:
        f_size = sizeof(c_float)
        stride = f_size * self.buffer_builder.row_size
        offset = 0
        format_type = self.buffer_builder.format_type
        # 先全部禁用，避免残留状态
        for i in range(6):
            glDisableVertexAttribArray(i)

        offset = 0
        if format_type & POS:
            glEnableVertexAttribArray(0)
            glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE,
                                  stride, c_void_p(offset))
            offset += sizeof(c_float) * 3
        if format_type & COL:
            glEnableVertexAttribArray(1)
            glVertexAttribPointer(1, 4, GL_FLOAT, GL_FALSE,
                                  stride, c_void_p(offset))
            offset += sizeof(c_float) * 4
        if format_type & TEX:
            glEnableVertexAttribArray(2)
            glVertexAttribPointer(2, 2, GL_FLOAT, GL_FALSE,
                                  stride, c_void_p(offset))
            offset += sizeof(c_float) * 2
        if format_type & NOR:
            glEnableVertexAttribArray(3)
            glVertexAttribPointer(3, 3, GL_FLOAT, GL_FALSE,
                                  stride, c_void_p(offset))
            offset += sizeof(c_float) * 3
        if format_type & LIT:
            glEnableVertexAttribArray(4)
            glVertexAttribPointer(4, 2, GL_FLOAT, GL_FALSE,
                                  stride, c_void_p(offset))
            offset += sizeof(c_float) * 2
        if format_type & SIZ:
            glEnableVertexAttribArray(5)
            glVertexAttribPointer(5, 1, GL_FLOAT, GL_FALSE,
                                  stride, c_void_p(offset))
            offset += sizeof(c_float) * 1

    @staticmethod
    def getWindowRenderBuffer():
        """全屏矩形（POS|TEX），所有相机共用一份。"""
        global WINDOWN_RENDER_BUFFER
        if WINDOWN_RENDER_BUFFER is not None:
            return WINDOWN_RENDER_BUFFER
        WINDOWN_RENDER_BUFFER = RenderBuffer(GL_STATIC_DRAW)
        buf_builder = WINDOWN_RENDER_BUFFER.createBuffer(
            GL_TRIANGLES, POS | TEX)
        buf_builder.pos(-1, -1, 0).tex(0, 0).end()  # bottom left
        buf_builder.pos(+1, -1, 0).tex(1, 0).end()   # bottom right
        buf_builder.pos(-1, +1, 0).tex(0, 1).end()   # top left
        buf_builder.pos(+1, -1, 0).tex(1, 0).end()   # bottom right
        buf_builder.pos(+1, +1, 0).tex(1, 1).end()    # top right
        buf_builder.pos(-1, +1, 0).tex(0, 1).end()   # top left
        return WINDOWN_RENDER_BUFFER

    # 兼容旧拼写
    getWindownRenderBuffer = getWindowRenderBuffer
