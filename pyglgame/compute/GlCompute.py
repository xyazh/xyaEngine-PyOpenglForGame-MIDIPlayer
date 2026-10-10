"""``@glCompute``：显式下标的 GPU kernel（+ 归约入口 ``glCompute.sum`` 等）。

编程模型（线程自己算下标、自己写回）::

    from pyglgame.compute import Buffer, glCompute

    @glCompute
    def process(a: Buffer[float], b: Buffer[float]):
        i = index()
        if i >= len(a):
            return
        a[i] = a[i] + b[i]

- ``Buffer[T]`` 参数 = 一整块 SSBO；``Image[T]`` 参数 = 一张 image2D/3D；
  普通 ``float/vec3/...`` 参数 = uniform；函数**不返回值**。
- 线程 id / 尺寸：``index() global_id() global_x/y/z() local_id() local_x/y/z()
  group_id() barrier() memory_barrier()``（也认不带括号的老写法）。
- ``len(buf)`` = 该 buffer 的**元素个数**（每块一个长度 uniform，运行时自动设置）。

参数规则（决定"读回 CPU 还是留在 GPU"）::

| 传进来 | 落地方式 | 算完 |
| --- | --- | --- |
| list / tuple / ndarray | 上传到临时 SSBO（或临时纹理） | 读回 CPU，返回 ndarray |
| 已有 Buffer / VBO / 名字(int) | **直接绑成 SSBO**，不复制 | 原地改 GPU 数据，不读回，返回 None |
| 已有 Texture（引擎 Texture / GlImage） | **直接绑成 image unit** | 原地改，不读回，返回 None |
| float / int / bool / vec* | uniform | —— |

返回值：1 个 CPU 数组参数 -> 那个 ndarray；多个 -> 列表（按参数顺序）；全 GPU -> None。
``readback=True`` 强制把 GPU 数据也读回来；``readback=False`` 强制不读。

派发尺寸：取**第一个数组/图像参数**的形状（1D -> (n,1,1)，2D ``(h,w)`` -> ``(w,h,1)``，
3D ``(d,h,w)`` -> ``(w,h,d)``）；没有数组参数时用调用处的 ``size=``。
"""

import functools

import numpy as np
from OpenGL.GL import (GL_READ_ONLY, GL_READ_WRITE, glGetUniformLocation,
                       glUniform1i)

from ..render import ImageUnits
from . import GlRuntime as rt
from .Objects import (GlBuffer, GlImage, detectBuffer, detectImage, makeImage,
                      readImage)
from .translator import analyzeSignature, imageFormat, translateFunction

__all__ = ["glCompute", "GlKernel", "GlBuffer", "GlImage"]


# ------------------------------------------------------------------ 小工具


class _Placed(object):
    """一个数组/图像参数在本次调用里的落地形态。"""

    __slots__ = ("name", "shape", "cpu", "image", "count", "owned", "source")

    def __init__(self, name, shape=None, cpu=None, image=None, count=None,
                 owned=False, source=None) -> None:
        self.name = name        # GL 缓冲区 / 纹理名字
        self.shape = shape      # "元素空间"形状（网格由它决定）
        self.cpu = cpu          # CPU 上传用的连续 ndarray（None = 本来就是 GPU 对象）
        self.image = image      # GlImage（image 参数）
        self.count = count      # 元素个数
        self.owned = owned      # 临时对象（要删）
        self.source = source    # 原始输入值（判断返回容器/形状用）


def gridOfShape(shape):
    """形状 -> 派发网格 (x, y, z)；1D -> (n,1,1)，2D (h,w) -> (w,h,1)。"""
    if not shape:
        return None
    if len(shape) == 1:
        return (int(shape[0]), 1, 1)
    if len(shape) == 2:
        return (int(shape[1]), int(shape[0]), 1)
    if len(shape) == 3:
        return (int(shape[2]), int(shape[1]), int(shape[0]))
    raise ValueError("最多支持三维数组，收到 shape=%s" % (shape,))


def elementShapeOf(arr, element):
    """CPU 数组 -> "元素空间"形状（去掉末尾的分量维）。"""
    if element.is_mat:
        return arr.shape[:-2]
    if element.comps > 1:
        return arr.shape[:-1]
    return arr.shape


def checkArrayShape(param, arr, element) -> None:
    """CPU 数组形状校验（元素类型决定"每元素几个分量"）。"""
    if element.is_mat:
        if arr.ndim != 3 or tuple(arr.shape[1:]) != (element.cols, element.rows):
            raise ValueError("参数 `%s`（%s）需要形状 (n, %d, %d) 的数组（列优先），"
                             "收到 shape=%s" % (param.name, element, element.cols,
                                                element.rows, arr.shape))
        return
    if element.comps > 1:
        if arr.ndim not in (2, 3, 4) or int(arr.shape[-1]) != element.comps:
            raise ValueError("参数 `%s`（%s）需要最后一维是 %d 的数组，收到 shape=%s"
                             % (param.name, element, element.comps, arr.shape))
        if arr.ndim == 4:
            raise ValueError("参数 `%s`：三维 + 分量的数组请按 3 维元素走，"
                             "或改用 Image[...]" % param.name)
        return
    if arr.ndim not in (1, 2, 3):
        raise ValueError("参数 `%s`（%s）需要 1~3 维数组，收到 shape=%s"
                         % (param.name, element, arr.shape))


def prepareImageData(arr, element, fmt):
    """CPU 数组 -> image 上传要的 (…, comps) 连续数组。"""
    comps = 4 if fmt.startswith("rgba") else (2 if fmt.startswith("rg") else 1)
    if element.comps == 1:
        core = arr[..., None]                 # 标量元素：(h, w) / (d, h, w) 补一个通道维
    elif arr.ndim == 2:
        raise ValueError("元素是 %s 的 Image 参数需要 (h, w, %d) 的数组，收到 shape=%s"
                         % (element, element.comps, arr.shape))
    else:
        core = arr
    if core.shape[-1] != comps:
        padded = np.zeros(core.shape[:-1] + (comps,), dtype=core.dtype)
        keep = min(comps, core.shape[-1])
        padded[..., :keep] = core[..., :keep]
        core = padded
    if fmt.endswith("f"):
        dtype = np.float32
    elif fmt.endswith("ui"):
        dtype = np.uint32
    else:
        dtype = np.int32
    return np.ascontiguousarray(core, dtype=dtype)


def trimImageResult(data, element):
    """读回来的 (h, w, comps) 按元素类型裁回 (h, w) / (h, w, comps)。"""
    if element.is_scalar:
        return data[..., 0]
    return data[..., :element.comps]


# ================================================================= kernel 本体


def normalizeLocalSize(value):
    """``64`` / ``(16, 16, 1)`` -> ``(x, y, z)``。"""
    if isinstance(value, int) and not isinstance(value, bool):
        size = (int(value), 1, 1)
    else:
        try:
            size = tuple(int(v) for v in value)
        except TypeError:
            raise TypeError("local_size 需要 int 或 3 元组，收到 %r" % (value,))
        if len(size) != 3:
            raise ValueError("local_size 需要 int 或 3 元组 (x, y, z)，收到 %r" % (value,))
    if any(n < 1 for n in size):
        raise ValueError("local_size 每一项都要 >= 1，收到 %r" % (size,))
    if size[0] * size[1] * size[2] > 1024:
        raise ValueError("local_size 的乘积不能超过 1024（GL 的 workgroup 上限），收到 %r"
                         % (size,))
    return size


def elementDims(t):
    """元素的 numpy 形状：标量 ()、vecN (N,)、矩阵 (列, 行)。"""
    if t.is_mat:
        return (t.cols, t.rows)
    if t.is_vec:
        return (t.comps,)
    return ()


class GlKernel(object):
    """``@glCompute`` 装饰出来的 kernel（唯一入口就是调用它）。"""

    def __init__(self, fn, local_size=None, name=None, source=None):
        if isinstance(fn, (staticmethod, classmethod)):   # @glCompute 叠在 @staticmethod 上
            fn = fn.__func__
        functools.update_wrapper(self, fn)
        self.func = fn
        self.name = name or getattr(fn, "__name__", "glCompute")
        # 显式源码（@glCompute(source=...)）：打包成 Nuitka/PyInstaller 之后没有 .py
        # 可读，译文只能靠它；None = 正常从文件里读函数源码
        self.source = source
        # None = 按派发维度自动（1D 64 / 2D+ 16x16）；给了就在装饰期校验
        self.local_size = normalizeLocalSize(local_size) if local_size is not None else None
        self.params = analyzeSignature(fn)
        self._infos = {}                      # 变体 -> ShaderInfo（纯文本，不需要 GL）
        self._temps = {}                      # 绑位 -> 我建的临时缓冲区
        self._locations = {}                  # (program, uniform 名) -> location
        # 装饰期先翻一份"主形态"（1D、image 按元素类型默认格式）供 glsl_source 查看，
        # 这一步不需要 GL 上下文；真正要跑的变体在调用时按实际形状/格式翻并缓存
        self._primary = self._info(None, (64, 1, 1))

    # ------------------------------------------------------------ 只读信息
    @property
    def glsl_source(self):
        """主形态 GLSL 源码（1D 派发、image 按元素类型默认格式）。"""
        return self._primary.source

    @property
    def glsl_sources(self):
        """所有已经翻译过的变体：``{变体键: GLSL 源码}``（调试用）。"""
        return dict((key, info.source) for key, info in self._infos.items())

    def __repr__(self):
        parts = []
        for param in self.params:
            if param.kind == "buffer":
                parts.append("%s: Buffer[%s]" % (param.name, param.type))
            elif param.kind == "image":
                parts.append("%s: Image[%s]" % (param.name, param.type))
            else:
                parts.append("%s: %s" % (param.name, param.type))
        return "<@glCompute %s(%s)>" % (self.name, ", ".join(parts))

    def clearCache(self) -> None:
        """丢掉临时缓冲区与已编译 program（换 GL 上下文之后调用）。"""
        rt.deleteBuffers(list(self._temps.values()))
        self._temps.clear()
        self._locations.clear()
        rt.clearProgramCache()
        ImageUnits.reset()          # 上下文换了，image unit 的记账也过期了

    def release(self) -> None:
        """别名：清掉这个 kernel 留下的 GPU 对象。"""
        self.clearCache()

    # --------------------------------------------------------------- 内部
    def _variantKey(self, image_specs, local_size):
        parts = []
        for p in self.params:
            spec = (image_specs or {}).get(p.name)
            if spec is None:
                parts.append("%s:%s" % (p.name, p.kind))
            else:
                parts.append("%s:%s:%d:%s" % (p.name, p.kind, spec[0], spec[2]))
        return (tuple(parts), tuple(local_size))

    def _info(self, image_specs, local_size):
        key = self._variantKey(image_specs, local_size)
        info = self._infos.get(key)
        if info is None:
            info = translateFunction(self.func, self.params, local_size, image_specs,
                                     self.source)
            self._infos[key] = info
        return info

    def _bindArgs(self, args, kwargs):
        """把调用参数对齐到参数表（支持按名字传 + uniform 默认值）。"""
        total = len(self.params)
        if len(args) > total:
            raise TypeError("%s() 最多接受 %d 个参数，给了 %d 个"
                            % (self.name, total, len(args)))
        values = [None] * total
        filled = [False] * total
        for index, value in enumerate(args):
            values[index] = value
            filled[index] = True
        positions = dict((p.name, i) for i, p in enumerate(self.params))
        for key, value in kwargs.items():
            index = positions.get(key)
            if index is None:
                raise TypeError("%s() 没有参数 `%s`" % (self.name, key))
            if filled[index]:
                raise TypeError("%s() 的参数 `%s` 被重复赋值" % (self.name, key))
            values[index] = value
            filled[index] = True
        for index, param in enumerate(self.params):
            if not filled[index]:
                if param.has_default:
                    values[index] = param.default
                else:
                    raise TypeError("%s() 缺少参数 `%s`" % (self.name, param.name))
        return values

    def _tempBuffer(self, slot):
        name = self._temps.get(slot)
        if name is None:
            name = rt.genBuffer()
            self._temps[slot] = name
        return name

    def _placeBuffer(self, param, value, slot):
        """Buffer 参数 -> 落在哪个缓冲区（CPU 上传 / 已有 GPU 对象直接用）。"""
        element = param.type
        itemsize = rt.bufferDtype(element.base).itemsize
        if not isinstance(value, (list, tuple, np.ndarray)):
            gpu = detectBuffer(value)
            if gpu is not None:
                count = gpu.length
                if count is None:
                    count = rt.bufferBytes(gpu.name) // (itemsize * element.comps)
                shape = getattr(gpu, "shape", None) or None
                return _Placed(gpu.name, shape=shape, count=count, source=value)
        arr = np.asarray(value)
        checkArrayShape(param, arr, element)
        shape = tuple(int(v) for v in elementShapeOf(arr, element))
        flat = np.ascontiguousarray(arr.reshape(-1), dtype=rt.bufferDtype(element.base))
        count = int(flat.size // element.comps)
        name = self._tempBuffer(slot)
        rt.uploadBuffer(name, flat)
        return _Placed(name, shape=shape, cpu=flat, count=count, owned=True, source=value)

    def _placeImage(self, param, value):
        """Image 参数 -> 落在哪张纹理（CPU 数组建临时纹理 / 已有纹理直接用）。"""
        element = param.type
        if not isinstance(value, (list, tuple, np.ndarray)):
            gpu = detectImage(value)
            if gpu is not None:
                width, height, depth = gpu.sizeOf()
                shape = (height, width) if depth <= 1 else (depth, height, width)
                return _Placed(gpu.name, shape=shape, image=gpu,
                               count=width * height * depth, source=value)
        arr = np.asarray(value)
        spatial = arr.shape if element.comps == 1 else arr.shape[:-1]
        if len(spatial) == 2:
            height, width, depth = int(spatial[0]), int(spatial[1]), 1
        elif len(spatial) == 3:
            depth, height, width = (int(v) for v in spatial)
        else:
            raise ValueError("参数 `%s`（Image[...]）需要二维/三维数组，收到 shape=%s"
                             % (param.name, arr.shape))
        fmt = imageFormat(element)
        data = prepareImageData(arr, element, fmt)
        image = makeImage(fmt, width, height, depth, data)
        return _Placed(image.name, shape=tuple(int(v) for v in spatial), image=image,
                       count=width * height * depth, owned=True, source=value,
                       cpu=data)

    def _resolveGrid(self, placed, size):
        if size is not None:
            if isinstance(size, int) and not isinstance(size, bool):
                return (int(size), 1, 1)
            parts = tuple(int(v) for v in size)
            if len(parts) > 3:
                parts = parts[:3]
            return parts + (1,) * (3 - len(parts))
        for param in self.params:
            if param.kind in ("buffer", "image"):
                item = placed[param.name]
                grid = gridOfShape(item.shape) if item.shape else None
                if grid is None and item.count:      # GPU 缓冲区没有形状：按元素个数
                    grid = (item.count, 1, 1)
                if grid:
                    return grid
        raise ValueError("%s(): 没有数组/图像参数，也没有 size=，无法确定派发尺寸"
                         % self.name)

    def _resolveLocalSize(self, local_size, grid):
        value = local_size if local_size is not None else self.local_size
        if value is not None:
            return normalizeLocalSize(value)
        return (64, 1, 1) if grid[1] == 1 and grid[2] == 1 else (16, 16, 1)

    def _location(self, shader, name):
        key = (shader.getShaderId(), name)
        location = self._locations.get(key)
        if location is None:
            location = int(glGetUniformLocation(shader.getShaderId(), name))
            self._locations[key] = location
        return location

    def _uploadParams(self, shader, info, placed, values, grid) -> None:
        """uniform（标量参数 + 每块长度 + width/height/depth/count）。"""
        value_of = dict((p.name, v) for p, v in zip(self.params, values))
        for entry in info.uniforms:
            location = self._location(shader, entry.glsl)
            if location >= 0:
                rt.setUniform(location, entry.type, value_of[entry.name], entry.name)
        for entry in info.buffers:
            location = self._location(shader, entry.length_uniform)
            if location >= 0:
                glUniform1i(location, int(placed[entry.name].count))
        for name, value in (("u_width", grid[0]), ("u_height", grid[1]),
                            ("u_depth", grid[2]), ("u_count", grid[0] * grid[1] * grid[2])):
            location = self._location(shader, name)
            if location >= 0:
                glUniform1i(location, int(value))

    # --------------------------------------------------------------- 调用
    def __call__(self, *args, **kwargs):
        readback = kwargs.pop("readback", None)
        size = kwargs.pop("size", None)
        local_size = kwargs.pop("local_size", None)
        barriers = kwargs.pop("barriers", None)
        values = self._bindArgs(args, kwargs)
        rt.requireContext()
        placed = {}
        temporary_images = []
        try:
            for slot, (param, value) in enumerate(zip(self.params, values)):
                if param.kind == "buffer":
                    placed[param.name] = self._placeBuffer(param, value, slot)
                elif param.kind == "image":
                    entry = self._placeImage(param, value)
                    placed[param.name] = entry
                    if entry.owned and entry.image is not None:
                        temporary_images.append(entry.image)
            grid = self._resolveGrid(placed, size)
            local = self._resolveLocalSize(local_size, grid)
            image_specs = {}
            for param in self.params:
                if param.kind == "image":
                    image = placed[param.name].image
                    image_specs[param.name] = (image.dim, None, image.glslFormat())
            info = self._info(image_specs, local)
            # GLSL 的 image binding 就是参数序号（0,1,2…），得塞得进 image unit 才行
            limit = ImageUnits.maxUnits()
            if len(info.images) > limit:
                raise RuntimeError(
                    "%s(): 这个 kernel 要同时绑 %d 个 image，但本上下文只有 %d 个"
                    " image unit（GL_MAX_IMAGE_UNITS）。少用几个 image 参数，"
                    "或者把多张图打包成一张（例如 2D array / 拼图）。"
                    % (self.name, len(info.images), limit))
            shader = rt.compileProgram(info.source, "@glCompute %s" % self.name)
            # ⚠ 只在 with 里切 program：engine 的 using_shader 不动、退出时把
            # "当前 program"还回去，否则同一帧后面画的物体全用 compute program 画
            with rt.programScope(shader.getShaderId()):
                # 这些绑定点是 engine（bloom / 体积渲染 / 别的 compute）在用的全局状态：
                # 先记下来，退出前原样还回去，否则它们的输入/输出会接到我们的缓冲区上
                saved_buffers = rt.saveBufferBindings([b.binding for b in info.buffers])
                saved_images = rt.saveImageBindings([i.binding for i in info.images])
                try:
                    self._uploadParams(shader, info, placed, values, grid)
                    for entry in info.buffers:
                        rt.bindBufferBase(entry.binding, placed[entry.name].name)
                    for entry in info.images:
                        image = placed[entry.name].image
                        access = (GL_READ_WRITE if entry.name in info.writable
                                  else GL_READ_ONLY)
                        rt.bindImage(entry.binding, image.name, image.target, access,
                                     rt.IMAGE_FORMATS[entry.format][0])
                    lx, ly, lz = local
                    rt.dispatch(((grid[0] + lx - 1) // lx,
                                 (grid[1] + ly - 1) // ly,
                                 (grid[2] + lz - 1) // lz))
                    rt.barrier(barriers if barriers is not None
                               else rt.barrierFor([b.name for b in info.buffers
                                                   if b.name in info.writable],
                                                  [i.name for i in info.images
                                                   if i.name in info.writable]))
                finally:
                    rt.restoreBufferBindings(saved_buffers)
                    rt.restoreImageBindings(saved_images)
            results = self._readback(info, placed, readback)
        finally:
            for image in temporary_images:
                image.release()
        if not results:
            return None
        return results[0] if len(results) == 1 else results

    def _readback(self, info, placed, readback):
        """按规则读回：**CPU 数据 + 这次真被写过**的默认读回，GPU 对象默认不读。

        ``readback=True`` 强制全读（GPU 的也读回来），``readback=False`` 强制不读。
        """
        results = []
        by_name = dict((p.name, p) for p in self.params)
        for entry in list(info.buffers) + list(info.images):
            param = by_name[entry.name]
            item = placed[entry.name]
            if readback is None:
                want = item.cpu is not None and entry.name in info.writable
            else:
                want = bool(readback)
            if not want:
                continue
            element = param.type
            if param.kind == "image":
                data = trimImageResult(readImage(item.image), element)
                results.append(finishImage(data, item))
                continue
            flat = np.empty(item.count * element.comps,
                            dtype=rt.bufferDtype(element.base))
            rt.downloadBuffer(item.name, flat)
            if element.base == "bool":
                flat = flat != 0
            else:
                flat = flat.reshape((item.count,) + elementDims(element))
            results.append(finishBuffer(flat, item, element))
        return results


def finishBuffer(flat, item, element):
    """把读回的扁平数组整理成"和输入一样形状"的 ndarray。"""
    source = item.source
    if isinstance(source, (list, tuple, np.ndarray)):
        try:
            return np.asarray(flat).reshape(np.shape(source))
        except ValueError:                  # pragma: no cover - 尺寸对不上就原样给
            return np.asarray(flat)
    shape = tuple(item.shape) if item.shape else (item.count,)
    return np.asarray(flat).reshape(shape + elementDims(element))


def finishImage(data, item):
    """把读回的图像整理成和输入一样的形状（GPU 纹理则给 ``(h,w[,c])``）。"""
    source = item.source
    if isinstance(source, (list, tuple, np.ndarray)):
        try:
            return np.asarray(data).reshape(np.shape(source))
        except ValueError:                  # pragma: no cover
            return np.asarray(data)
    return np.asarray(data)


# ============================================================ 装饰器 / 归约入口


class _GlComputeAPI(object):
    """``@glCompute``：**既是装饰器，也是归约入口**。

    用法::

        @glCompute                              # 当装饰器
        def f(a: Buffer[float]): ...

        @glCompute(local_size=256)              # 带参数也行
        def g(a: Buffer[float]): ...

        @glCompute(source="i = index()\\nif i >= len(a):\\n    return\\na[i] = a[i] * 2.0")
        def h(a: Buffer[float]): ...            # 打包后没有 .py：函数体直接给字符串

        total = glCompute.sum(array)            # 归约（shared memory + 多轮 dispatch）
        prod = glCompute.dot(a, b)              # 等价于 sum(a[i] * b[i])
        lo = glCompute.min(array)
        hi = glCompute.max(array)

    关于 ``source=``：``@glCompute`` 要在翻译期读函数**源码**（翻成 GLSL）。
    打包（Nuitka / PyInstaller）之后 .py 不在分发里，``inspect`` 拿不到源码
    （Nuitka 甚至把 ``co_code`` 也清了，code object 里没有可还原的东西），
    这时用 ``source=`` 把函数体写成字符串即可 —— 参数的类型标注仍然写在 def 上
    （``inspect.signature`` 在 Nuitka 下正常，实测）。详见 ``docs/glCompute.md``。
    """

    def __call__(self, fn=None, *, local_size=None, name=None, source=None):
        if fn is None:
            return functools.partial(glCompute, local_size=local_size, name=name,
                                     source=source)
        if isinstance(fn, GlKernel):
            return fn
        # @glCompute 叠在 @staticmethod 上（3.9 及以前的 staticmethod 对象不可调用，这里挡住）
        if isinstance(fn, (staticmethod, classmethod)):
            fn = fn.__func__
        if not callable(fn):
            raise TypeError("glCompute 需要装饰一个函数，收到 %r"
                            "（要传参数就写 @glCompute(local_size=...)）" % (fn,))
        return GlKernel(fn, local_size=local_size, name=name, source=source)

    def __repr__(self):
        return "<pyglgame.compute.glCompute>"

    # 顶层函数在 Reduce.py；这里只做转发（避免 import 环）
    def sum(self, values, **kwargs):
        """求和（``sum(buffer[i])``）：CPU 输入返回 np 标量，GPU 输入结果留在 GPU。"""
        from .Reduce import reduceSum
        return reduceSum(values, **kwargs)

    def dot(self, a, b, **kwargs):
        """点积：等价于 ``sum(a[i] * b[i])``。"""
        from .Reduce import reduceDot
        return reduceDot(a, b, **kwargs)

    def min(self, values, **kwargs):
        """最小值。"""
        from .Reduce import reduceMin
        return reduceMin(values, **kwargs)

    def max(self, values, **kwargs):
        """最大值。"""
        from .Reduce import reduceMax
        return reduceMax(values, **kwargs)


#: 唯一的对外名字：装饰器 + 归约入口
glCompute = _GlComputeAPI()





