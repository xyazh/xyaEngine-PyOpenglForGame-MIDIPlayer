"""GPU 归约：``glCompute.sum / .dot / .min / .max``。

做法就是"shared memory 组内归约 + 多轮 dispatch"：

    N 个元素 --(每个 workgroup 汇总一部分)--> G 个部分和
             --(同样的内核递归)--> ... --> 1 个结果

例如 1,000,000 -> 3907 -> 16 -> 1（一组 256 线程）。
**不会让所有线程往同一个 float 上写**：每个 workgroup 只有第 0 号线程写一个部分和。

- 归约内核用**模板生成**（不是 AST 翻译出来的）：树形归约需要 ``shared`` 数组，
  这是 GLSL 里少数必须手写的东西；模板放在这里，"编译/派发/读回"仍然走
  :mod:`pyglgame.compute.GlRuntime`，和 ``@glCompute`` 完全同一条路。
- CPU 数组输入 -> 返回 numpy 标量；GPU 缓冲区输入 -> 结果留在 GPU（:class:`GlBuffer`），
  可以再喂给别的 kernel，不来回拷贝。
- 向量元素按分量一起归约（得到标量总和/最值）；
  ``dot`` 等价于 ``sum(a[i] * b[i])``，之后再走普通求和。
"""

import numpy as np
from OpenGL.GL import glGetUniformLocation, glUniform1i

from . import GlRuntime as rt
from .Objects import GlBuffer, detectBuffer

__all__ = ["reduceSum", "reduceDot", "reduceMin", "reduceMax", "LOCAL_SIZE"]

#: 组内线程数（<= 256：shared 数组装得下；1e6 -> 3907 组就是它算出来的）
LOCAL_SIZE = 256

#: 每种归约的：(单位元, 一个输入时的合并表达式, 两个输入时的合并表达式, shared 合并)
_IDENTITIES = {
    ("sum", "float"): "0.0",
    ("sum", "int"): "0",
    ("sum", "uint"): "0u",
    ("min", "float"): "3.4e38",
    ("min", "int"): "2147483647",
    ("min", "uint"): "4294967295u",
    ("max", "float"): "-3.4e38",
    ("max", "int"): "-2147483648",
    ("max", "uint"): "0u",
}

_COMBINE = {
    "sum": lambda a, b: "(%s + %s)" % (a, b),
    "min": lambda a, b: "min(%s, %s)" % (a, b),
    "max": lambda a, b: "max(%s, %s)" % (a, b),
}


def baseOfDtype(dtype):
    """numpy dtype -> GLSL 标量类型。"""
    kind = np.dtype(dtype).kind
    if kind == "f":
        return "float"
    if kind == "u":
        return "uint"
    if kind == "i":
        return "int"
    raise ValueError("归约只支持 float/int/uint（bool 可以先用 int 存），收到 dtype=%s"
                     % (dtype,))


def _template(base, local, inputs, out_binding, identity, op_first, op_shared):
    """生成归约内核源码（树形归约 + 网格跨步循环）。"""
    lines = ["#version 430",
             "layout(local_size_x = %d) in;" % local,
             "layout(std430, binding = 0) readonly buffer In { %s data[]; };" % base]
    if inputs > 1:
        lines.append("layout(std430, binding = 1) readonly buffer In2 { %s data2[]; };"
                     % base)
    lines.append("layout(std430, binding = %d) buffer Out { %s sums[]; };"
                 % (out_binding, base))
    lines.append("uniform int u_count;")
    lines.append("shared %s pc_partial[%d];" % (base, local))
    lines.append("")
    lines.append("void main() {")
    lines.append("    int lid = int(gl_LocalInvocationID.x);")
    lines.append("    int stride = int(gl_NumWorkGroups.x) * int(gl_WorkGroupSize.x);")
    lines.append("    %s acc = %s;" % (base, identity))
    lines.append("    for (int i = int(gl_GlobalInvocationID.x); i < u_count; i += stride) {")
    lines.append("        acc = %s;" % op_first)
    lines.append("    }")
    lines.append("    pc_partial[lid] = acc;")
    lines.append("    barrier();")
    lines.append("    for (int step = %d; step > 0; step = step >> 1) {" % (local // 2))
    lines.append("        if (lid < step) { pc_partial[lid] = %s; }" % op_shared)
    lines.append("        barrier();")
    lines.append("    }")
    lines.append("    if (lid == 0) { sums[int(gl_WorkGroupID.x)] = pc_partial[0]; }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _scalarCount(name, itemsize):
    """一个输入缓冲区的标量个数（向量元素按分量一起算）。"""
    return rt.bufferBytes(name) // itemsize


def _run(op, inputs, dtype=None, local_size=LOCAL_SIZE, readback=None, trace=None):
    """归约主流程。

    :param op: ``"sum"`` / ``"min"`` / ``"max"``（``dot`` 用 ``sum`` + 两个输入）
    :param inputs: 1~2 个输入（ndarray / list / GlBuffer / 缓冲区名字）
    :param trace: 可选 list；每轮往里追一个 ``(处理元素数, 组数)``（调试/测试用）
    """
    rt.requireContext()
    local = int(local_size)
    if not 1 <= local <= 1024:
        raise ValueError("local_size 要在 1~1024 之间，收到 %r" % (local_size,))

    placed = []          # [(缓冲区名, 标量个数, GLSL 标量类型)]
    pool = []            # 我建的缓冲区（结束时删，除了要返回的结果）
    from_cpu = False
    try:
        for value in inputs:
            gpu = None
            if not isinstance(value, (list, tuple, np.ndarray)):
                gpu = detectBuffer(value)
            if gpu is not None:
                dt = gpu.dtype if gpu.dtype is not None else (dtype or np.float32)
                itemsize = np.dtype(dt).itemsize
                placed.append((gpu.name, _scalarCount(gpu.name, itemsize),
                               baseOfDtype(dt)))
                continue
            arr = np.asarray(value)
            if arr.dtype.kind == "b":
                raise ValueError("归约不支持 bool 数组；请先用 int 的 0/1 数组")
            if arr.dtype.kind not in "fiu":
                raise ValueError("归约只支持数值数组，收到 dtype=%s" % arr.dtype)
            input_base = baseOfDtype(arr.dtype)
            flat = np.ascontiguousarray(arr.reshape(-1),
                                        dtype=rt.bufferDtype(input_base))
            name = rt.genBuffer()
            rt.uploadBuffer(name, flat)
            pool.append(name)
            placed.append((name, int(flat.size), input_base))
            from_cpu = True

        bases = sorted(set(item[2] for item in placed))
        if len(bases) > 1:
            raise ValueError("归约的输入标量类型要一致，收到 %s" % bases)
        counts = sorted(set(item[1] for item in placed))
        if len(counts) > 1:
            raise ValueError("归约的输入长度要一致，收到 %s" % counts)
        base = bases[0]
        length = counts[0]
        if length <= 0:
            raise ValueError("归约的输入是空的（0 个元素）")

        first_input = placed[0][0]
        second_input = placed[1][0] if len(placed) > 1 else None
        current = first_input
        second = second_input
        free = []
        result = None

        def takeBuffer():
            if free:
                return free.pop()
            name = rt.genBuffer()
            pool.append(name)
            return name

        rounds = 0
        while True:
            groups = max(1, (length + local - 1) // local)
            if trace is not None:
                trace.append((length, groups))
            kind = "dot" if (rounds == 0 and second is not None) else op
            combine = _COMBINE["sum" if kind == "dot" else kind]
            identity = _IDENTITIES[("sum" if kind == "dot" else kind, base)]
            inputs_count = 2 if kind == "dot" else 1
            out_binding = inputs_count
            if kind == "dot":
                op_first = "acc + data[i] * data2[i]"
            else:
                op_first = combine("acc", "data[i]")
            op_shared = combine("pc_partial[lid]", "pc_partial[lid + step]")
            source = _template(base, local, inputs_count, out_binding, identity,
                               op_first, op_shared)
            title = "glCompute.%s 归约第 %d 轮（%d 个元素 -> %d 组）" % (
                op, rounds + 1, length, groups)
            shader = rt.compileProgram(source, title)
            location = int(glGetUniformLocation(shader.getShaderId(), "u_count"))
            out_name = takeBuffer()
            rt.ensureBufferSize(out_name, groups * rt.bufferDtype(base).itemsize)
            # ⚠ 只在 with 里切 program：还原"当前 program"，engine 的绘制状态不受影响
            with rt.programScope(shader.getShaderId()):
                # SSBO 绑定点也是 engine（bloom 等 compute）在用的全局状态：要还
                slots = [0, out_binding] + ([1] if kind == "dot" else [])
                saved = rt.saveBufferBindings(slots)
                try:
                    glUniform1i(location, length)
                    rt.bindBufferBase(0, current)
                    if kind == "dot":
                        rt.bindBufferBase(1, second)
                    rt.bindBufferBase(out_binding, out_name)
                    rt.dispatch((groups, 1, 1))
                    rt.barrier(rt.SSB_BIT)
                finally:
                    rt.restoreBufferBindings(saved)
            if current not in (first_input, second_input):
                free.append(current)          # 中间结果可以复用
            current = out_name
            second = None
            length = groups
            rounds += 1
            if length == 1:
                result = out_name
                break
        pool.remove(result)                   # 结果由调用方处置
        if readback is False:
            return None
        if readback is True:
            flat = np.empty(1, dtype=rt.bufferDtype(base))
            rt.downloadBuffer(result, flat)
            return flat[0]
        if from_cpu:                          # 默认：CPU 输入 -> numpy 标量
            flat = np.empty(1, dtype=rt.bufferDtype(base))
            rt.downloadBuffer(result, flat)
            return flat[0]
        # 默认：GPU 输入 -> 结果留在 GPU（可继续喂给别的 kernel，不做回拷）
        return GlBuffer(result, dtype=np.dtype(rt.bufferDtype(base)),
                        length=1, owned=True)
    finally:
        rt.deleteBuffers(pool)


def reduceSum(values, dtype=None, local_size=LOCAL_SIZE, readback=None, trace=None):
    """``sum``：所有元素（含向量分量）求和。CPU 数组 -> numpy 标量；GPU -> 留在 GPU。"""
    return _run("sum", (values,), dtype, local_size, readback, trace)


def reduceDot(a, b, dtype=None, local_size=LOCAL_SIZE, readback=None, trace=None):
    """``dot``：等价于 ``sum(a[i] * b[i])``（第一轮乘起来，之后按 sum 归约）。"""
    return _run("sum", (a, b), dtype, local_size, readback, trace)


def reduceMin(values, dtype=None, local_size=LOCAL_SIZE, readback=None, trace=None):
    """``min``：最小值。"""
    return _run("min", (values,), dtype, local_size, readback, trace)


def reduceMax(values, dtype=None, local_size=LOCAL_SIZE, readback=None, trace=None):
    """``max``：最大值。"""
    return _run("max", (values,), dtype, local_size, readback, trace)

