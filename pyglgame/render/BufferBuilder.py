import array

import numpy as np

POS = 0b00000001
COL = 0b00000010
TEX = 0b00000100
NOR = 0b00001000
LIT = 0b00010000
SIZ = 0b00100000
# === 2D 光照用到的两个"材质开关"位（Phase 7）===
# 它们不产生新的顶点属性，只是告诉片元着色器"这批三角形的 tex3/tex4 是
# 2D 法线贴图 / 2D 高光贴图"—— 所以可以随 draw call 自由开关，不会污染后续绘制。
# 与 ``res/shader/dis.vert|frag`` 里的 ``const int N2D / S2D`` **数值必须一致**。
# 说明见 docs/2D光照与阴影.md
N2D = 0b01000000                     # 2D 法线贴图（tex3）
S2D = 0b10000000                     # 2D 高光贴图（tex4）

# === 2D 光照的「整批开关」位（同样不产生顶点属性，逐 draw call 生效）===
# 用途：**按图层 / 按对象**控制这一批三角形吃不吃光照（Tiled 的图层自定义属性就映射到这两个位）
#   ``AMB2D``   = 只吃环境光：直接光 / 高光 / 阴影整段跳过
#   ``UNLIT2D`` = 完全不受光照：环境光也不加，只输出颜色 / 贴图
# 两个位同时出现时以 ``UNLIT2D`` 为准（先判 unlit）。见 docs/2D光照与阴影.md §2.1
AMB2D = 0b100000000                  # 256：只吃环境光（ambient only）
UNLIT2D = 0b1000000000               # 512：完全不受光照（unlit）

# 单精度浮点字节数
FLOAT_SIZE = 4


class BufferBuilder:
    """顶点数据构造器（CPU 侧）。

    与旧实现（Python list + ``(c_float * n)(*list)`` 展开）相比：
    - 数据放在 ``array.array('f')`` 里，``end()`` 只是一次 C 层 ``extend``
    - 上传时用 ``np.frombuffer`` 做零拷贝视图，直接交给 ``glBufferData``
    """

    def __init__(self, pri_type: int, format_type: int) -> None:
        self.pri_type = pri_type
        self.format_type = format_type
        self.offset_pos = 0
        self.offset_col = 0
        self.offset_tex = 0
        self.offset_nor = 0
        self.offset_lit = 0
        self.offset_siz = 0
        self.initTempBuffer()
        # 每个顶点的 float 个数
        self.row_size = len(self.temp_buffer)
        self.size = 0                       # 已写入的顶点数
        self.dirty = False                  # 是否需要重新上传到 GPU
        self._data = array.array("f")

    def initTempBuffer(self) -> None:
        count = 0
        if self.format_type & POS:
            self.offset_pos = count
            count += 3
        if self.format_type & COL:
            self.offset_col = count
            count += 4
        if self.format_type & TEX:
            self.offset_tex = count
            count += 2
        if self.format_type & NOR:
            self.offset_nor = count
            count += 3
        if self.format_type & LIT:
            self.offset_lit = count
            count += 2
        if self.format_type & SIZ:
            self.offset_siz = count
            count += 1
        self.temp_buffer = [0 for _ in range(count)]

    # === 兼容旧代码 ===

    @property
    def buffer(self) -> array.array:
        """扁平 float32 缓冲（与旧 list 的用法兼容：len() 为 float 个数）。"""
        return self._data

    def clear(self) -> None:
        """清空顶点数据（保留已申请的容量以便复用）。"""
        try:
            del self._data[:]
        except BufferError:
            # 有人还握着一份 numpy 视图（``vertex_data``，例如调试工具 / 单测读完顶点
            # 又去改对象）：``del`` 会撞 "cannot resize an array that is exporting
            # buffers"。这时直接换一块新数组，行为不变（``dirty`` 仍会让它重传）。
            self._data = array.array("f")
        self.size = 0
        self.dirty = True

    def freeData(self) -> None:
        """释放 CPU 侧顶点内存（数据已上传到 GPU 后调用，绘制仍使用已上传的顶点数）。"""
        self._data = array.array("f")
        self.size = 0

    # === 顶点写入 ===

    def pos(self, x: float | int, y: float | int, z: float | int) -> "BufferBuilder":
        o = self.offset_pos
        self.temp_buffer[o] = x
        self.temp_buffer[o + 1] = y
        self.temp_buffer[o + 2] = z
        return self

    def col(self, r: float | int, g: float | int, b: float | int,
            a: float | int = 1) -> "BufferBuilder":
        o = self.offset_col
        self.temp_buffer[o] = r
        self.temp_buffer[o + 1] = g
        self.temp_buffer[o + 2] = b
        self.temp_buffer[o + 3] = a
        return self

    def tex(self, u: float | int, v: float | int) -> "BufferBuilder":
        o = self.offset_tex
        self.temp_buffer[o] = u
        self.temp_buffer[o + 1] = v
        return self

    def nor(self, x: float | int, y: float | int,
            z: float | int) -> "BufferBuilder":
        o = self.offset_nor
        self.temp_buffer[o] = x
        self.temp_buffer[o + 1] = y
        self.temp_buffer[o + 2] = z
        return self

    def lit(self, u: float | int, v: float | int) -> "BufferBuilder":
        o = self.offset_lit
        self.temp_buffer[o] = u
        self.temp_buffer[o + 1] = v
        return self

    def siz(self, s: float | int) -> "BufferBuilder":
        o = self.offset_siz
        self.temp_buffer[o] = s
        return self

    def end(self) -> None:
        # array.array.extend 是 C 层批量追加，比逐元素写 numpy 行快很多
        self._data.extend(self.temp_buffer)
        self.size += 1
        self.dirty = True

    # === 给渲染层用 ===

    @property
    def vertex_data(self) -> np.ndarray:
        """形状为 ``(size, row_size)`` 的零拷贝 float32 视图（C 连续）。"""
        return np.frombuffer(self._data, dtype=np.float32,
                             count=self.size * self.row_size
                             ).reshape(self.size, self.row_size)

    @property
    def nbytes(self) -> int:
        return self.size * self.row_size * FLOAT_SIZE

    @property
    def stride(self) -> int:
        return self.row_size * FLOAT_SIZE

    def markClean(self) -> None:
        self.dirty = False
