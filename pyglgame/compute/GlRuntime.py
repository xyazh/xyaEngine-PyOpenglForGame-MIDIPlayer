"""低层 OpenGL 运行时：编译 program / SSBO / image / 派发 / barrier / 读回。

和 AST 编译器（``translator.py``）**完全分离**：那边只产"源码 + 绑位元信息"
（纯 Python，不 import OpenGL），这边只认"源码 + 绑位"，负责所有 GL 调用。

两个必须记住的工程细节：

1. **指针一律显式传 ``c_void_p``**：这份 PyOpenGL 自己那套"numpy 数组自动转指针"
   在**输入**方向是对的、**输出**方向会把数据写到别处（实测直接 heap corruption）。
2. **删除缓冲区用 ctypes 数组**，不依赖 Python list -> 数组的隐式转换。
"""

import ctypes
import re

import numpy as np
from OpenGL.GL import *

from ..shader.Shader import Shader
from ..shader.ShaderManager import ShaderManager
from ..xyazhServer.ConsoleMessage import ConsoleMessage

__all__ = [
    "BUFFER_BASE", "IMAGE_FORMATS", "bufferDtype", "requireContext",
    "clearProgramCache", "compileProgram", "genBuffer", "deleteBuffers",
    "uploadBuffer", "downloadBuffer", "bufferBytes", "bindBufferBase",
    "bindImage", "textureSize", "dispatch", "barrier", "barrierFor",
    "setUniform", "components", "matValues", "uint32",
    "currentProgram", "currentBinding", "programScope",
    "saveBufferBindings", "restoreBufferBindings",
    "saveImageBindings", "restoreImageBindings",
]

#: GLSL 标量类型 -> SSBO 里真正用的标量类型（bool 用 int 存 0/1）
BUFFER_BASE = {"float": "float", "int": "int", "uint": "uint", "bool": "int"}

#: GLSL 标量类型 -> numpy dtype
_NUMPY_DTYPE = {"float": np.float32, "int": np.int32, "uint": np.uint32}

#: GLSL image 格式 -> (GL 内部格式, 像素格式, 像素类型)
IMAGE_FORMATS = {
    "r32f": (GL_R32F, GL_RED, GL_FLOAT),
    "rg32f": (GL_RG32F, GL_RG, GL_FLOAT),
    "rgba32f": (GL_RGBA32F, GL_RGBA, GL_FLOAT),
    "r32i": (GL_R32I, GL_RED_INTEGER, GL_INT),
    "rg32i": (GL_RG32I, GL_RG_INTEGER, GL_INT),
    "rgba32i": (GL_RGBA32I, GL_RGBA_INTEGER, GL_INT),
    "r32ui": (GL_R32UI, GL_RED_INTEGER, GL_UNSIGNED_INT),
    "rg32ui": (GL_RG32UI, GL_RG_INTEGER, GL_UNSIGNED_INT),
    "rgba32ui": (GL_RGBA32UI, GL_RGBA_INTEGER, GL_UNSIGNED_INT),
}

#: GL 内部格式 -> GLSL image 格式（认已有纹理用；只列能当 image 用的）
GLSL_BY_GL_FORMAT = {
    GL_R32F: "r32f", GL_RG32F: "rg32f", GL_RGBA32F: "rgba32f",
    GL_R32I: "r32i", GL_RG32I: "rg32i", GL_RGBA32I: "rgba32i",
    GL_R32UI: "r32ui", GL_RG32UI: "rg32ui", GL_RGBA32UI: "rgba32ui",
    GL_R8: "r8", GL_RG8: "rg8", GL_RGBA8: "rgba8",
    GL_R8I: "r8i", GL_RG8I: "rg8i", GL_RGBA8I: "rgba8i",
    GL_R8UI: "r8ui", GL_RG8UI: "rg8ui", GL_RGBA8UI: "rgba8ui",
    GL_R16F: "r16f", GL_RG16F: "rg16f", GL_RGBA16F: "rgba16f",
    GL_R16I: "r16i", GL_RG16I: "rg16i", GL_RGBA16I: "rgba16i",
    GL_R16UI: "r16ui", GL_RG16UI: "rg16ui", GL_RGBA16UI: "rgba16ui",
}

_PROGRAMS = {}              # GLSL 源码 -> Shader（换上下文后 clearProgramCache()）

#: 只等 SSBO 写入可见（归约多轮之间用）
SSB_BIT = GL_SHADER_STORAGE_BARRIER_BIT


def bufferDtype(base):
    """GLSL 标量类型 -> numpy dtype。"""
    return np.dtype(_NUMPY_DTYPE[BUFFER_BASE[base]])


# --------------------------------------------------------------- 上下文 / 编译


def requireContext() -> None:
    """复用当前 Context；不是 4.3+ 就给一句人话。"""
    try:
        raw = glGetString(GL_VERSION)
    except Exception as error:
        raise RuntimeError("@glCompute 需要活动的 OpenGL 上下文（%s）" % error)
    if not raw:
        raise RuntimeError("@glCompute 复用引擎当前的 OpenGL Context，但现在没有活动的"
                           "上下文（请在窗口/渲染线程里调用）")
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    match = re.match(r"(\d+)\.(\d+)", text)
    if not match or (int(match.group(1)), int(match.group(2))) < (4, 3):
        raise RuntimeError("compute shader / SSBO 需要 OpenGL 4.3+，当前上下文是：%s" % text)


def numberedSource(source, title):
    body = "\n".join("%4d | %s" % (i + 1, line)
                     for i, line in enumerate(source.splitlines()))
    return "生成的 GLSL（%s）:\n%s" % (title, body)


def _compileError(source, title, error):
    text = numberedSource(source, title)
    ConsoleMessage.printError(text)
    return RuntimeError("%s\n%s" % (error, text))


def compileProgram(source, title):
    """编译一段 compute shader（带进程内缓存）。

    失败时：打印**带行号的完整源码** + shader log，再抛 RuntimeError
    （错误消息里也带源码，方便日志里直接看到）。
    """
    cached = _PROGRAMS.get(source)
    if cached is not None:
        return cached
    requireContext()
    program = 0
    shader_object = 0
    try:
        program = int(glCreateProgram())
        shader_object = int(glCreateShader(GL_COMPUTE_SHADER))
        glShaderSource(shader_object, source.encode("utf-8"))
        glCompileShader(shader_object)
        try:
            ShaderManager._checkShader(shader_object, title)
        except RuntimeError as error:
            raise _compileError(source, title, error)
        glAttachShader(program, shader_object)
        glLinkProgram(program)
        try:
            ShaderManager._checkProgram(program, title)
        except RuntimeError as error:
            raise _compileError(source, title, error)
        shader = Shader(program)
        _PROGRAMS[source] = shader
        return shader
    except BaseException:
        if program:
            glDeleteProgram(program)
        raise
    finally:
        if shader_object:
            glDeleteShader(shader_object)


def clearProgramCache() -> None:
    """丢掉所有缓存的 program（换 GL 上下文之后调用）。"""
    _PROGRAMS.clear()


# ------------------------------------------------------------------- SSBO


def genBuffer():
    return int(glGenBuffers(1))


def deleteBuffers(names) -> None:
    names = [int(n) for n in names if int(n)]
    if not names:
        return
    glDeleteBuffers(len(names), (ctypes.c_uint * len(names))(*names))


def uploadBuffer(name, array, target=GL_SHADER_STORAGE_BUFFER) -> None:
    """把 numpy 数组整块写进缓冲区（重新分配存储 = 孤儿化，避免同步等待）。"""
    glBindBuffer(target, int(name))
    glBufferData(target, int(array.nbytes),
                 array.ctypes.data_as(ctypes.c_void_p) if array.nbytes else None,
                 GL_DYNAMIC_DRAW)


def downloadBuffer(name, array, target=GL_SHADER_STORAGE_BUFFER):
    """把缓冲区内容读进 numpy 数组（同步，会等 GPU 算完）。"""
    if not array.nbytes:
        return array
    glBindBuffer(target, int(name))
    glGetBufferSubData(target, 0, int(array.nbytes),
                       array.ctypes.data_as(ctypes.c_void_p))
    return array


def bufferBytes(name):
    """查已有缓冲区的字节数（借 GL_COPY_READ_BUFFER 这个"空"目标，不动别的绑定）。"""
    glBindBuffer(GL_COPY_READ_BUFFER, int(name))
    size = (ctypes.c_int * 1)()
    glGetBufferParameteriv(GL_COPY_READ_BUFFER, GL_BUFFER_SIZE, size)
    glBindBuffer(GL_COPY_READ_BUFFER, 0)
    return int(size[0])


def ensureBufferSize(name, nbytes, target=GL_SHADER_STORAGE_BUFFER) -> None:
    """保证缓冲区至少有 ``nbytes`` 的存储（不够才重新分配；glGenBuffers 出来的没有存储）。

    SSBO 一定要有存储：没存储时 shader 写进去会被丢弃，读回来还会 GL_INVALID_VALUE。
    """
    nbytes = int(nbytes)
    if nbytes <= 0 or bufferBytes(name) >= nbytes:
        return
    glBindBuffer(target, int(name))
    glBufferData(target, nbytes, None, GL_DYNAMIC_DRAW)


def bindBufferBase(slot, name) -> None:
    """把已有缓冲区绑成 SSBO（**不复制数据**）。"""
    glBindBufferBase(GL_SHADER_STORAGE_BUFFER, int(slot), int(name))


def saveBufferBindings(slots):
    """记下这些 SSBO 绑定点上原来绑的是谁（用完要还回去）。"""
    saved = {}
    out = (ctypes.c_int * 1)()
    for slot in slots:
        slot = int(slot)
        glGetIntegeri_v(GL_SHADER_STORAGE_BUFFER_BINDING, slot, out)
        saved[slot] = int(out[0])
    return saved


def restoreBufferBindings(saved) -> None:
    for slot, name in saved.items():
        glBindBufferBase(GL_SHADER_STORAGE_BUFFER, int(slot), int(name))


def saveImageBindings(units):
    """记下这些 image unit 上原来的整套绑定（name/level/layered/access/format）。"""
    saved = {}
    name = (ctypes.c_int * 1)()
    level = (ctypes.c_int * 1)()
    layered = (ctypes.c_int * 1)()
    access = (ctypes.c_int * 1)()
    fmt = (ctypes.c_int * 1)()
    for unit in units:
        unit = int(unit)
        glGetIntegeri_v(GL_IMAGE_BINDING_NAME, unit, name)
        glGetIntegeri_v(GL_IMAGE_BINDING_LEVEL, unit, level)
        glGetIntegeri_v(GL_IMAGE_BINDING_LAYERED, unit, layered)
        glGetIntegeri_v(GL_IMAGE_BINDING_ACCESS, unit, access)
        glGetIntegeri_v(GL_IMAGE_BINDING_FORMAT, unit, fmt)
        saved[unit] = (int(name[0]), int(level[0]), int(layered[0]),
                       int(access[0]), int(fmt[0]))
    return saved


def restoreImageBindings(saved) -> None:
    for unit, (name, level, layered, access, fmt) in saved.items():
        glBindImageTexture(int(unit), name, level, layered, 0, access, fmt)


# ------------------------------------------------------------------ Image


def bindImage(unit, name, target, access, gl_format) -> None:
    """把已有纹理绑成 image unit（层 0；3D 纹理整张 layered 绑定）。"""
    layered = GL_TRUE if target == GL_TEXTURE_3D else GL_FALSE
    glBindImageTexture(int(unit), int(name), 0, layered, 0, access, gl_format)


def textureSize(target, name, level=0):
    """纹理尺寸 -> (w, h, d)（1D/2D 的 y/z 补 1）。

    用完把**原来绑在这个纹理单元上的纹理**还原回去：engine 的 tex0 就挂在当前
    active unit 上，被顶掉之后物体就采样不到自己的贴图了。
    """
    out = (ctypes.c_int * 3)()
    previous = currentBinding(target)
    glBindTexture(target, int(name))
    glGetTexLevelParameteriv(target, level, GL_TEXTURE_WIDTH, out)
    if target != GL_TEXTURE_1D:
        glGetTexLevelParameteriv(target, level, GL_TEXTURE_HEIGHT, ctypes.byref(out, 4))
    if target == GL_TEXTURE_3D:
        glGetTexLevelParameteriv(target, level, GL_TEXTURE_DEPTH, ctypes.byref(out, 8))
    glBindTexture(target, previous)
    return (int(out[0]), int(out[1]) or 1, int(out[2]) or 1)


# ---------------------------------------------------------------- 派发 / 同步


def currentProgram():
    """当前 GL 的 program（没绑就是 0）。"""
    out = (ctypes.c_int * 1)()
    glGetIntegerv(GL_CURRENT_PROGRAM, out)
    return int(out[0])


def currentBinding(target):
    """当前 active texture unit 上 target 绑的纹理（没绑就是 0）。"""
    query = {GL_TEXTURE_2D: GL_TEXTURE_BINDING_2D,
             GL_TEXTURE_3D: GL_TEXTURE_BINDING_3D}.get(target)
    if query is None:
        return 0
    out = (ctypes.c_int * 1)()
    glGetIntegerv(query, out)
    return int(out[0])


class programScope(object):
    """临时切到 compute program，退出时把"当前 program"原样还回去。

    ⚠ **必须还**：engine 的 ``Shader.use()`` 只在 ``using_shader`` 不是自己时才真的
    ``glUseProgram``。compute 用完不还原的话，engine 会以为自己的 program 还绑着，
    于是同一帧里后面的物体（背景、精灵……）**全用 compute program 画** —— 表现就是
    整屏变成一片糊色。这里也不碰 ``RenderGlobal.using_shader``（那是 engine 的记账）。
    """

    __slots__ = ("program", "previous")

    def __init__(self, program):
        self.program = int(program)
        self.previous = 0

    def __enter__(self):
        self.previous = currentProgram()
        glUseProgram(self.program)
        return self

    def __exit__(self, *exc):
        glUseProgram(self.previous)
        return False


def dispatch(groups) -> None:
    """派发线程组（每组线程数由 shader 的 ``local_size_*`` 决定）。"""
    gx, gy, gz = (max(1, int(g)) for g in groups)
    glDispatchCompute(gx, gy, gz)


def barrierFor(buffers=(), images=(), extra=0):
    """按"这次 kernel 真的碰了什么"拼 barrier 位：

    - 写过 SSBO（含被当 SSBO 用的 VBO）：STORAGE + VERTEX_ATTRIB
      （后面可能当 SSBO 读，也可能直接拿去画）
    - 写过 image：IMAGE_ACCESS + TEXTURE_FETCH（后面可能 imageLoad 或纹理采样）
    """
    bits = int(extra)
    if buffers:
        bits |= GL_SHADER_STORAGE_BARRIER_BIT | GL_VERTEX_ATTRIB_ARRAY_BARRIER_BIT
    if images:
        bits |= (GL_SHADER_IMAGE_ACCESS_BARRIER_BIT
                 | GL_TEXTURE_FETCH_BARRIER_BIT)
    return bits or GL_ALL_BARRIER_BITS


def barrier(bits=GL_ALL_BARRIER_BITS) -> None:
    glMemoryBarrier(int(bits))


# ----------------------------------------------------------------- uniform


def components(value, n, name):
    """向量 uniform 的分量（接受 glm 向量 / tuple / list / numpy / 标量广播）。"""
    attrs = "xyzw"[:n]
    if all(hasattr(value, a) for a in attrs):
        return [getattr(value, a) for a in attrs]
    if isinstance(value, np.ndarray):
        flat = value.reshape(-1)
        if flat.size == 1:
            return [flat[0]] * n
        if flat.size != n:
            raise TypeError("uniform 参数 `%s` 需要 %d 个分量，收到 %d 个"
                            % (name, n, flat.size))
        return list(flat)
    try:
        seq = list(value)
    except TypeError:
        seq = None
    if seq is None:
        if isinstance(value, (int, float, bool, np.generic)):
            return [value] * n
        raise TypeError("uniform 参数 `%s` 需要 %d 个分量，收到 %r" % (name, n, value))
    if len(seq) == 1:
        return list(seq) * n
    if len(seq) != n:
        raise TypeError("uniform 参数 `%s` 需要 %d 个分量，收到 %d 个" % (name, n, len(seq)))
    return seq


def matValues(value, t, name):
    """矩阵 uniform -> (列, 行) 的 float32 数组（列主序内存，GLSL 就这么吃）。"""
    if isinstance(value, np.ndarray):
        arr = np.ascontiguousarray(value, dtype=np.float32)
    else:
        try:
            arr = np.array([[float(value[c][r]) for r in range(t.rows)]
                            for c in range(t.cols)], dtype=np.float32)
        except Exception:
            raise TypeError("uniform 参数 `%s`（%s）需要 (列, 行) = (%d, %d) 的矩阵"
                            % (name, t, t.cols, t.rows))
    if arr.shape != (t.cols, t.rows):
        raise ValueError("uniform 参数 `%s`（%s）需要形状 (%d, %d)，收到 %s"
                         % (name, t, t.cols, t.rows, arr.shape))
    return np.ascontiguousarray(arr)


_UNIFORM_VEC = {
    ("float", 1): glUniform1f, ("float", 2): glUniform2f,
    ("float", 3): glUniform3f, ("float", 4): glUniform4f,
    ("int", 1): glUniform1i, ("int", 2): glUniform2i,
    ("int", 3): glUniform3i, ("int", 4): glUniform4i,
    ("uint", 1): glUniform1ui, ("uint", 2): glUniform2ui,
    ("uint", 3): glUniform3ui, ("uint", 4): glUniform4ui,
}

_UNIFORM_MAT = {
    (2, 2): glUniformMatrix2fv, (3, 3): glUniformMatrix3fv, (4, 4): glUniformMatrix4fv,
    (2, 3): glUniformMatrix2x3fv, (3, 2): glUniformMatrix3x2fv,
    (2, 4): glUniformMatrix2x4fv, (4, 2): glUniformMatrix4x2fv,
    (3, 4): glUniformMatrix3x4fv, (4, 3): glUniformMatrix4x3fv,
}


def uint32(value):
    """Python 的负数 -> GLSL uint 的位模式。"""
    return int(value) & 0xFFFFFFFF


def setUniform(location, t, value, name="?") -> None:
    """按 GType 把值塞进 uniform。"""
    base = t.base
    if t.is_mat:
        setter = _UNIFORM_MAT.get((t.cols, t.rows))
        if setter is None:                  # pragma: no cover - 表里覆盖了全部尺寸
            raise TypeError("不支持 %s 类型的 uniform" % t)
        setter(int(location), 1, GL_FALSE, matValues(value, t, name))
        return
    if t.is_scalar:
        if base == "float":
            glUniform1f(int(location), float(value))
        elif base == "int":
            glUniform1i(int(location), int(value))
        elif base == "uint":
            glUniform1ui(int(location), uint32(value))
        else:
            glUniform1i(int(location), 1 if value else 0)
        return
    comps = components(value, t.comps, name)
    if base == "float":
        _UNIFORM_VEC[("float", t.comps)](int(location), *[float(v) for v in comps])
    elif base == "int":
        _UNIFORM_VEC[("int", t.comps)](int(location), *[int(v) for v in comps])
    elif base == "uint":
        _UNIFORM_VEC[("uint", t.comps)](int(location), *[uint32(v) for v in comps])
    else:
        _UNIFORM_VEC[("int", t.comps)](int(location), *[1 if v else 0 for v in comps])

