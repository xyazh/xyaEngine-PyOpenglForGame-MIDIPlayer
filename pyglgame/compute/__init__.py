"""``pyglgame.compute``：把受限的 Python 数值函数编译成 GLSL 430 compute shader。

对外只有 **glCompute** 一个名字（既是装饰器，也是归约入口）::

    import numpy as np
    from pyglgame.compute import Buffer, Image, glCompute

    # 1) 显式下标的 kernel：线程自己算下标、结果写回 Buffer / Image
    @glCompute
    def process(a: Buffer[float], b: Buffer[float]):
        i = index()
        if i >= len(a):
            return
        a[i] = a[i] + b[i]

    process(array_a, array_b)          # CPU 数组：算完读回，返回 ndarray
    process(vbo, gl_buffer)            # 已有 GPU 对象：原地改，不读回，返回 None
    process(texture, readback=True)    # 想拿回来就 readback=True

    # 2) 归约：shared memory 组内归约 + 多轮 dispatch，最后只剩一个结果
    total = glCompute.sum(array)       # CPU 数组 -> numpy 标量
    prod = glCompute.dot(a, b)         # 等价于 sum(a[i] * b[i])
    lo = glCompute.min(array)
    hi = glCompute.max(array)
    gpu_total = glCompute.sum(gl_buffer)   # GPU 输入 -> 结果留在 GPU（GlBuffer）

关键点：

- 复用引擎当前的 OpenGL 4.3+ 上下文（不建窗口、不建新 Context）；
- ``Buffer[T]`` / ``Image[T]`` 参数 = SSBO / image，普通标量参数 = uniform；
- list / ndarray -> 临时 SSBO（或临时纹理），算完**读回 CPU 返回 ndarray**；
  已有 Buffer / VBO / Texture -> **直接绑上去原地改**，不复制、不读回；
- program 按源码缓存；``func.glsl_source`` 随时看生成的 GLSL；
- 语法不支持时抛 :class:`CompileError`（函数名 + 行号 + 源码原文），
  GLSL 编译失败会打印**带行号的完整源码** + shader log。

细节与语法范围见 ``docs/glCompute.md``。
"""

from .GlCompute import GlKernel, glCompute
from .Objects import GlBuffer, GlImage
from . import translator as _translator
# noqa 下面这行把类型标注 + kernel 内置名（global_x / image_store / barrier / PI…）都带进来：
# 内核里其实不 import 也能翻（翻译器按名字认），但 import 了 IDE 不报红、抄代码不报 NameError
from .translator import *                                                        # noqa: F401,F403

__all__ = [
    # 唯一的入口：装饰器 + 归约
    "glCompute",
    # GPU 对象句柄（包装已有 Buffer / 纹理时用）
    "GlBuffer", "GlImage",
    # 类型标注
    "Buffer", "Image", "uint", "vec2", "vec3", "vec4",
    "ivec2", "ivec3", "ivec4", "uvec2", "uvec3", "uvec4",
    "bvec2", "bvec3", "bvec4", "mat2", "mat3", "mat4",
    # 底层（一般用不到）
    "GlKernel", "CompileError", "GType", "resolveType",
]
# 再加上 kernel 里能直接用的那些内置名（global_x() / image_store() / barrier() / PI …）
__all__ += [name for name in _translator.__all__ if name not in __all__]
