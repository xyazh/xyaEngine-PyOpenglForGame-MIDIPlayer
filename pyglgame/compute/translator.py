"""受限 Python 函数 -> GLSL compute shader 源码的翻译器。

只干一件事：把 ``@glCompute`` 装饰的函数体（AST）翻成一段 compute shader
源码，外加运行时需要的元信息（每个参数是 SSBO / image 还是 uniform、
各自的绑位与长度 uniform……）。
**不碰 OpenGL** —— 编译 / 派发 / 读回都在 :mod:`pyglgame.compute.GlRuntime`
与 :mod:`pyglgame.compute.GlCompute`。

编程模型是"显式下标"那一套（线程自己算下标、自己写回）::

    @glCompute
    def process(a: Buffer[float], b: Buffer[float]):
        i = index()
        if i >= len(a):
            return
        a[i] = a[i] + b[i]

- ``Buffer[T]`` 参数 = 一整块 SSBO（可读可写，``a[i]`` / ``a[y, x]``）；
- ``Image[T]`` 参数 = 一张 image2D/image3D（``imageLoad`` / ``imageStore``）；
- 普通 ``float/int/uint/bool/vec*/mat*`` 参数 = uniform；
- ``len(a)`` 是该 buffer 的元素个数（每块一个长度 uniform，运行时自动设置）；
- 函数**没有返回值**，结果写进 Buffer/Image。

翻译走**两遍**：

1. 干跑：不产出代码，只做类型推导、收集局部变量表；
2. 生成：真正写出 GLSL。

之所以要干跑，是因为 Python 的块作用域和 GLSL 不一样::

    if x > 0.0:
        y = 1.0        # Python 里 y 出了 if 还活着
    else:
        y = 2.0
    z = y

GLSL 里两个分支各声明一个 y 是不行的，所以第一遍先把局部变量收全，
第二遍把它们统一声明到 ``main`` 顶部（``float y;``），赋值语句留在原处。

明确不支持（遇到就抛 :class:`CompileError`，带行号与源码原文）：
class / dict / set / 动态 list / 生成器 / async / try / with / lambda /
推导式 / 切片 / 多重解包 / 任意 Python 库调用 / 任意精度整数。
"""

import ast
import linecache
from collections import namedtuple

__all__ = [
    "CompileError", "GType", "GlslType", "Buffer", "Image", "BufferType",
    "ImageType", "Param", "ShaderInfo", "BufferParam", "ImageParam",
    "UniformParam", "analyzeSignature", "resolveType", "resolveParamType",
    "translateFunction", "GLSL_TYPES", "SCALARS", "BUFFER_BASE", "arrayType",
    "vecType", "uint", "vec2", "vec3", "vec4", "ivec2", "ivec3", "ivec4",
    "uvec2", "uvec3", "uvec4", "bvec2", "bvec3", "bvec4",
    "mat2", "mat3", "mat4",
]


# ====================================================================== 错误


def _sourceLine(filename, lineno):
    """取某一行的源码原文（错误提示里原样引用用）。"""
    if not filename or not lineno:
        return ""
    lines = _EXPLICIT_SOURCES.get(filename)
    if lines is None:
        try:
            lines = linecache.getlines(filename)
        except Exception:
            return ""
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()
    return ""


#: 显式给的源码（``@glCompute(source=...)``）：合成文件名 -> 源码行。
#: 打包（Nuitka/PyInstaller）之后没有 .py 文件，报错里的"源码原文"就靠它。
_EXPLICIT_SOURCES = {}


def explicitSourceName(func_name):
    """显式源码用的合成"文件名"（报错信息里显示它，而不是一个不存在的路径）。"""
    return "<glCompute source: %s>" % (func_name or "kernel")


class CompileError(RuntimeError):
    """翻译期错误：语法不支持 / 类型推导失败 / 类型不匹配。

    继承 ``RuntimeError``，和引擎里"着色器编译失败即抛 RuntimeError"
    的 fail-fast 约定保持一致。消息里带**行号 + 源码原文 + 原因**
    （行号是**源文件里的绝对行号**）。
    """

    def __init__(self, message, node=None, filename=None, func=None,
                 line_offset=0, lineno=None) -> None:
        self.node = node
        self.filename = filename
        if lineno is None:
            lineno = getattr(node, "lineno", None)
        if lineno is not None:
            lineno += line_offset          # 片段行号 -> 文件绝对行号
        self.lineno = lineno
        self.source_line = _sourceLine(filename, lineno)
        text = message
        if self.lineno:
            where = "第 %d 行" % self.lineno
            if self.source_line:
                where += "（`%s`）" % self.source_line
            text = "%s: %s" % (where, message)
        if func:
            text = "%s(): %s" % (func, text)
        super(CompileError, self).__init__(text)


# ====================================================================== 类型


class GType(object):
    """一个 GLSL 值类型：标量 / 向量 / 矩阵 / 局部数组。"""

    __slots__ = ("glsl", "base", "comps", "cols", "rows", "elem", "length")

    def __init__(self, glsl, base, comps=1, cols=0, rows=0, elem=None, length=None) -> None:
        self.glsl = glsl        # GLSL 类型名；数组为 None
        self.base = base        # 'float' / 'int' / 'uint' / 'bool' / None(void)
        self.comps = comps      # 标量分量数（矩阵 = 列 x 行）
        self.cols = cols        # 矩阵列数（非矩阵 0）
        self.rows = rows        # 矩阵行数（非矩阵 0）
        self.elem = elem        # 数组元素类型（非数组 None）
        self.length = length    # 数组长度；None = 长度未知（SSBO 视图）

    # ------------------------------------------------------------ 判定
    @property
    def is_array(self):
        return self.elem is not None

    @property
    def is_void(self):
        return self.glsl == "void"

    @property
    def is_mat(self):
        return self.cols > 0

    @property
    def is_vec(self):
        return self.elem is None and self.cols == 0 and self.comps > 1

    @property
    def is_scalar(self):
        return self.elem is None and self.cols == 0 and self.comps == 1

    @property
    def is_bool(self):
        return self.base == "bool"

    @property
    def is_int(self):
        return self.base in ("int", "uint")

    # ------------------------------------------------------------ 文本
    def baseGlsl(self):
        """剥掉数组维度后的 GLSL 类型名。"""
        t = self
        while t.is_array:
            t = t.elem
        return t.glsl

    def dims(self):
        """数组维度文本：``[4]`` / ``[2][3]``。"""
        out = ""
        t = self
        while t.is_array:
            out += "[%s]" % (t.length if t.length is not None else "")
            t = t.elem
        return out

    def ctor(self):
        """数组构造器类型名：``float[4]``。"""
        return "%s%s" % (self.baseGlsl(), self.dims())

    def decl(self, name):
        """局部变量声明的类型+名字：``float a[4]``。"""
        if self.is_array:
            return "%s %s%s" % (self.baseGlsl(), name, self.dims())
        return "%s %s" % (self.glsl, name)

    def __str__(self):
        return self.glsl if not self.is_array else self.ctor()

    def __repr__(self):
        return "<GType %s>" % self


FLOAT = GType("float", "float")
INT = GType("int", "int")
UINT = GType("uint", "uint")
BOOL = GType("bool", "bool")
VOID = GType("void", None, 0)

#: SSBO 里用哪种标量存（GLSL 的 bool 不能直接当缓冲区元素，用 int 存 0/1）
BUFFER_BASE = {"float": "float", "int": "int", "uint": "uint", "bool": "int"}

SCALARS = {"float": FLOAT, "int": INT, "uint": UINT, "bool": BOOL}

#: 向量前缀：float -> vec3，int -> ivec3，uint -> uvec3，bool -> bvec3
_VEC_PREFIX = {"float": "", "int": "i", "uint": "u", "bool": "b"}


def vecType(base, n):
    return GType("%svec%d" % (_VEC_PREFIX[base], n), base, n)


def matType(cols, rows):
    name = "mat%d" % cols if cols == rows else "mat%dx%d" % (cols, rows)
    return GType(name, "float", cols * rows, cols, rows)


def arrayType(elem, length=None):
    """局部数组（``length`` 为 int）或 SSBO 视图（``length=None``）。"""
    return GType(None, elem.base, elem.comps, elem.cols, elem.rows, elem, length)


#: 所有能被标注 / 构造的 GLSL 类型
GLSL_TYPES = {}
for _b in ("float", "int", "uint", "bool"):
    GLSL_TYPES[_b] = SCALARS[_b]
    for _n in (2, 3, 4):
        GLSL_TYPES["%svec%d" % (_VEC_PREFIX[_b], _n)] = vecType(_b, _n)
for _c in (2, 3, 4):
    for _r in (2, 3, 4):
        GLSL_TYPES["mat%d" % _c if _c == _r else "mat%dx%d" % (_c, _r)] = matType(_c, _r)


class GlslType(object):
    """类型标注用的标记（``vec3`` / ``mat4`` / ``uint``……）。

    它只是"告诉 @glCompute 这个参数是什么 GLSL 类型"，
    不能（也不需要）在 Python 里真的构造出值。
    """

    __slots__ = ("glsl_name",)

    def __init__(self, name) -> None:
        self.glsl_name = name

    def __repr__(self):
        return self.glsl_name

    def __call__(self, *args, **kwargs) -> None:
        raise TypeError(
            "%s 只是 @glCompute 的类型标注，不能在 Python 里当构造器用"
            "（GLSL 里的 %s(...) 请写在被装饰函数体内）" % (self.glsl_name, self.glsl_name))


uint = GlslType("uint")
vec2, vec3, vec4 = GlslType("vec2"), GlslType("vec3"), GlslType("vec4")
ivec2, ivec3, ivec4 = GlslType("ivec2"), GlslType("ivec3"), GlslType("ivec4")
uvec2, uvec3, uvec4 = GlslType("uvec2"), GlslType("uvec3"), GlslType("uvec4")
bvec2, bvec3, bvec4 = GlslType("bvec2"), GlslType("bvec3"), GlslType("bvec4")
mat2, mat3, mat4 = GlslType("mat2"), GlslType("mat3"), GlslType("mat4")

#: numpy 标量也能当标注
_NUMPY_TYPE_NAMES = {
    "float16": "float", "float32": "float", "float64": "float",
    "int8": "int", "int16": "int", "int32": "int", "int64": "int",
    "uint8": "uint", "uint16": "uint", "uint32": "uint", "uint64": "uint",
    "bool_": "bool", "bool8": "bool",
}

_HINT_TYPES = "float/int/uint/bool、vec2..vec4、ivec*/uvec*/bvec*、mat2..mat4"


def resolveType(annotation, where=""):
    """把 Python 类型标注解析成 :class:`GType`；不认识就抛 CompileError。"""
    if isinstance(annotation, GType):
        return annotation
    if isinstance(annotation, GlslType):
        found = GLSL_TYPES.get(annotation.glsl_name)
        if found is None:
            raise CompileError("未知的 GLSL 类型 `%s`%s" % (annotation.glsl_name, where))
        return found
    if isinstance(annotation, str):
        text = annotation.strip().strip("'\"")
        if text in ("None", "none", "NoneType", ""):
            return None
        found = GLSL_TYPES.get(text)
        if found is None:
            raise CompileError("无法识别的类型标注 `%s`%s（可用：%s）"
                               % (text, where, _HINT_TYPES))
        return found
    if annotation is float:
        return FLOAT
    if annotation is int:
        return INT
    if annotation is bool:
        return BOOL
    if annotation is None:
        return None
    name = getattr(annotation, "__name__", "")
    if name in _NUMPY_TYPE_NAMES:
        return GLSL_TYPES[_NUMPY_TYPE_NAMES[name]]
    raise CompileError("不支持的类型标注 %r%s（可用：%s）"
                       % (annotation, where, _HINT_TYPES))


# --------------------------------------------------------------- Buffer / Image


class BufferType(object):
    """``Buffer[T]`` 的结果：一个 SSBO 数组参数（元素类型 = ``element``）。"""

    glsl_kind = "buffer"
    __slots__ = ("element",)

    def __init__(self, element) -> None:
        self.element = element

    def __repr__(self):
        return "Buffer[%s]" % self.element


class ImageType(object):
    """``Image[T]`` 的结果：一张 image2D/image3D 参数。"""

    glsl_kind = "image"
    __slots__ = ("element",)

    def __init__(self, element) -> None:
        self.element = element

    def __repr__(self):
        return "Image[%s]" % self.element


class Buffer(object):
    """标注：``Buffer[float]`` / ``Buffer[vec3]`` —— 一整块可读写的 SSBO。

    ``T`` 是**元素**类型；底层 SSBO 是**按分量摊平**的标量数组
    （这样 std430 的对齐/跨距和 numpy 的连续内存一定一致，
    避开 ``vec3`` 在 std430 里 16 字节跨距的坑）。
    """

    def __new__(cls, *args, **kwargs) -> None:
        raise TypeError("Buffer[...] 只能当类型标注用（写成 `a: Buffer[float]`）")

    def __class_getitem__(cls, item):
        element = resolveType(item, "（Buffer[...] 的元素类型）")
        if element is None or element.is_void:
            raise CompileError("Buffer[...] 的元素类型不合法：%r" % (item,))
        return BufferType(element)


class Image(object):
    """标注：``Image[vec4]`` / ``Image[float]`` —— 一张可读写的 image2D/3D。

    元素类型决定 image 格式：``float`` -> ``r32f``、``vec2`` -> ``rg32f``、
    ``vec3``/``vec4`` -> ``rgba32f``、``int`` -> ``r32i``、``uint`` -> ``r32ui``……
    （``vec3`` 用 ``rgba32f`` 装，读写时自动只看 ``.rgb``。）
    """

    def __new__(cls, *args, **kwargs) -> None:
        raise TypeError("Image[...] 只能当类型标注用（写成 `img: Image[vec4]`）")

    def __class_getitem__(cls, item):
        element = resolveType(item, "（Image[...] 的元素类型）")
        if element is None or element.is_void or element.is_bool or element.is_mat:
            raise CompileError(
                "Image[...] 的元素类型只能是 float/int/uint 或 vec2..4/ivec/uvec：%r" % (item,))
        return ImageType(element)


#: 引擎纹理类上常见的"绑定为 image"方法：有其中一个就当它是 Texture 标注
_TEXTURE_METHODS = ("bindImage2D", "bindImage3D", "bindImage", "bindUnit",
                    "useTextureParameteri")


def looksLikeTexture(annotation):
    """标注写的是引擎的纹理类（``TextureStorage2D`` 之类）吗？

    （``def f(img: TextureStorage2D)`` 这种写法，元素类型按 ``vec4`` 处理；
    要别的元素类型请用 ``Image[float]`` / ``Image[uint]`` 之类。）
    """
    for name in _TEXTURE_METHODS:
        if hasattr(annotation, name):
            return True
    return False


def resolveParamType(annotation, where=""):
    """参数标注 -> ``(kind, GType)``；``kind`` 是 ``"buffer"/"image"/"uniform"``。"""
    kind = getattr(annotation, "glsl_kind", None)
    if kind == "buffer":
        return "buffer", annotation.element
    if kind == "image":
        return "image", annotation.element
    if looksLikeTexture(annotation):
        return "image", GLSL_TYPES["vec4"]      # 引擎纹理类：按 RGBA 纹理处理
    gtype = resolveType(annotation, where)
    if gtype is None:
        raise CompileError("参数不能标注为 None%s" % where)
    return "uniform", gtype


# ====================================================================== 签名

#: 参数：Python 名 / 元素类型 / 形态（buffer|image|uniform）/ 默认值 / 有无默认值
Param = namedtuple("Param", "name type kind default has_default")


def analyzeSignature(fn):
    """解析被装饰函数签名，返回参数列表（每个参数带 ``kind``）。

    返回类型必须为空或 ``None``（结果写进 Buffer/Image），否则报错。
    """
    import inspect

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as error:      # pragma: no cover - 极少数内建函数
        raise CompileError("%s(): 读不到函数签名（%s）" % (fn.__name__, error))

    name = getattr(fn, "__name__", "?")
    params = []
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            raise CompileError("%s(): 不支持 *args / **kwargs" % name)
        if p.annotation is p.empty:
            raise CompileError(
                "%s(): 参数 `%s` 缺少类型标注（数组写 `%s: Buffer[float]`，"
                "标量写 `%s: float`）" % (name, p.name, p.name, p.name))
        kind, ptype = resolveParamType(p.annotation, "（参数 `%s`）" % p.name)
        has_default = p.default is not p.empty
        if has_default and kind != "uniform":
            raise CompileError("%s(): 只有 uniform（标量）参数能有默认值，"
                               "`%s` 是 %s" % (name, p.name, kind))
        params.append(Param(p.name, ptype, kind,
                            p.default if has_default else None, has_default))
    if len(set(p.name for p in params)) != len(params):   # pragma: no cover - Python 已保证
        raise CompileError("%s(): 参数名重复" % name)

    if sig.return_annotation is not sig.empty:
        ret = resolveType(sig.return_annotation, "（返回类型）")
        if ret is not None:
            raise CompileError(
                "%s(): @glCompute 的函数不要返回值（结果写进 Buffer/Image）；"
                "要算总和/最值请用 glCompute.sum / .dot / .min / .max" % name)
    return params


# ================================================================ 内置名字表

#: GPU 线程 id / 尺寸：名字 -> (GLSL 表达式, 类型)
#: 既是"函数"（``index()``）也是"名字"（``index``），两种写法都认。
#: 一律给 **int**，省得 uint 和 len() 打架；要 uint 自己 ``uint(...)``。
_INTRINSICS = {
    "index": ("int(gl_GlobalInvocationID.x)", INT),
    "global_x": ("int(gl_GlobalInvocationID.x)", INT),
    "global_y": ("int(gl_GlobalInvocationID.y)", INT),
    "global_z": ("int(gl_GlobalInvocationID.z)", INT),
    # 把 (x, y, z) 展平成一维（1D/2D 派发时 y/z 为 0，正好退化成一个下标）
    "global_id": ("((int(gl_GlobalInvocationID.z) * u_height"
                  " + int(gl_GlobalInvocationID.y)) * u_width"
                  " + int(gl_GlobalInvocationID.x))", INT),
    "local_x": ("int(gl_LocalInvocationID.x)", INT),
    "local_y": ("int(gl_LocalInvocationID.y)", INT),
    "local_z": ("int(gl_LocalInvocationID.z)", INT),
    "local_id": ("(int(gl_LocalInvocationID.y) * int(gl_WorkGroupSize.x)"
                 " + int(gl_LocalInvocationID.x))", INT),
    "group_x": ("int(gl_WorkGroupID.x)", INT),
    "group_y": ("int(gl_WorkGroupID.y)", INT),
    "group_z": ("int(gl_WorkGroupID.z)", INT),
    "group_id": ("((int(gl_WorkGroupID.z) * int(gl_NumWorkGroups.y)"
                 " + int(gl_WorkGroupID.y)) * int(gl_NumWorkGroups.x)"
                 " + int(gl_WorkGroupID.x))", INT),
    "groups_x": ("int(gl_NumWorkGroups.x)", INT),
    "groups_y": ("int(gl_NumWorkGroups.y)", INT),
    "width": ("u_width", INT),        # 本次派发的 x 方向长度（= 第一个数组的宽度）
    "height": ("u_height", INT),
    "depth": ("u_depth", INT),
    "count": ("u_count", INT),        # 本次派发总线程数 = width * height * depth
    # 旧写法（uint）保留，省的改代码
    "global_id_x": ("gl_GlobalInvocationID.x", UINT),
    "global_id_y": ("gl_GlobalInvocationID.y", UINT),
    "global_id_z": ("gl_GlobalInvocationID.z", UINT),
    "local_id_x": ("gl_LocalInvocationID.x", UINT),
    "local_id_y": ("gl_LocalInvocationID.y", UINT),
    "local_id_z": ("gl_LocalInvocationID.z", UINT),
    "workgroup_id_x": ("gl_WorkGroupID.x", UINT),
    "workgroup_id_y": ("gl_WorkGroupID.y", UINT),
    "workgroup_id_z": ("gl_WorkGroupID.z", UINT),
    "num_workgroups_x": ("gl_NumWorkGroups.x", UINT),
    "num_workgroups_y": ("gl_NumWorkGroups.y", UINT),
    "num_workgroups_z": ("gl_NumWorkGroups.z", UINT),
    "local_size_x": ("gl_WorkGroupSize.x", UINT),
    "local_size_y": ("gl_WorkGroupSize.y", UINT),
    "local_size_z": ("gl_WorkGroupSize.z", UINT),
}

#: 不带参数、只当语句用的同步函数（``memory_barrier()`` 是 ``memoryBarrier()`` 的别名）
_SYNC_FUNCS = {
    "barrier": "barrier",
    "memory_barrier": "memoryBarrier",
    "memoryBarrier": "memoryBarrier",
    "memoryBarrierBuffer": "memoryBarrierBuffer",
    "memoryBarrierShared": "memoryBarrierShared",
    "memoryBarrierImage": "memoryBarrierImage",
    "groupMemoryBarrier": "groupMemoryBarrier",
}

_ATOMIC_FUNCS = frozenset((
    "atomicAdd", "atomicMin", "atomicMax", "atomicAnd", "atomicOr",
    "atomicXor", "atomicExchange", "atomicCompSwap",
))

#: 显式 image 访问（也可以直接用 `img[y, x]` 下标写法；两种拼法都认）
_IMAGE_FUNCS = frozenset(("image_load", "imageLoad", "image_store", "imageStore",
                          "image_size", "imageSize"))

#: 常数
_CONSTANTS = {
    "PI": "3.141592653589793",
    "TAU": "6.283185307179586",
    "E": "2.718281828459045",
}

#: glsl 数学函数：名字 -> (结果规则, 允许的参数个数)
#:   "same"   结果 = 各参数统一后的类型（同维、int 向 float 提升）
#:   "float"  同 "same"，但参数先转 float（pow / mod 没有 int 重载）
#:   "scalar" 结果 = float 标量（length / distance / dot）
#:   "cross"  = vec3（参数先转成 vec3）
_MATH_FUNCS = {
    "sin": ("same", (1,)), "cos": ("same", (1,)), "tan": ("same", (1,)),
    "asin": ("same", (1,)), "acos": ("same", (1,)), "atan": ("same", (1, 2)),
    "sinh": ("same", (1,)), "cosh": ("same", (1,)), "tanh": ("same", (1,)),
    "sqrt": ("same", (1,)), "inversesqrt": ("same", (1,)),
    "exp": ("same", (1,)), "exp2": ("same", (1,)),
    "log": ("same", (1,)), "log2": ("same", (1,)),
    "pow": ("float", (2,)), "mod": ("float", (2,)),
    "abs": ("same", (1,)), "sign": ("same", (1,)),
    "floor": ("same", (1,)), "ceil": ("same", (1,)), "round": ("same", (1,)),
    "trunc": ("same", (1,)), "fract": ("same", (1,)),
    "min": ("same", (2,)), "max": ("same", (2,)), "clamp": ("same", (3,)),
    "mix": ("same", (3,)), "step": ("same", (2,)), "smoothstep": ("same", (3,)),
    "length": ("scalar", (1,)), "distance": ("scalar", (2,)), "dot": ("scalar", (2,)),
    "cross": ("cross", (2,)),
    "normalize": ("same", (1,)),
    "reflect": ("same", (2,)), "refract": ("same", (3,)),
    "faceforward": ("same", (3,)),
    "radians": ("same", (1,)), "degrees": ("same", (1,)),
}

#: 写成 `math.sin` / `np.sin` 也能认出来
_MODULE_ALIASES = frozenset(("math", "m", "np", "numpy", "glm", "gl"))

# ==================================================== kernel 里可以直接 import 的名字
#
# 翻译器**只在 AST 上按名字**认这些内置：写 kernel 时其实一个都不用 import。
# 这里额外把它们做成真的对象，一是 `from pyglgame.compute import global_x,
# image_store` 不报 ImportError，二是 IDE / pyflakes 不标红；真正在 kernel
# 外面调用会抛 CompileError（它们没有 Python 实现）。

_BUILTIN_CLASH = frozenset(("abs", "min", "max", "pow", "round", "sum"))

#: 各表里所有"函数形态"的名字（`PI/TAU/E` 是常数，单独给真值）
_KERNEL_FUNCS = ((set(_INTRINSICS) | set(_SYNC_FUNCS) | set(_ATOMIC_FUNCS)
                  | set(_IMAGE_FUNCS) | set(_MATH_FUNCS)) - _BUILTIN_CLASH)


def _kernelOnly(name):
    def stub(*args, **kwargs):
        raise CompileError(
            "`%s` 只在 @glCompute 的 kernel 里有效（翻译期直接变 GLSL），"
            "普通 Python 代码里没法调用" % name)
    stub.__name__ = name
    stub.__qualname__ = name
    stub.__doc__ = "@glCompute kernel 内的内置名字：%s（翻译期 -> GLSL）" % name
    return stub


for _name in sorted(_KERNEL_FUNCS):
    globals()[_name] = _kernelOnly(_name)
for _name in _CONSTANTS:
    globals()[_name] = float(_CONSTANTS[_name])
del _name

__all__ = __all__ + sorted(_KERNEL_FUNCS | set(_CONSTANTS))

#: 块头/赋值里出现的、需要挡住的 GLSL 保留字（Python 里合法、GLSL 里不合法）
_GLSL_RESERVED = frozenset("""
attribute const uniform varying buffer shared coherent volatile restrict readonly
writeonly layout centroid flat smooth noperspective patch sample subroutine
in out inout struct void do while for if else switch case default break continue
return discard true false precision highp mediump lowp
mat2 mat3 mat4 mat2x2 mat2x3 mat2x4 mat3x2 mat3x3 mat3x4 mat4x2 mat4x3 mat4x4
vec2 vec3 vec4 ivec2 ivec3 ivec4 uvec2 uvec3 uvec4 bvec2 bvec3 bvec4
int uint float double bool atomic_uint row_major namespace using inline noinline
public static extern external interface long short half fixed unsigned superp
goto partition active common superp sampler1D sampler2D sampler3D samplerCube
""".split())

#: 不支持的语法 -> 人话
_UNSUPPORTED = {
    "ClassDef": "class 定义", "FunctionDef": "嵌套函数 / def",
    "AsyncFunctionDef": "async def", "Lambda": "lambda",
    "Import": "import", "ImportFrom": "from ... import",
    "Global": "global 声明的全局变量", "Nonlocal": "nonlocal",
    "Try": "try/except/finally", "TryStar": "except*",
    "Raise": "raise", "Assert": "assert", "Delete": "del",
    "With": "with", "AsyncWith": "async with",
    "AsyncFor": "async for", "Await": "await",
    "Yield": "yield", "YieldFrom": "yield from",
    "Match": "match/case", "MatchValue": "match/case",
    "ListComp": "列表推导式", "SetComp": "集合推导式",
    "DictComp": "字典推导式", "GeneratorExp": "生成器表达式",
    "Dict": "字典字面量", "Set": "集合字面量",
    "JoinedStr": "f-string", "FormattedValue": "f-string",
    "NamedExpr": "海象运算符 :=", "Starred": "星号解包", "Slice": "切片",
    "Tuple": "元组（不支持多重赋值/解包）",
}


# ================================================================ 程序元信息

#: 一个 Buffer 参数：Python 名 / GLSL 名字 / 元素类型 / 绑位 / 长度 uniform / 是否只读
BufferParam = namedtuple("BufferParam", "name glsl element binding length_uniform")

#: 一个 Image 参数：Python 名 / GLSL 名字 / 元素类型 / 绑位 / 维度 / image 类型 / 格式
ImageParam = namedtuple("ImageParam", "name glsl element binding dim image_type format")

#: 一个 uniform 参数：Python 名 / GLSL 名字 / 类型
UniformParam = namedtuple("UniformParam", "name glsl type")

#: 一次翻译的产物：源码 + 运行时需要的绑位/长度信息
ShaderInfo = namedtuple("ShaderInfo",
                        "source buffers images uniforms local_size writable")


def _functionBody(fn, source=None):
    """取出被装饰函数的语句列表（AST）、"行号偏移"、以及可选的合成文件名。

    行号偏移是必要的：GLSL 翻译报错要指**源文件里的绝对行号**，
    而 ``ast.parse`` 拿到的是函数片段，行号从片段第一行算起。

    ``source`` 显式给了就**只认它、不去读文件**（打包成 Nuitka / PyInstaller 之后
    .py 不在分发里，``inspect`` 拿不到源码）：可以给一整段 ``def f(...): ...``，
    也可以只给函数体（自动包一层 def、行号从 1 算起）。
    """
    import inspect
    import textwrap

    name = getattr(fn, "__name__", "?")
    explicit = None
    if source is not None:
        text = textwrap.dedent(str(source).strip("\n"))
        if not text.lstrip().startswith(("def ", "async def ")):
            # 只给了函数体：包一层 def，行号按"用户写的那几行"算（第 1 行 = 第一行 body）
            text = "def _kernel():\n" + textwrap.indent(text, "    ") + "\n"
            offset = -1
            stored = text.split("\n")[1:-1]        # 去掉那行合成的 def
        else:
            offset = 0
            stored = text.split("\n")
        explicit = explicitSourceName(name)
        _EXPLICIT_SOURCES[explicit] = [row + "\n" for row in stored]
    else:
        try:
            lines, start = inspect.getsourcelines(fn)
        except (OSError, TypeError) as error:
            raise CompileError(_missingSource(name, fn, error))
        rows = "".join(lines).split("\n")
        indent = len(rows[0]) - len(rows[0].lstrip())
        text = "\n".join(r[indent:] if r[:indent].strip() == "" else r for r in rows)
        offset = start - 1
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise CompileError("%s(): 解析函数源码失败（%s）" % (name, error),
                           filename=explicit)
    node = tree.body[0] if tree.body else None
    if isinstance(node, ast.AsyncFunctionDef):
        raise CompileError("%s(): 不支持 async def" % name, filename=explicit)
    if not isinstance(node, ast.FunctionDef):
        raise CompileError("%s(): @glCompute 只能装饰 def 定义的函数" % name,
                           filename=explicit)
    return node.body, offset, explicit


def _missingSource(name, fn, error):
    """取不到源码时的人话 —— 打包之后最常见（Nuitka / PyInstaller 不发行 .py）。"""
    compiled = None
    try:
        compiled = fn.__globals__.get("__compiled__")
    except Exception:                          # pragma: no cover - 奇怪的函数对象
        compiled = None
    hints = ("@glCompute 要在翻译期读函数源码（翻成 GLSL 用），取不到就没法编译。三种解法：\n"
             "  1) @glCompute(source=\"...\")：把函数体直接写成字符串，不依赖任何文件（最稳）；\n"
             "  2) 打包时把 .py 一起发出去：Nuitka 加 "
             "--include-data-files=\"./Game.py=Game.py\"，Nuitka 会把 co_filename 指到\n"
             "     exe 目录下的这个路径 —— 实测放进去 inspect 就能读到源码；\n"
             "  3) 别用 def 写 kernel 体，改用 (1) 的字符串（(2) 只是让旧代码直接能跑的权宜之计）。")
    if compiled is not None:
        hints = ("检测到 Nuitka 编译（__compiled__）：编译后的 co_code 是空的、只剩行号表，\n"
                 "源码没法从 code object 里还原，必须显式给。\n" + hints)
    return "%s(): 取不到函数源码（%s）。\n%s" % (name, error, hints)


# ==================================================================== 翻译器


def _dottedName(func):
    """``ast.Name`` / ``ast.Attribute`` -> 点分名字符串；其它返回 None。"""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return "%s.%s" % (func.value.id, func.attr)
    return None


def _ascii(text, fallback="x"):
    """GLSL 块名只留 ASCII（变量名另有改名逻辑）。"""
    out = "".join(c if (c.isalnum() and c.isascii()) or c == "_" else "_" for c in str(text))
    if not out or out[0].isdigit():
        out = fallback + "_" + out
    return out


class _Var(object):
    """一个在 GLSL 里有名字的东西：局部变量 / Buffer 参数 / Image 参数 / uniform。"""

    __slots__ = ("name", "type", "kind", "length", "pending")

    def __init__(self, name, type, kind="local", length=None, pending=False) -> None:
        self.name = name        # GLSL 里的名字
        self.type = type        # GType（Buffer/Image 参数 = 元素类型）
        self.kind = kind        # "local" | "buffer" | "image" | "uniform"
        self.length = length    # Buffer 参数的长度 uniform 名
        self.pending = pending  # 局部数组：等第二遍在首次赋值处写声明

    @property
    def buf(self):
        """是不是 Buffer 参数（可以下标访问）。"""
        return self.kind == "buffer"

    @property
    def uniform(self):
        """是不是 uniform（只读）。"""
        return self.kind == "uniform"


class _Decl(object):
    """局部变量声明表条目。"""

    __slots__ = ("name", "type", "hoist")

    def __init__(self, name, type, hoist) -> None:
        self.name = name
        self.type = type
        self.hoist = hoist      # True = 提到 main 顶部声明（标量/向量/矩阵）


def imageFormat(element):
    """元素类型 -> GLSL image 格式限定符（vec3 用 rgba32f 装，读只看 .rgb）。"""
    comps = 1 if element.comps == 1 else (2 if element.comps == 2 else 4)
    suffix = {"float": "f", "int": "i", "uint": "ui"}[element.base]
    return {1: "r32", 2: "rg32", 4: "rgba32"}[comps] + suffix


def imageTypeName(element, dim=2):
    """元素类型 + 维度 -> GLSL image 类型名（image2D / iimage3D / uimage2D……）。"""
    prefix = {"float": "", "int": "i", "uint": "u"}[element.base]
    return "%simage%dD" % (prefix, 3 if int(dim) == 3 else 2)



class GlslTranslator(object):
    """一次翻译的全部状态。"""

    def __init__(self, fn, params, local_size=(64, 1, 1), image_specs=None,
                 source=None) -> None:
        self.fn = fn
        self.func_name = getattr(fn, "__name__", "glCompute")
        self.filename = getattr(getattr(fn, "__code__", None), "co_filename", None)
        self.params = list(params)
        self.local_size = tuple(local_size)
        self.body, self.line_offset, explicit = _functionBody(fn, source)
        if explicit is not None:
            # 源码是显式给的（打包之后没有 .py）：报错指这份源码，而不是不存在的路径
            self.filename = explicit
        self.image_specs = dict(image_specs or {})

        self.dry = False          # 干跑：只推导、不产出代码
        self.lines = []
        self.indent = 1
        self.loop_depth = 0

        self.taken = set()        # 已占用的 GLSL 名字
        self.name_map = {}        # Python 名 -> GLSL 名（两遍之间稳定）
        self.declared = {}        # Python 名 -> _Decl（第一遍收集）
        self.vars = {}            # Python 名 -> _Var
        self.return_index = 0     # 生成的临时变量编号
        self.writable = set()     # 被写过的 Buffer 参数（其余加 readonly）
        self.atomic_buffers = set()
        self.uses_atomic = False
        self.buffers = []         # [BufferParam]（按绑位顺序）
        self.images = []          # [ImageParam]
        self.uniforms = []        # [UniformParam]
        self._infos = {}          # Python 名 -> BufferParam / ImageParam / UniformParam
        self.image_arity = {}     # Image 参数被下标时的分量数（推断 2D/3D 用）

        # 先把生成的名字占掉，再把参数登记进去（名字不能撞）
        self.taken.update(("u_count", "u_width", "u_height", "u_depth", "pc_ret",
                           "pc_tmp", "image_size"))
        for p in self.params:
            glsl = self._localName(p.name)
            if p.kind == "buffer":
                length = self._localName("u_len_%s" % _ascii(p.name, "buf"))
                info = BufferParam(p.name, glsl, p.type, len(self.buffers), length)
                self.buffers.append(info)
            elif p.kind == "image":
                info = None                       # 等 _prescan 看清下标是 2 个还是 3 个
            else:
                info = UniformParam(p.name, glsl, p.type)
                self.uniforms.append(info)
            if info is not None:
                self._infos[p.name] = info

    def _buildImageParams(self) -> None:
        """建 Image 参数信息：维度优先用运行时给的 spec，否则按函数里的下标个数推。"""
        for p in self.params:
            if p.kind != "image" or p.name in self._infos:
                continue
            dim, image_type, fmt = self.image_specs.get(p.name, (None, None, None))
            if dim is None:
                dim = self.image_arity.get(p.name, 2)
                if dim not in (2, 3):
                    dim = 2       # 1 个下标之类：按 2D 报"要几个下标"，不去造 image1D
            if image_type is None:
                image_type = imageTypeName(p.type, dim)
            if fmt is None:
                fmt = imageFormat(p.type)
            info = ImageParam(p.name, self.name_map[p.name], p.type, len(self.images),
                              int(dim), image_type, fmt)
            self.images.append(info)
            self._infos[p.name] = info


    # --------------------------------------------------------------- 入口
    def run(self):
        self._prescan()
        self._buildImageParams()
        # 第一遍：类型推导 + 收集局部变量表
        self.dry = True
        self._reset()
        self._emitBody()
        # 第二遍：出代码
        self.dry = False
        self._reset(keep=True)
        self._emitBody()
        return ShaderInfo(source=self._source(),
                          buffers=tuple(self.buffers),
                          images=tuple(self.images),
                          uniforms=tuple(self.uniforms),
                          local_size=self.local_size,
                          writable=frozenset(self.writable))

    def _reset(self, keep=False) -> None:
        self.lines = []
        self.indent = 1
        self.loop_depth = 0
        self.vars = {}
        self.return_index = 0
        if not keep:
            self.declared = {}
            self.name_map = {}
        for param in self.params:
            self._declareParam(param)
        if not self.dry:
            for py, decl in self.declared.items():   # 第一遍收集到的局部变量
                self.vars[py] = _Var(decl.name, decl.type, pending=not decl.hoist)

    def _declareParam(self, p) -> None:
        if p.kind == "uniform":
            self.vars[p.name] = _Var(p.name, p.type, kind="uniform")
        elif p.kind == "image":
            info = self._infos[p.name]
            self.vars[p.name] = _Var(info.glsl, p.type, kind="image")
        else:
            info = self._infos[p.name]
            self.vars[p.name] = _Var(info.glsl, p.type, kind="buffer",
                                     length=info.length_uniform)

    def _emitBody(self) -> None:
        for node in self.body:
            self._stmt(node)

    def _prescan(self) -> None:
        """先扫一遍：哪些 Buffer 被写过（其余加 readonly）、谁参与 atomic、image 几维。"""
        names = set(p.name for p in self.params)
        image_names = set(p.name for p in self.params if p.kind == "image")
        for node in ast.walk(ast.Module(body=self.body, type_ignores=[])):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self._scanStore(target, names, False)
            elif isinstance(node, ast.AugAssign):
                self._scanStore(node.target, names, False)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self._scanStore(node.target, names, False)
            elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                    and node.value.id in image_names:
                # 先看清 Image 参数是被 2 下标（2D）还是 3 下标（3D）用的
                if isinstance(node.slice, ast.Tuple):
                    self.image_arity[node.value.id] = len(node.slice.elts)
                else:
                    self.image_arity.setdefault(node.value.id, 1)
            elif isinstance(node, ast.Call):
                name = _dottedName(node.func)
                short = name.rsplit(".", 1)[-1] if name else None
                if short in _ATOMIC_FUNCS:
                    self.uses_atomic = True
                    if node.args:
                        self._scanStore(node.args[0], names, True)
                elif short in ("image_store", "imageStore") and node.args:
                    # image_store(img, ...) 也算写过 img
                    first = node.args[0]
                    if isinstance(first, ast.Name) and first.id in names:
                        self.writable.add(first.id)

    def _scanStore(self, target, names, atomic) -> None:
        """判断这次写入有没有落到某个 Buffer / Image 参数上。

        要认到底：``a[i] = ...`` / ``a[i][j] = ...`` / ``a[i].x = ...`` /
        ``atomicAdd(a[i], ...)`` 都算"写过 a"（只被读的才加 readonly）。
        """
        node = target
        while isinstance(node, (ast.Subscript, ast.Attribute)):
            node = node.value
        if isinstance(node, ast.Name) and node.id in names:
            self.writable.add(node.id)
            if atomic:
                self.atomic_buffers.add(node.id)

    # ------------------------------------------------------------- 命名
    def _localName(self, py):
        """给一个 Python 名分配 GLSL 名（两遍之间稳定）。"""
        known = self.name_map.get(py)
        if known is not None:
            return known
        base = py
        if not (py.isidentifier() and py.isascii()) or py in _GLSL_RESERVED \
                or py in self.taken or py.startswith("gl_"):
            base = "v_" + _ascii(py)
        name = base
        index = 1
        while name in self.taken:
            name = "%s_%d" % (base, index)
            index += 1
        self.taken.add(name)
        self.name_map[py] = name
        return name

    def _emit(self, text) -> None:
        if self.dry:
            return
        self.lines.append("    " * self.indent + text)

    # --------------------------------------------------------- 源码装配
    def _source(self):
        lx, ly, lz = self.local_size
        out = ["#version 430",
               "// auto-generated by @glCompute: %s" % _ascii(self.func_name),
               "layout(local_size_x = %d, local_size_y = %d, local_size_z = %d) in;"
               % (lx, ly, lz), ""]
        for info in self.buffers:
            qual = ""
            if info.name in self.atomic_buffers:
                qual += "coherent "
            if info.name not in self.writable:
                qual += "readonly "
            out.append("layout(std430, binding = %d) %sbuffer Buf_%s { %s %s[]; };"
                       % (info.binding, qual, _ascii(info.name, "b%d" % info.binding),
                          BUFFER_BASE[info.element.base], info.glsl))
        for info in self.images:
            out.append("layout(%s, binding = %d) uniform %s %s;"
                       % (info.format, info.binding, info.image_type, info.glsl))
        out.append("")
        for info in self.uniforms:
            out.append("uniform %s %s;" % (info.type.glsl, info.glsl))
        for info in self.buffers:
            out.append("uniform int %s;" % info.length_uniform)
        out.append("uniform int u_width;")
        out.append("uniform int u_height;")
        out.append("uniform int u_depth;")
        out.append("uniform int u_count;")
        out.append("")
        out.append("void main() {")
        for decl in self.declared.values():     # 局部变量统一在 main 顶部声明
            if decl.hoist:
                out.append("    %s;" % decl.type.decl(decl.name))
        out.extend(self.lines)
        out.append("}")
        return "\n".join(out) + "\n"

    # --------------------------------------------------- Buffer / Image 访存
    def _intIndex(self, node, what="下标"):
        """一个下标表达式 -> 代码（必须是 int/uint 标量）。"""
        e = self._expr(node)
        if not (e.type.is_scalar and e.type.is_int):
            self._fail(node, "%s 必须是 int/uint 标量" % what)
        return e.code

    def _bufferIndex(self, node):
        """``a[i]`` -> 元素下标代码；``a[y, x]`` -> ``y * width + x``；``a[z, y, x]`` 同理。"""
        parts = None
        if isinstance(node.slice, ast.Tuple):
            parts = [self._intIndex(item) for item in node.slice.elts]
        if parts is None:
            return self._intIndex(node.slice)
        if len(parts) == 2:
            return "((%s) * u_width + (%s))" % (parts[0], parts[1])
        if len(parts) == 3:
            return "(((%s) * u_height + (%s)) * u_width + (%s))" % tuple(parts)
        self._fail(node, "Buffer 下标最多 3 个分量（`a[i]` / `a[y, x]` / `a[z, y, x]`）")

    def _bufferAccess(self, field, index, comps, k):
        if comps == 1:
            return "%s[%s]" % (field, index)
        return "%s[(%s) * %d + %d]" % (field, index, comps, k)

    def _bufferRead(self, var, node):
        """``a[i]`` 读 -> ``(代码, 元素类型)``（向量/矩阵现场拼出来）。"""
        index = self._bufferIndex(node)
        t = var.type
        if t.is_scalar:
            access = self._bufferAccess(var.name, index, 1, 0)
            if t.base == "bool":
                return E("(%s != 0)" % access, BOOL)
            return E(access, t)
        if t.is_vec:
            args = ", ".join(self._bufferAccess(var.name, index, t.comps, i)
                             for i in range(t.comps))
            return E("%s(%s)" % (t.glsl, args), t)
        cols = []                       # 矩阵：列优先，一列一个 vec
        for c in range(t.cols):
            args = ", ".join(self._bufferAccess(var.name, index, t.comps, c * t.rows + r)
                             for r in range(t.rows))
            cols.append("%s(%s)" % (vecType(t.base, t.rows).glsl, args))
        return E("%s(%s)" % (t.glsl, ", ".join(cols)), t)

    def _bufferWrite(self, node, var, value_node, value_code, value_type, what="赋值") -> None:
        """``a[i] = v``：向量/矩阵摊平成逐分量写。"""
        t = var.type
        index = self._bufferIndex(node)
        target = SCALARS[BUFFER_BASE[t.base]] if t.is_scalar else t
        code = self._coerce(value_node, value_code, value_type, target,
                            "%s 给 `%s[...]`" % (what, var.name))
        if t.is_scalar:
            self._emit("%s = %s;" % (self._bufferAccess(var.name, index, 1, 0), code))
            return
        tmp = self._tempName()
        self._emit("%s %s = %s;" % (t.glsl, tmp, code))
        for k in range(t.comps):
            if t.is_vec:
                source = "%s.%s" % (tmp, "xyzw"[k])
            else:
                source = "%s[%d][%d]" % (tmp, k // t.rows, k % t.rows)
            self._emit("%s = %s;" % (self._bufferAccess(var.name, index, t.comps, k),
                                     source))

    def _imageCoordArgs(self, node, info, arg_nodes):
        """一组下标表达式 -> ``ivec2(...)`` / ``ivec3(...)`` 坐标。

        **Image 的下标顺序就是 GLSL 的坐标顺序（x 在前）**：``img[x, y]`` /
        ``vol[x, y, z]``，和 ``image_load(img, x, y)`` / ``image_store(img, x, y, v)``
        完全一致。（``Buffer`` 是另一回事：它是一块扁内存，``a[y, x]`` 按 numpy
        的行优先算，见文档里的"下标顺序"表。）
        """
        parts = [self._intIndex(item) for item in arg_nodes]
        want = 3 if info.dim == 3 else 2
        if len(parts) != want:
            self._fail(node, "`%s` 是 %s（%d 维），要 %d 个下标：`%s[x, y%s]`"
                             % (info.name, info.image_type, info.dim, want, info.name,
                                ", z" if want == 3 else ""))
        return "%s(%s)" % ("ivec3" if want == 3 else "ivec2", ", ".join(parts))

    def _imageCoord(self, node, info):
        """``img[x, y]`` / ``img[x, y, z]`` -> 坐标代码。"""
        args = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) \
            else [node.slice]
        return self._imageCoordArgs(node, info, args)

    def _imageRead(self, info, coord):
        load = "imageLoad(%s, %s)" % (info.glsl, coord)
        t = info.element
        if t.is_scalar:
            return E("%s.x" % load, t)
        return E("%s.%s" % (load, "xyzw"[:t.comps]), t)

    def _imageStoreCode(self, info, coord, value_node, value_code, value_type):
        """``imageStore(...)`` 的调用文本（下标赋值和显式 image_store() 共用）。"""
        t = info.element
        code = self._coerce(value_node, value_code, value_type, t,
                            "写 `%s[...]`" % info.name)
        if t.comps == 4:
            packed = code
        else:
            prefix = {"float": "vec4", "int": "ivec4", "uint": "uvec4"}[t.base]
            zero = {"float": "0.0", "int": "0", "uint": "0u"}[t.base]
            packed = "%s(%s)" % (prefix, ", ".join([code] + [zero] * (4 - t.comps)))
        return "imageStore(%s, %s, %s)" % (info.glsl, coord, packed)



    def _fail(self, node, message, hint=None) -> None:
        if hint:
            message = "%s\n   提示：%s" % (message, hint)
        raise CompileError(message, node=node, filename=self.filename,
                           func=self.func_name, line_offset=self.line_offset)

    # --------------------------------------------------------------- 语句
    def _stmt(self, node) -> None:
        handler = getattr(self, "_st_" + node.__class__.__name__, None)
        if handler is None:
            what = _UNSUPPORTED.get(node.__class__.__name__)
            if what is None:
                what = "该语法（%s）" % node.__class__.__name__
            self._fail(node, "不支持 %s" % what)
        handler(node)

    def _indented(self, body) -> None:
        self.indent += 1
        try:
            for node in body:
                self._stmt(node)
        finally:
            self.indent -= 1

    def _cond(self, node):
        """条件表达式必须是 bool。"""
        e = self._expr(node)
        if not e.type.is_bool:
            self._fail(node, "条件必须是 bool（GLSL 没有 Python 那种隐式真值判断），"
                             "请写成显式比较，例如 `x != 0.0`")
        return e.code

    def _tempName(self):
        name = "pc_ret%d" % self.return_index
        self.return_index += 1
        return name

    def _st_Pass(self, node) -> None:
        pass

    def _st_Expr(self, node) -> None:
        if isinstance(node.value, ast.Constant):
            return                              # 文档字符串之类，忽略
        if not isinstance(node.value, ast.Call):
            return                              # 纯表达式语句没有副作用，忽略
        self._emit("%s;" % self._expr(node.value).code)

    def _st_Break(self, node) -> None:
        if not self.loop_depth:
            self._fail(node, "break 只能用在 for/while 循环里")
        self._emit("break;")

    def _st_Continue(self, node) -> None:
        if not self.loop_depth:
            self._fail(node, "continue 只能用在 for/while 循环里")
        self._emit("continue;")

    def _st_If(self, node) -> None:
        orelse = node.orelse
        self._emit("if (%s) {" % self._cond(node.test))
        self._indented(node.body)
        while orelse:
            if len(orelse) == 1 and isinstance(orelse[0], ast.If):
                inner = orelse[0]
                self._emit("} else if (%s) {" % self._cond(inner.test))
                self._indented(inner.body)
                orelse = inner.orelse
                continue
            self._emit("} else {")
            self._indented(orelse)
            orelse = []
        self._emit("}")

    def _st_While(self, node) -> None:
        if node.orelse:
            self._fail(node, "while 的 else 分支不支持")
        self._emit("while (%s) {" % self._cond(node.test))
        self.loop_depth += 1
        self._indented(node.body)
        self.loop_depth -= 1
        self._emit("}")

    def _st_For(self, node) -> None:
        if node.orelse:
            self._fail(node, "for 的 else 分支不支持")
        if not isinstance(node.target, ast.Name):
            self._fail(node, "for 的循环变量必须是单个名字")
        call = node.iter
        if not isinstance(call, ast.Call) or _dottedName(call.func) != "range":
            self._fail(node, "只支持 `for i in range(...)`",
                       "GLSL compute 里没有容器的概念，要遍历数组请用 "
                       "`for i in range(n)` 自己控制下标")
        args = [self._expr(a) for a in call.args]
        if not 1 <= len(args) <= 3:
            self._fail(node, "range() 需要 1~3 个参数")
        for a in args:
            if not (a.type.is_scalar and a.type.is_int):
                self._fail(node, "range() 的参数必须是 int/uint 标量")
        if len(args) == 1:
            start, stop = "0", args[0].code
        else:
            start, stop = args[0].code, args[1].code
        step_node = call.args[2] if len(args) == 3 else None
        compare, inc = "<", "+= 1"
        if step_node is not None:
            # 注意：`-1` 在 AST 里是 UnaryOp(USub)，不是负的 Constant
            step_value = self._constIndex(step_node)
            if step_value is not None and step_value < 0:
                compare, inc = ">", "-= %d" % -step_value
            elif step_value is not None:
                compare, inc = "<", "+= %d" % step_value
            else:                               # 动态步长：按正步长处理（见文档）
                inc = "+= %s" % args[2].code
        name = self._localName(node.target.id)
        previous = self.vars.get(node.target.id)
        self.vars[node.target.id] = _Var(name, INT)
        self._emit("for (int %s = %s; %s %s %s; %s %s) {"
                   % (name, start, name, compare, stop, name, inc))
        self.loop_depth += 1
        self._indented(node.body)
        self.loop_depth -= 1
        self._emit("}")
        if previous is None:                    # 循环变量出了循环就不认识了
            self.vars.pop(node.target.id, None)
        else:
            self.vars[node.target.id] = previous

    # ----------------------------------------------------------- 类型工具
    @staticmethod
    def _implicit(src, dst):
        """GLSL 允许的隐式转换（整数可以自动升成 uint/float，反过来不行）。"""
        return src in ("int", "uint") and dst in ("uint", "float")

    def _floatType(self, t):
        if t.is_mat:
            return t
        return FLOAT if t.is_scalar else vecType("float", t.comps)

    def _floatCode(self, code, t):
        """整数表达式显式转浮点：GLSL 会优先选 int 重载（int/int 会截断）。"""
        if t.base in ("int", "uint"):
            return "%s(%s)" % (self._floatType(t).glsl, code)
        return code

    def _commonType(self, node, a, b, what):
        """把两个类型统一成能同时容纳它们的类型。"""
        for t in (a, b):
            if t.is_array:
                self._fail(node, "%s：数组不能直接参与运算，请用下标访问元素" % what)
            if t.is_void:
                self._fail(node, "%s：没有值（void）" % what)
        if a.is_bool or b.is_bool:
            if a.is_bool and b.is_bool:
                return BOOL
            self._fail(node, "%s：bool 不能和数值混用" % what)
        if a.is_mat or b.is_mat:
            self._fail(node, "%s：矩阵只能和矩阵/向量/标量相乘" % what)
        if a.is_vec and b.is_vec and a.comps != b.comps:
            self._fail(node, "%s 的向量长度不一致：%s 与 %s" % (what, a, b))
        comps = a.comps if a.comps == b.comps else max(a.comps, b.comps)
        base = a.base if _BASE_RANK[a.base] >= _BASE_RANK[b.base] else b.base
        return SCALARS[base] if comps == 1 else vecType(base, comps)

    def _coerce(self, node, code, src, dst, what):
        """必要时插一个构造器做显式转换（GLSL 隐式转换覆盖不到的场合）。"""
        if dst is None or dst.is_void:
            self._fail(node, "%s：目标类型是 void" % what)
        if src.is_void:
            self._fail(node, "%s：右边没有值（void）" % what)
        if src.is_array or dst.is_array:
            self._fail(node, "%s：GLSL 的数组不能整体赋值，请逐个元素赋值" % what)
        if src.is_mat != dst.is_mat or src.comps != dst.comps:
            self._fail(node, "%s 的类型不匹配：%s -> %s" % (what, src, dst))
        if src.base == dst.base or self._implicit(src.base, dst.base):
            return code
        return "%s(%s)" % (dst.glsl, code)

    # --------------------------------------------------------------- return
    def _st_Return(self, node) -> None:
        value = node.value
        if value is not None and not (isinstance(value, ast.Constant)
                                      and value.value is None):
            self._fail(node, "@glCompute 的函数不能返回值：结果写进 Buffer / Image"
                             "（要算总和/最值用 glCompute.sum / .dot / .min / .max）")
        self._emit("return;")

    # --------------------------------------------------------------- 赋值
    def _st_Assign(self, node) -> None:
        for target in node.targets:
            self._assignOne(target, node.value)

    def _assignOne(self, target, value) -> None:
        if isinstance(target, ast.Name):
            self._assignName(target, value)
            return
        if isinstance(target, ast.Subscript):
            base = target.value
            if isinstance(base, ast.Name):
                var = self.vars.get(base.id)
                if var is not None and var.buf:                 # a[i] = ...
                    e = self._expr(value)
                    self._bufferWrite(target, var, value, e.code, e.type)
                    return
                if var is not None and var.kind == "image":     # img[x, y] = ...
                    info = self._infos[base.id]
                    coord = self._imageCoord(target, info)
                    e = self._expr(value)
                    self._emit("%s;" % self._imageStoreCode(info, coord, value,
                                                            e.code, e.type))
                    return
                if var is not None and var.uniform:
                    self._fail(target, "不能给 uniform 参数 `%s` 赋值" % base.id)
            if self._isRefElement(base):                        # a[i][k] = ...
                self._storeRefElement(target, base, value)
                return
            ref = self._expr(target)                            # 局部数组 / 矩阵列 / 分量
            e = self._expr(value)
            code = self._coerce(value, e.code, e.type, ref.type, "下标赋值")
            self._emit("%s = %s;" % (ref.code, code))
            return
        if isinstance(target, ast.Attribute):
            if self._isRefElement(target.value):                # a[i].x = ...
                self._storeRefElement(target, target.value, value)
                return
            name, base_type = self._lvalue(target.value, "swizzle 赋值")
            result = self._swizzle(target, name, base_type, target.attr)
            e = self._expr(value)
            code = self._coerce(value, e.code, e.type, result.type, "swizzle 赋值")
            self._emit("%s = %s;" % (result.code, code))
            return
        what = _UNSUPPORTED.get(target.__class__.__name__)
        self._fail(target, "不支持的赋值目标%s" % ("（%s）" % what if what else ""))

    def _isRefElement(self, node):
        """``node`` 是不是"Buffer 参数的一个元素"（``a[i]``）。"""
        return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in self.vars and self.vars[node.value.id].buf)

    def _storeRefElement(self, target, element, value) -> None:
        """``a[i][k] = v`` / ``a[i].x = v``：读元素 -> 改局部 -> 整体写回。

        ``a[i]`` 在 GLSL 里是用分量拼出来的**右值**，不能直接当左值，
        所以统一"拷进临时变量、改、再写回"。
        """
        var = self.vars[element.value.id]
        current = self._expr(element)
        tmp = self._tempName()
        self._emit("%s %s = %s;" % (current.type.glsl, tmp, current.code))
        key = "@" + tmp              # 临时变量在 vars 里的键（不是合法标识符，不会撞）
        self.vars[key] = _Var(tmp, current.type)
        name = ast.Name(id=key, ctx=ast.Load())
        if isinstance(target, ast.Attribute):
            inner = ast.Attribute(value=name, attr=target.attr, ctx=ast.Store())
        else:
            inner = ast.Subscript(value=name, slice=target.slice, ctx=ast.Store())
        ref = self._expr(ast.copy_location(inner, target))
        e = self._expr(value)
        code = self._coerce(value, e.code, e.type, ref.type, "下标赋值")
        self._emit("%s = %s;" % (ref.code, code))
        write = ast.copy_location(
            ast.Subscript(value=element.value, slice=element.slice, ctx=ast.Store()),
            element)
        self._bufferWrite(write, var, element, tmp, current.type)

    def _assignName(self, target, value) -> None:
        py = target.id
        var = self.vars.get(py)
        if var is not None:
            if var.pending:                     # 局部数组：补上带初始化的声明
                gtype, init = self._arrayValue(value, var.type.elem)
                self._emit("%s = %s;" % (var.type.decl(var.name), init))
                var.pending = False
                if str(gtype) != str(var.type):     # pragma: no cover - 第一遍已校验
                    self._fail(value, "数组类型与推导结果不一致")
                return
            if var.type.is_array:
                self._fail(target, "局部数组（`%s`）不支持整体赋值，请逐个元素赋值"
                                   "（GLSL 的数组不可赋值）" % py)
            if var.buf:
                self._fail(target, "`%s` 是 Buffer 参数，要写成 `%s[i] = ...`" % (py, py))
            if var.kind == "image":
                self._fail(target, "`%s` 是 Image 参数，要写成 `%s[x, y] = ...`" % (py, py))
            if var.uniform:
                self._fail(target, "不能给 uniform 参数 `%s` 赋值"
                                   "（它由调用方通过 GPU 常量传入）" % py)
            e = self._expr(value)
            code = self._coerce(value, e.code, e.type, var.type, "给 `%s` 赋值" % py)
            self._emit("%s = %s;" % (var.name, code))
            return
        if self._isArrayValue(value):            # 新局部数组
            gtype, init = self._arrayValue(value, None)
            name = self._localName(py)
            self.declared[py] = _Decl(name, gtype, hoist=False)
            self.vars[py] = _Var(name, gtype)
            self._emit("%s = %s;" % (gtype.decl(name), init))
            return
        e = self._expr(value)                    # 新局部变量：第一遍登记，第二遍顶部已声明
        if e.type.is_void:
            self._fail(value, "右边没有值（void），不能赋给 `%s`" % py)
        if e.type.is_array:
            self._fail(value, "右边是数组，GLSL 里数组不能赋给变量")
        name = self._localName(py)
        self.declared[py] = _Decl(name, e.type, hoist=True)
        self.vars[py] = _Var(name, e.type)

    def _lvalue(self, node, what):
        """取一个可写目标的 (GLSL 代码, 类型)。"""
        if isinstance(node, ast.Name):
            var = self.vars.get(node.id)
            if var is None:
                self._fail(node, "%s：`%s` 还没有定义" % (what, node.id))
            if var.uniform:
                self._fail(node, "%s：不能写 uniform 参数 `%s`" % (what, node.id))
            if var.buf:
                self._fail(node, "%s：`%s` 是 Buffer 参数，要写成 `%s[i]`"
                                 % (what, node.id, node.id))
            if var.kind == "image":
                self._fail(node, "%s：`%s` 是 Image 参数，要写成 `%s[x, y]`"
                                 % (what, node.id, node.id))
            if var.type.is_array:
                self._fail(node, "%s：`%s` 是数组，不能整体赋值" % (what, node.id))
            return var.name, var.type
        if isinstance(node, ast.Subscript):
            e = self._expr(node)
            return e.code, e.type
        self._fail(node, "%s 的目标只能是变量、数组元素或 swizzle" % what)

    def _st_AnnAssign(self, node) -> None:
        if not isinstance(node.target, ast.Name):
            self._fail(node, "带类型标注的赋值只支持 `名字: 类型 = 值`")
        py = node.target.id
        ann = resolveType(node.annotation, "（变量 `%s`）" % py)
        if ann is None:
            self._fail(node, "变量 `%s` 的类型标注不能是 None" % py)
        var = self.vars.get(py)
        if node.value is None:                   # 只声明
            if var is None:
                name = self._localName(py)
                self.declared[py] = _Decl(name, ann, hoist=True)
                self.vars[py] = _Var(name, ann)
            return
        e = self._expr(node.value)
        code = self._coerce(node.value, e.code, e.type, ann, "给 `%s` 赋值" % py)
        if var is None:
            name = self._localName(py)
            self.declared[py] = _Decl(name, ann, hoist=True)
            self.vars[py] = _Var(name, ann)
            self._emit("%s = %s;" % (name, code))
            return
        if var.uniform:
            self._fail(node, "不能给 uniform 参数 `%s` 赋值" % py)
        if var.buf or var.kind == "image":
            self._fail(node, "`%s` 是 %s 参数，要写成 `%s[...] = ...`"
                             % (py, var.kind, py))
        self._emit("%s = %s;" % (var.name, code))

    def _st_AugAssign(self, node) -> None:
        target = node.target
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            var = self.vars.get(target.value.id)
            if var is not None and var.buf:                 # a[i] += ...（读-算-写回）
                current = self._bufferRead(var, target)
                e = self._expr(node.value)
                result = self._binary(node, node.op, current.code, current.type,
                                      e.code, e.type)
                write = ast.copy_location(
                    ast.Subscript(value=target.value, slice=target.slice,
                                  ctx=ast.Store()), target)
                self._bufferWrite(write, var, node.value, result[0], result[1],
                                  what="复合赋值")
                return
            if var is not None and var.kind == "image":     # img[x, y] += ...
                info = self._infos[target.value.id]
                coord = self._imageCoord(target, info)
                current = self._imageRead(info, coord)
                e = self._expr(node.value)
                result = self._binary(node, node.op, current.code, current.type,
                                      e.code, e.type)
                self._emit("%s;" % self._imageStoreCode(info, coord, node.value,
                                                        result[0], result[1]))
                return
        if isinstance(node.op, ast.Div) and isinstance(node.target, ast.Name):
            var = self.vars.get(node.target.id)
            if var is not None and var.type.is_int:
                self._fail(node, "int/uint 的 `/=` 在 GLSL 里是整数除法，与 Python 的 "
                                 "`/=` 语义不同；请写成 `x = float(x) / n` "
                                 "或把变量改成 float")
        code, gtype = self._lvalue(node.target, "复合赋值")
        e = self._expr(node.value)
        result = self._binary(node, node.op, code, gtype, e.code, e.type)
        final = self._coerce(node, result[0], result[1], gtype, "复合赋值")
        self._emit("%s = %s;" % (code, final))

    # ----------------------------------------------------------- 局部数组
    @staticmethod
    def _isArrayValue(node):
        if isinstance(node, ast.List):
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            return isinstance(node.left, ast.List) or isinstance(node.right, ast.List)
        return False

    def _constInt(self, node, what):
        value = node
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            value = node.operand
            sign = -1
        else:
            sign = 1
        if isinstance(value, ast.Constant) and isinstance(value.value, int) \
                and not isinstance(value.value, bool):
            return sign * value.value
        self._fail(node, "%s 必须是常量整数" % what)

    def _arrayValue(self, node, want=None):
        """局部数组字面量 -> (GType, GLSL 初始化文本)。"""
        if isinstance(node, ast.List):
            if not node.elts:
                self._fail(node, "空列表推不出类型，请写出元素（如 [0.0, 0.0]）")
            codes = []
            elem = want
            for item in node.elts:
                item_type, text = self._arrayItem(item, elem)
                if elem is None:
                    elem = item_type
                elif str(item_type) != str(elem):
                    self._fail(item, "数组元素类型不一致：%s 与 %s" % (elem, item_type))
                codes.append(text)
            result = arrayType(elem, len(codes))
            return result, "%s(%s)" % (result.ctor(), ", ".join(codes))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            left, right = node.left, node.right
            if not isinstance(left, ast.List):
                left, right = right, left
            if not isinstance(left, ast.List):
                self._fail(node, "这里不是数组字面量")
            if len(left.elts) != 1:
                self._fail(node, "数组乘法初始化要写成 `[值] * N`（单个元素）")
            count = self._constInt(right, "`[值] * N` 的 N")
            if count < 0:
                self._fail(node, "数组长度不能是负数")
            elem, text = self._arrayItem(left.elts[0], want)
            result = arrayType(elem, count)
            return result, "%s(%s)" % (result.ctor(), ", ".join([text] * count))
        self._fail(node, "局部数组只能用列表字面量或 `[值] * N` 初始化"
                         "（GLSL 的局部数组长度必须是编译期常量）")

    def _arrayItem(self, node, want):
        if isinstance(node, ast.List):
            inner = want.elem if (want is not None and want.is_array) else None
            return self._arrayValue(node, inner)
        if want is not None and want.is_array:
            self._fail(node, "数组的维度不一致（这一层应该是数组）")
        e = self._expr(node)
        if e.type.is_void:
            self._fail(node, "这里没有值（void）")
        if e.type.is_array:
            self._fail(node, "数组不能嵌套在表达式里，请直接用列表字面量")
        return e.type, e.code

    # --------------------------------------------------------------- 表达式
    def _expr(self, node):
        handler = getattr(self, "_ex_" + node.__class__.__name__, None)
        if handler is None:
            if isinstance(node, (ast.List, ast.Tuple)):
                self._fail(node, "列表/元组字面量只能用于局部数组初始化"
                                 "（如 `a = [0.0] * 4`）")
            what = _UNSUPPORTED.get(node.__class__.__name__)
            if what is None:
                what = "该语法（%s）" % node.__class__.__name__
            self._fail(node, "不支持 %s" % what)
        return handler(node)

    def _ex_Constant(self, node):
        value = node.value
        if value is True:
            return E("true", BOOL)
        if value is False:
            return E("false", BOOL)
        if isinstance(value, int):
            if -2147483648 <= value <= 2147483647:
                return E(str(value), INT)
            if 0 <= value <= 4294967295:
                return E("%du" % value, UINT)
            self._fail(node, "整数 %s 超出 GLSL 的 int/uint 范围"
                             "（GLSL 没有 Python 的任意精度整数）" % value)
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                self._fail(node, "GLSL 没有 nan/inf 字面量")
            text = repr(value)
            if "." not in text and "e" not in text and "E" not in text:
                text += ".0"
            return E(text, FLOAT)
        if value is None:
            self._fail(node, "`None` 只能出现在 `return` 里")
        self._fail(node, "不支持的字面量 %r（只支持数字 / 布尔 / None）" % (value,))

    def _ex_Name(self, node):
        name = node.id
        var = self.vars.get(name)
        if var is not None:
            if var.buf:
                self._fail(node, "`%s` 是 Buffer 参数，要下标使用（`%s[i]`）；"
                                 "元素个数用 `len(%s)`" % (name, name, name))
            if var.kind == "image":
                self._fail(node, "`%s` 是 Image 参数，要下标使用（`%s[x, y]`）"
                                 % (name, name))
            return E(var.name, var.type)
        if name in _INTRINSICS:           # 也支持"名字"写法（index / global_x / …）
            code, gtype = _INTRINSICS[name]
            return E(code, gtype)
        if name in _CONSTANTS:
            return E(_CONSTANTS[name], FLOAT)
        self._fail(node, "未定义的变量 `%s`" % name,
                   "只能用参数、函数内定义过的局部变量和内置名（函数不能当值用）")

    def _ex_BinOp(self, node):
        a = self._expr(node.left)
        b = self._expr(node.right)
        return self._binary(node, node.op, a.code, a.type, b.code, b.type)

    def _binary(self, node, op, code_a, type_a, code_b, type_b):
        symbol = _BINOPS.get(op.__class__.__name__)
        if symbol is None:
            self._fail(node, "不支持的二元运算符 %s" % op.__class__.__name__)
        for t in (type_a, type_b):
            if t.is_array:
                self._fail(node, "数组不能直接参与运算，请用下标访问元素")
            if t.is_void:
                self._fail(node, "没有值（void）不能参与运算")
            if t.is_bool:
                self._fail(node, "bool 不能参与算术/位运算")
        if symbol == "**":
            if type_a.is_mat or type_b.is_mat:
                self._fail(node, "矩阵不支持 `**`")
            want_a, want_b = self._floatType(type_a), self._floatType(type_b)
            result = self._commonType(node, want_a, want_b, "** 运算")
            return E("pow(%s, %s)" % (self._floatCode(code_a, type_a),
                                      self._floatCode(code_b, type_b)), result)
        if symbol in ("&", "|", "^"):
            if not (type_a.is_int and type_b.is_int):
                self._fail(node, "位运算 %s 只支持 int/uint" % symbol)
            result = self._commonType(node, type_a, type_b, "位运算")
            return E("(%s %s %s)" % (code_a, symbol, code_b), result)
        if symbol in ("<<", ">>"):
            if not (type_a.is_int and type_b.is_int):
                self._fail(node, "移位运算只支持 int/uint")
            return E("(%s %s %s)" % (code_a, symbol, code_b), type_a)
        if type_a.is_mat or type_b.is_mat:
            result = self._matMul(node, symbol, type_a, type_b)
            return E("(%s %s %s)" % (code_a, symbol, code_b), result)
        if symbol == "/" and type_a.is_int and type_b.is_int:
            # Python 的 / 是浮点除法（GLSL 的 int/int 会截断），显式转 float
            result = self._commonType(node, self._floatType(type_a), self._floatType(type_b),
                                      "除法")
            return E("(%s / %s)" % (self._floatCode(code_a, type_a),
                                    self._floatCode(code_b, type_b)), result)
        result = self._commonType(node, type_a, type_b, "%s 运算" % symbol)
        if symbol == "%":
            if result.base == "float":
                return E("mod(%s, %s)" % (self._floatCode(code_a, type_a),
                                          self._floatCode(code_b, type_b)), result)
            return E("(%s %% %s)" % (code_a, code_b), result)
        if symbol == "//":
            if result.base == "float":
                return E("floor(%s / %s)" % (code_a, code_b), result)
            return E("(%s / %s)" % (code_a, code_b), result)
        return E("(%s %s %s)" % (code_a, symbol, code_b), result)

    def _matMul(self, node, symbol, a, b):
        if symbol != "*":
            self._fail(node, "矩阵只支持 `*` 运算")
        if a.is_mat and b.is_mat:
            if (a.cols, a.rows) != (b.cols, b.rows):
                self._fail(node, "矩阵尺寸不匹配：%s * %s" % (a, b))
            return a
        if a.is_mat and b.is_vec:
            if b.comps != a.cols:
                self._fail(node, "mat%dx%d * %s 尺寸不匹配" % (a.cols, a.rows, b))
            return vecType("float", a.rows)
        if a.is_vec and b.is_mat:
            if a.comps != b.rows:
                self._fail(node, "%s * mat%dx%d 尺寸不匹配" % (a, b.cols, b.rows))
            return vecType("float", b.cols)
        scalar, matrix = (b, a) if a.is_mat else (a, b)
        if scalar.is_scalar and not scalar.is_bool:
            return matrix
        self._fail(node, "矩阵只能和矩阵 / 向量 / 标量相乘（%s * %s）" % (a, b))

    def _ex_UnaryOp(self, node):
        e = self._expr(node.operand)
        name = node.op.__class__.__name__
        if name == "Not":
            if not e.type.is_bool:
                self._fail(node, "`not` 需要 bool 操作数（GLSL 的 ! 不接受数值），"
                                 "请写成显式比较，例如 `not (x > 0.0)`")
            return E("(!%s)" % e.code, BOOL)
        if name == "Invert":
            if not e.type.is_int:
                self._fail(node, "`~` 只支持 int/uint")
            return E("(~%s)" % e.code, e.type)
        if name in ("USub", "UAdd"):
            if e.type.is_bool or e.type.is_void or e.type.is_array:
                self._fail(node, "这里不能用一元 +/-（%s）" % e.type)
            symbol = "-" if name == "USub" else "+"
            return E("(%s%s)" % (symbol, e.code), e.type)
        self._fail(node, "不支持的一元运算符 %s" % name)

    def _ex_BoolOp(self, node):
        parts = [self._expr(value) for value in node.values]
        for value, e in zip(node.values, parts):
            if not e.type.is_bool:
                self._fail(value, "`and`/`or` 的操作数必须是 bool（GLSL 的 && || 不接受数值），"
                                  "请写成显式比较，例如 `x != 0.0`")
        symbol = "&&" if isinstance(node.op, ast.And) else "||"
        return E("(%s)" % (" %s " % symbol).join(e.code for e in parts), BOOL)

    def _ex_Compare(self, node):
        current = self._expr(node.left)
        parts = []
        for op, comparator in zip(node.ops, node.comparators):
            right = self._expr(comparator)
            symbol = _CMPOPS.get(op.__class__.__name__)
            if symbol is None:
                self._fail(node, "不支持的比较运算 `%s`（不支持 is / in）"
                                 % op.__class__.__name__)
            for t in (current.type, right.type):
                if t.is_array or t.is_void:
                    self._fail(node, "数组/void 不能比较")
            if symbol not in ("==", "!="):
                for t in (current.type, right.type):
                    if not (t.is_scalar and not t.is_bool):
                        self._fail(node, "大小比较只支持数值标量"
                                         "（向量请用 length/dot 或逐分量比较）")
            parts.append("(%s %s %s)" % (current.code, symbol, right.code))
            current = right
        return E(" && ".join(parts), BOOL)

    def _ex_IfExp(self, node):
        cond = self._cond(node.test)
        a = self._expr(node.body)
        b = self._expr(node.orelse)
        result = self._commonType(node, a.type, b.type, "三元表达式")
        return E("(%s ? %s : %s)"
                 % (cond, self._coerce(node, a.code, a.type, result, "三元表达式"),
                    self._coerce(node, b.code, b.type, result, "三元表达式")), result)

    def _ex_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id not in self.vars \
                and node.value.id in _MODULE_ALIASES:
            self._fail(node, "`%s.%s` 只能当函数调用用（例如 `%s(x)`）"
                             % (node.value.id, node.attr, node.attr))
        e = self._expr(node.value)
        return self._swizzle(node, e.code, e.type, node.attr)

    def _swizzle(self, node, code, t, name):
        """向量分量访问（.x/.xy/.rgb……）。"""
        if t.is_array or t.is_mat or t.is_void:
            self._fail(node, "%s 没有分量可以取（只有标量/向量支持 .x/.xy/.rgb）" % t)
        for chars in ("xyzw", "rgba", "stpq"):
            if all(c in chars for c in name):
                break
        else:
            self._fail(node, "非法分量 `.%s`（不能混用不同分组，如 `.xg`）" % name)
        if len(name) > 4:
            self._fail(node, "分量选择最多 4 个字符：`.%s`" % name)
        if t.is_scalar and len(name) != 1:
            self._fail(node, "标量只能取一个分量（`.%s` 取不出来）" % name)
        for c in name:
            if chars.index(c) >= t.comps:
                self._fail(node, "`.%s` 越界：%s 只有 %d 个分量" % (name, t, t.comps))
        if len(name) == 1:
            return E("%s.%s" % (code, name), SCALARS[t.base])
        return E("%s.%s" % (code, name), vecType(t.base, len(name)))

    def _constIndex(self, node):
        """字面量下标（含 `-1`）-> int；不是常量返回 None。"""
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = self._constIndex(node.operand)
            return None if inner is None else -inner
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool):
            return node.value
        return None

    def _ex_Subscript(self, node):
        if isinstance(node.slice, ast.Slice):
            self._fail(node, "不支持切片，请用 `a[i]` 逐个取")
        base = node.value
        if isinstance(base, ast.Name):
            var = self.vars.get(base.id)
            if var is not None and var.buf:                     # Buffer 参数：a[i]
                return self._bufferRead(var, node)
            if var is not None and var.kind == "image":         # Image 参数：img[x, y]
                info = self._infos[base.id]
                return self._imageRead(info, self._imageCoord(node, info))
            if var is not None and var.uniform:
                self._fail(node, "uniform 参数 `%s` 是标量，不能下标" % base.id)
        e = self._expr(base)
        t = e.type
        if t.is_array:                                          # 局部数组
            index = self._intIndex(node.slice)
            constant = self._constIndex(node.slice)
            if constant is not None:
                if constant < 0:
                    self._fail(node.slice, "下标不能是负数（GLSL 数组不支持负下标）")
                if t.length is not None and constant >= t.length:
                    self._fail(node.slice, "下标 %d 越界：只有 %d 个元素"
                                           % (constant, t.length))
            return E("%s[%s]" % (e.code, index), t.elem)
        if t.is_mat:                                            # 矩阵取列：m[c] -> vecR
            index = self._intIndex(node.slice)
            constant = self._constIndex(node.slice)
            if constant is not None and not 0 <= constant < t.cols:
                self._fail(node.slice, "列下标 %d 越界：%s 只有 %d 列" % (constant, t, t.cols))
            return E("%s[%s]" % (e.code, index), vecType(t.base, t.rows))
        if t.is_vec:                                            # 向量取分量：v[k] -> 标量
            index = self._intIndex(node.slice)
            constant = self._constIndex(node.slice)
            if constant is not None and not 0 <= constant < t.comps:
                self._fail(node.slice, "分量下标 %d 越界：%s 只有 %d 个分量"
                                       % (constant, t, t.comps))
            return E("%s[%s]" % (e.code, index), SCALARS[t.base])
        self._fail(node, "%s 不能下标" % t)

    def _calleeName(self, node):
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in self.vars:
                self._fail(node, "`%s` 是变量，不能当函数调用" % func.id)
            return func.id
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = func.value.id
            if module not in self.vars and module in _MODULE_ALIASES:
                return func.attr
            self._fail(node, "不支持的调用形式 `%s.%s`（不支持对象方法 / 闭包）"
                             % (module, func.attr))
        self._fail(node, "不支持的调用形式（不支持函数指针 / 闭包 / 方法 / lambda）")

    def _ex_Call(self, node):
        if node.keywords:
            self._fail(node, "不支持关键字参数")
        name = self._calleeName(node)
        if name == "len":
            return self._len(node)
        if name in _IMAGE_FUNCS:
            return self._imageFunc(node, name)
        if name in _INTRINSICS:
            if node.args:
                self._fail(node, "%s() 不接受参数（线程 id / 尺寸由派发决定）" % name)
            code, gtype = _INTRINSICS[name]
            return E(code, gtype)
        if name in _SYNC_FUNCS:
            if node.args:
                self._fail(node, "%s() 不接受参数" % name)
            return E("%s()" % _SYNC_FUNCS[name], VOID)
        args = [self._expr(a) for a in node.args]
        if name in GLSL_TYPES:
            return self._construct(node, name, args)
        if name in _ATOMIC_FUNCS:
            return self._atomic(node, name, args)
        if name in _MATH_FUNCS:
            return self._math(node, name, args)
        self._fail(node, "不认识的函数 `%s`" % name,
                   "只支持 GLSL 内置数学函数、类型构造器（float/vec3/...）、"
                   "index()/global_x()/local_y() 之类线程 id 函数、"
                   "atomic*/barrier 之类；@glCompute 不会调用任意 Python 函数")

    def _imageParam(self, node):
        if not isinstance(node, ast.Name) or node.id not in self.vars \
                or self.vars[node.id].kind != "image":
            self._fail(node, "这里要写一个 Image 参数的名字（例如 `img`）")
        return self._infos[node.id]

    def _imageFunc(self, node, name):
        """``image_size/img(y,x)`` 与 ``image_load/image_store``（两种拼法都认）。"""
        if not node.args:
            self._fail(node, "%s() 需要参数" % name)
        info = self._imageParam(node.args[0])
        kind = {"image_load": "load", "imageLoad": "load",
                "image_store": "store", "imageStore": "store",
                "image_size": "size", "imageSize": "size"}[name]
        if kind == "size":
            if len(node.args) != 1:
                self._fail(node, "image_size() 只要 1 个参数")
            return E("imageSize(%s)" % info.glsl,
                     vecType("int", 3 if info.dim == 3 else 2))
        if kind == "load":
            if len(node.args) not in (2, 3):
                self._fail(node, "image_load(img, x, y[, z])")
            return self._imageRead(info,
                                   self._imageCoordArgs(node, info, node.args[1:]))
        if len(node.args) < 3:
            self._fail(node, "image_store(img, x, y[, z], value)")
        coord = self._imageCoordArgs(node, info, node.args[1:-1])
        e = self._expr(node.args[-1])
        return E(self._imageStoreCode(info, coord, node.args[-1], e.code, e.type),
                 VOID)

    def _construct(self, node, name, args):
        """类型构造器：float(x) / vec3(...) / mat3(...) ……"""
        target = GLSL_TYPES[name]
        if not args:
            self._fail(node, "%s() 至少要一个参数" % name)
        for a in args:
            if a.type.is_array:
                self._fail(node, "%s() 的参数不能是数组" % name)
            if a.type.is_void:
                self._fail(node, "%s() 的参数没有值（void）" % name)
        codes = ", ".join(a.code for a in args)
        if len(args) == 1:
            src = args[0].type
            if src.is_mat:
                if not target.is_mat or (src.cols, src.rows) != (target.cols, target.rows):
                    self._fail(node, "%s(%s) 不合法：矩阵只能同尺寸拷贝"
                                     "（取小请显式取列，如 mat3(m[0].xyz, m[1].xyz, ...)）"
                                     % (name, src))
                return E("%s(%s)" % (name, codes), target)
            if target.is_mat:
                if src.is_scalar and src.base == "float":
                    return E("%s(%s)" % (name, codes), target)     # 对角阵
                self._fail(node, "matN(...) 需要 N*N 个标量、N 个列向量或 1 个浮点标量（对角阵）")
            if src.comps == 1 or (src.comps == target.comps and target.is_vec):
                return E("%s(%s)" % (name, codes), target)
            self._fail(node, "%s(%s) 维度不匹配：%s 有 %d 个分量" % (name, name, src, src.comps))
        total = sum(1 if a.type.is_scalar else a.type.comps for a in args)
        if total != target.comps:
            self._fail(node, "%s(...) 需要 %d 个标量分量，收到 %d 个"
                             % (name, target.comps, total))
        return E("%s(%s)" % (name, codes), target)

    def _math(self, node, name, args):
        rule, allowed = _MATH_FUNCS[name]
        if len(args) not in allowed:
            self._fail(node, "%s() 需要 %s 个参数，收到 %d 个"
                             % (name, " 或 ".join(str(n) for n in allowed), len(args)))
        for a in args:
            if a.type.is_array or a.type.is_void:
                self._fail(node, "%s() 的参数类型不支持：%s" % (name, a.type))
            if a.type.is_bool:
                self._fail(node, "%s() 不接受 bool 参数" % name)
        if rule == "scalar":                # length / distance / dot -> float
            codes = [self._floatCode(a.code, a.type) for a in args]
            return E("%s(%s)" % (name, ", ".join(codes)), FLOAT)
        if rule in ("float", "cross"):      # pow / mod / cross
            codes = []
            result = None
            for a in args:
                if rule == "cross":
                    want = GLSL_TYPES["vec3"]
                    code = a.code if a.type is want else "%s(%s)" % (want.glsl, a.code)
                else:
                    want = self._floatType(a.type)
                    code = self._floatCode(a.code, a.type)
                codes.append(code)
                result = want if result is None else self._commonType(node, result, want, name)
            return E("%s(%s)" % (name, ", ".join(codes)), result)
        result = args[0].type               # rule == "same"
        for a in args[1:]:
            result = self._commonType(node, result, a.type, name)
        return E("%s(%s)" % (name, ", ".join(a.code for a in args)), result)

    def _atomic(self, node, name, args):
        need = 3 if name == "atomicCompSwap" else 2
        if len(args) != need:
            self._fail(node, "%s() 需要 %d 个参数，收到 %d 个" % (name, need, len(args)))
        first = node.args[0]
        if not isinstance(first, ast.Subscript):
            self._fail(first, "%s() 的第一个参数必须是缓冲区/数组元素（out[i] / x[i]）" % name,
                       "GLSL 的 atomic 需要一个可写的内存位置")
        target = args[0].type
        if target.is_array or target.is_void or target.is_bool:
            self._fail(first, "%s() 的目标类型不支持：%s" % (name, target))
        return E("%s(%s)" % (name, ", ".join(a.code for a in args)), target)

    def _len(self, node):
        """``len(a)``：Buffer 参数的**元素个数**（每块一个长度 uniform，运行时设置）。"""
        if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
            self._fail(node, "len() 用法：`len(Buffer 参数)` 或 `len(局部数组)`")
        py = node.args[0].id
        var = self.vars.get(py)
        if var is not None and var.buf:
            return E(var.length, INT)
        if var is not None and var.kind == "image":
            self._fail(node, "Image 参数不能用 len()；用 `image_size(%s)`（ivec2/ivec3）"
                             % py)
        if var is not None and var.type.is_array and var.type.length is not None:
            return E(str(var.type.length), INT)
        self._fail(node, "len() 只支持 Buffer 参数和长度已知的局部数组")


# ============================================================ 模块级小表

#: 表达式结果：(GLSL 代码, 类型)
E = namedtuple("E", "code type")

#: Python 运算符 -> GLSL 符号
_BINOPS = {
    "Add": "+", "Sub": "-", "Mult": "*", "Div": "/", "FloorDiv": "//",
    "Mod": "%", "Pow": "**", "BitAnd": "&", "BitOr": "|", "BitXor": "^",
    "LShift": "<<", "RShift": ">>", "MatMult": "*",
}

#: 比较运算符
_CMPOPS = {
    "Eq": "==", "NotEq": "!=", "Lt": "<", "LtE": "<=", "Gt": ">", "GtE": ">=",
}

#: 数值类型的"谁更宽"（统一类型时用）
_BASE_RANK = {"bool": 0, "int": 1, "uint": 2, "float": 3}


def translateFunction(fn, params, local_size=(64, 1, 1), image_specs=None,
                      source=None):
    """把被装饰函数翻译成 GLSL compute shader，返回 :class:`ShaderInfo`。

    :param params: :func:`analyzeSignature` 的结果（每个参数带 ``kind``）
    :param image_specs: ``{参数名: (维度, image 类型名, 格式)}``；不给就按元素类型
        默认（2D + r32f/rg32f/rgba32f/r32i/…）
    :param source: 显式给的函数源码（``@glCompute(source=...)``）；不给就读文件。
        打包（Nuitka/PyInstaller）之后必须显式给 —— 那时没有 .py 可读。
    """
    return GlslTranslator(fn, params, local_size, image_specs, source).run()
