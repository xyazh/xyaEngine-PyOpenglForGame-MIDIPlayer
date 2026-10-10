"""GPU 对象句柄：传已有 Buffer / VBO / Texture 时**不复制、不留新对象**。

- :class:`GlBuffer`：GPU 上已经存在的缓冲区（VBO / SSBO / PBO 都行）
- :class:`GlImage`：GPU 上已经存在的纹理，当 ``image2D``/``image3D`` 用

kernel 参数也可以直接收：

| 你传的东西 | 怎么认出来 |
| --- | --- |
| ``GlBuffer`` / ``GlImage`` | 直接就是 |
| ``int`` | 当成 GL 缓冲区 / 纹理名字（纹理默认 2D） |
| 引擎 ``RenderBuffer`` | 用它的 ``.vbo`` |
| PyOpenGL ``VBO`` | 用它的 ``.buffers[0]`` |
| 引擎 ``Texture`` / ``TextureStorage2D`` | 用它的 ``.id`` / ``.w`` / ``.h`` |
| ``list`` / ``tuple`` / ``ndarray`` | **不是** GPU 对象 -> 走 CPU 上传（见 GlCompute） |
"""

import ctypes

import numpy as np
from OpenGL.GL import (GL_CLAMP_TO_EDGE, GL_NEAREST, GL_READ_WRITE,
                       GL_TEXTURE_2D, GL_TEXTURE_3D, GL_TEXTURE_DEPTH,
                       GL_TEXTURE_HEIGHT, GL_TEXTURE_INTERNAL_FORMAT,
                       GL_TEXTURE_MAG_FILTER, GL_TEXTURE_MIN_FILTER,
                       GL_TEXTURE_WIDTH, GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
                       glBindTexture, glDeleteTextures, glGenTextures,
                       glGetTexImage, glGetTexLevelParameteriv, glTexImage2D,
                       glTexImage3D, glTexParameteri)

from .GlRuntime import (GLSL_BY_GL_FORMAT, IMAGE_FORMATS, bufferBytes,
                        currentBinding, deleteBuffers, downloadBuffer)

__all__ = ["GlBuffer", "GlImage", "detectBuffer", "detectImage",
           "imageGlslFormat", "makeImage", "readImage"]


class GlBuffer(object):
    """GPU 上已经存在的缓冲区（**不复制数据**，kernel 直接在它上面算）。"""

    __slots__ = ("name", "dtype", "length", "owned", "shape")

    def __init__(self, name, dtype=None, length=None, owned=False, shape=None) -> None:
        self.name = int(name)
        self.dtype = np.dtype(dtype) if dtype is not None else None
        self.length = None if length is None else int(length)
        self.owned = bool(owned)        # True = 这个缓冲区是我们建的（可以删）
        self.shape = tuple(int(v) for v in shape) if shape else None

    def to_numpy(self):
        """读回 CPU（同步等 GPU）。``length`` 没给就按 dtype 元素个数算。"""
        if self.dtype is None:
            raise ValueError("GlBuffer 没有 dtype，不能 to_numpy()；"
                             "请用 GlBuffer(name, dtype=..., length=...) 建一个")
        count = self.length
        if count is None:
            count = bufferBytes(self.name) // self.dtype.itemsize
        out = np.empty(count, dtype=self.dtype)
        downloadBuffer(self.name, out)
        return out

    def release(self) -> None:
        """只删自己建的缓冲区（用户传进来的绝不动）。"""
        if self.owned and self.name:
            deleteBuffers([self.name])
            self.name = 0
            self.owned = False

    def __repr__(self):
        return "<GlBuffer name=%d length=%s%s>" % (
            self.name, self.length, " owned" if self.owned else "")


class GlImage(object):
    """GPU 上已经存在的纹理（**不复制**，kernel 用 imageLoad/imageStore 原地改）。"""

    __slots__ = ("name", "target", "dim", "format", "access", "size", "owned")

    def __init__(self, name, target=GL_TEXTURE_2D, glsl_format=None, access=GL_READ_WRITE,
                 size=None, owned=False) -> None:
        self.name = int(name)
        self.target = int(target)
        self.dim = 3 if self.target == GL_TEXTURE_3D else 2
        self.format = glsl_format          # GLSL image 格式：r32f / rgba32f / …
        self.access = int(access)
        self.size = tuple(int(v) for v in size) if size else None
        self.owned = bool(owned)

    def sizeOf(self):
        """(w, h, d)；没显式给就查纹理。"""
        if self.size is None:
            out = (ctypes.c_int * 3)()
            previous = currentBinding(self.target)      # 别顶掉 engine 的 tex0
            glBindTexture(self.target, self.name)
            glGetTexLevelParameteriv(self.target, 0, GL_TEXTURE_WIDTH, out)
            glGetTexLevelParameteriv(self.target, 0, GL_TEXTURE_HEIGHT,
                                     ctypes.byref(out, 4))
            if self.target == GL_TEXTURE_3D:
                glGetTexLevelParameteriv(self.target, 0, GL_TEXTURE_DEPTH,
                                         ctypes.byref(out, 8))
            glBindTexture(self.target, previous)
            self.size = (int(out[0]), int(out[1]) or 1, int(out[2]) or 1)
        return self.size

    def glslFormat(self):
        """GLSL 里的 image 格式：显式给的优先，否则按纹理内部格式映射。"""
        if self.format is None:
            self.format = imageGlslFormat(self.target, self.name)
        return self.format

    def release(self) -> None:
        if self.owned and self.name:
            glDeleteTextures(1, (ctypes.c_uint * 1)(int(self.name)))
            self.name = 0
            self.owned = False

    def __repr__(self):
        return "<GlImage name=%d %s format=%s>" % (
            self.name, "3D" if self.dim == 3 else "2D", self.format)


# --------------------------------------------------------------- 识别已有对象


def detectBuffer(value):
    """是不是"GPU 上已有的缓冲区"？是 -> :class:`GlBuffer`，不是 -> ``None``。"""
    if isinstance(value, GlBuffer):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return GlBuffer(value) if value > 0 else None
    name = getattr(value, "vbo", None)                   # 引擎 render.RenderBuffer
    if isinstance(name, int) and name > 0:
        return GlBuffer(name)
    names = getattr(value, "buffers", None)              # PyOpenGL VBO
    if isinstance(names, (list, tuple)) and names and names[0]:
        return GlBuffer(int(names[0]))
    return None


def detectImage(value):
    """是不是"GPU 上已有的纹理"？是 -> :class:`GlImage`，不是 -> ``None``。"""
    if isinstance(value, GlImage):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return GlImage(value) if value > 0 else None
    name = getattr(value, "id", None)                    # 引擎 TextureBase 子类
    if isinstance(name, int) and name > 0:
        width = getattr(value, "w", None)
        height = getattr(value, "h", None)
        depth = getattr(value, "d", None)
        size = (int(width), int(height), int(depth) if depth else 1) \
            if (width and height) else None
        target = GL_TEXTURE_3D if depth else GL_TEXTURE_2D
        return GlImage(name, target=target, size=size)
    return None


# ------------------------------------------------------------------ image 工具


def imageGlslFormat(target, name):
    """查纹理的内部格式并映射成 GLSL 的 image 格式。"""
    out = (ctypes.c_int * 1)()
    previous = currentBinding(target)                   # 用完还原，别顶掉 engine 的 tex0
    glBindTexture(target, int(name))
    glGetTexLevelParameteriv(target, 0, GL_TEXTURE_INTERNAL_FORMAT, out)
    glBindTexture(target, previous)
    fmt = GLSL_BY_GL_FORMAT.get(int(out[0]))
    if fmt is None:
        raise ValueError("认不出纹理 %d 的内部格式 0x%X：请显式给格式，例如 "
                         "GlImage(%d, glsl_format='rgba32f')"
                         % (name, int(out[0]), name))
    return fmt


def makeImage(glsl_format, width, height=1, depth=1, data=None):
    """建一张临时纹理（给"CPU 数组 + ``Image[...]``"用）。"""
    internal, pixel_format, pixel_type = IMAGE_FORMATS[glsl_format]
    name = int(glGenTextures(1))
    target = GL_TEXTURE_3D if int(depth) > 1 else GL_TEXTURE_2D
    previous = currentBinding(target)                   # 建完还回去，别顶掉 engine 的 tex0
    glBindTexture(target, name)
    glTexParameteri(target, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(target, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    glTexParameteri(target, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(target, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    pointer = data.ctypes.data_as(ctypes.c_void_p) if data is not None else None
    if target == GL_TEXTURE_3D:
        glTexImage3D(target, 0, internal, int(width), int(height), int(depth), 0,
                     pixel_format, pixel_type, pointer)
    else:
        glTexImage2D(target, 0, internal, int(width), int(height), 0,
                     pixel_format, pixel_type, pointer)
    glBindTexture(target, previous)
    return GlImage(name, target=target, glsl_format=glsl_format,
                   size=(width, height, depth), owned=True)


def readImage(image):
    """把 image 内容读回 numpy，形状 ``(h, w, comps)``（3D 是 ``(d, h, w, comps)``）。"""
    fmt = image.glslFormat()
    internal, pixel_format, pixel_type = IMAGE_FORMATS[fmt]
    width, height, depth = image.sizeOf()
    channels = 1 if fmt.startswith("r32") else (2 if fmt.startswith("rg3") else 4)
    if fmt.endswith("f"):
        dtype = np.float32
    elif fmt.endswith("ui"):
        dtype = np.uint32
    else:
        dtype = np.int32
    shape = ((height, width, channels) if int(depth) <= 1
             else (depth, height, width, channels))
    out = np.empty(shape, dtype=dtype)
    previous = currentBinding(image.target)             # 读完还回去，别顶掉 engine 的 tex0
    glBindTexture(image.target, image.name)
    glGetTexImage(image.target, 0, pixel_format, pixel_type,
                  out.ctypes.data_as(ctypes.c_void_p))
    glBindTexture(image.target, previous)
    return out

