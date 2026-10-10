import ctypes
from OpenGL.GL import *
from ..tools.glDebug import currentContext, isSameContext

class TextureBase:
    """纹理基类。

    ``__del__`` 里原先调用的是 ``glDeleteTextures(self.id)``（少传 count 参数），
    而且在 GC/解释器退出时可能没有当前 GL 上下文。现在：
    - 提供显式 :meth:`release`（推荐在上下文有效时调用）
    - ``__del__`` 仅在“同一上下文”里安全释放，否则交给上下文销毁回收
    """
    w: int
    h: int

    def __init__(self, id: int) -> None:
        self.id = int(id)
        self._context = currentContext()
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        """释放 GPU 纹理对象。"""
        if self._released or self.id == 0:
            return
        self._released = True
        if not isSameContext(self._context):
            self.id = 0
            return
        glDeleteTextures(1, [self.id])
        self.id = 0

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass

    def bind(self, unit) -> None:
        raise NotImplementedError("Method bind not implemented")
    
    def getData(self, level=0, fmt=GL_RGBA, dtype=GL_UNSIGNED_BYTE, ref_data=None):
        if dtype == GL_UNSIGNED_BYTE:
            c_type = ctypes.c_ubyte
        elif dtype == GL_FLOAT:
            c_type = ctypes.c_float
        else:
            raise ValueError(f"Unsupported dtype {dtype}")
        if fmt in (GL_RGBA, GL_RGBA8, GL_RGBA32F):
            channels = 4
        elif fmt in (GL_RGB, GL_RGB8, GL_RGB32F):
            channels = 3
        elif fmt in (GL_RED, GL_R32F):
            channels = 1
        else:
            raise ValueError(f"Unsupported format {fmt}")
        if ref_data is None:
            count = self.w * self.h * channels
            ref_data = (c_type * count)()
        buf_size = ctypes.sizeof(ref_data)
        glBindTexture(GL_TEXTURE_2D, self.id)
        glGetTextureImage(self.id, level, fmt, dtype, buf_size, ref_data)
        glBindTexture(GL_TEXTURE_2D, 0)
        return ref_data