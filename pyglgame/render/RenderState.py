"""渲染状态（混合模式 / 深度写入 / 深度测试）管理。

引擎原先在 :meth:`BaseWindow.run` 里一次性设死：

    glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDepthMask(GL_TRUE)

也就是"混合常开 + 固定 alpha 混合 + 半透明也写深度"，任何半透明内容都只能按
绘制顺序叠加 —— 属于**顺序相关透明**（order-dependent transparency）。

这里把状态拆成三层，既能保持旧行为，又能做 OIT 所需的两趟渲染：

1. **Pass 级**：``setDefaults()`` —— 不透明 pass 恢复引擎默认；半透明 pass
   关闭深度写入（:meth:`Camera.beginTransparent`）。
2. **批次级**：``RenderBuffer.blend_mode / depth_write`` —— 只覆盖显式指定的
   字段，用于"这一批三角形用加性混合"这类需求（一次 draw call 一个状态）。
3. **对象级**：``GameObject.transparent`` —— 决定该对象进哪一趟
   （不透明先画、半透明后画并按相机深度排序）。

状态带缓存，重复设置不会产生多余 GL 调用。
"""

from OpenGL.GL import *

# === 混合模式 ===
BLEND_NONE = "none"                     # 关闭混合
BLEND_ALPHA = "alpha"                   # 常规 alpha 混合（引擎默认）
BLEND_PREMULTIPLIED = "premultiplied"   # 预乘 alpha
BLEND_ADD = "add"                       # 加性：加法可交换 => 叠加顺序无关

BLEND_FUNCS = {
    BLEND_NONE: (GL_ONE, GL_ZERO),
    BLEND_ALPHA: (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA),
    BLEND_PREMULTIPLIED: (GL_ONE, GL_ONE_MINUS_SRC_ALPHA),
    BLEND_ADD: (GL_ONE, GL_ONE),
}

DEFAULT_BLEND = BLEND_ALPHA


def resolveState(blend_mode=None, depth_write=None, depth_test=None,
                 default_blend: str = DEFAULT_BLEND):
    """把 ``None``（未指定）解析成具体状态，返回 ``(blend, depth_write, depth_test)``。

    纯函数：方便单元测试，也用于状态缓存比较。
    """
    if blend_mode is not None and blend_mode not in BLEND_FUNCS:
        raise ValueError(f"未知混合模式: {blend_mode}")
    return (default_blend if blend_mode is None else blend_mode,
            True if depth_write is None else bool(depth_write),
            True if depth_test is None else bool(depth_test))


def defaultState():
    """引擎原始默认状态（= 旧行为，保证不透明物体渲染结果不变）。"""
    return resolveState(DEFAULT_BLEND, True, True)


_current = None


def current():
    return _current


def applyState(state, force: bool = False) -> bool:
    """应用状态；与当前状态相同则跳过。返回是否真的调用了 GL。"""
    global _current
    if not force and state == _current:
        return False
    _current = state
    blend_mode, depth_write, depth_test = state
    if blend_mode == BLEND_NONE:
        glDisable(GL_BLEND)
    else:
        glEnable(GL_BLEND)
        src, dst = BLEND_FUNCS[blend_mode]
        glBlendFunc(src, dst)
    glDepthMask(GL_TRUE if depth_write else GL_FALSE)
    if depth_test:
        glEnable(GL_DEPTH_TEST)
    else:
        glDisable(GL_DEPTH_TEST)
    return True


def setDefaults(blend_mode=None, depth_write=None, depth_test=None) -> bool:
    """设置"这一趟渲染"的基准状态（未指定的字段用引擎默认值）。"""
    return applyState(resolveState(blend_mode, depth_write, depth_test))


def override(blend_mode=None, depth_write=None, depth_test=None) -> bool:
    """只覆盖显式指定的字段，其余保持当前状态。

    用于 :class:`~pyglgame.render.RenderBuffer.RenderBuffer` 的批次级配置
    （例如"这一批用加性混合"，但深度写入跟随当前 pass）。
    """
    if blend_mode is None and depth_write is None and depth_test is None:
        return False
    base = _current if _current is not None else defaultState()
    if blend_mode is not None and blend_mode not in BLEND_FUNCS:
        raise ValueError(f"未知混合模式: {blend_mode}")
    state = (base[0] if blend_mode is None else blend_mode,
             base[1] if depth_write is None else bool(depth_write),
             base[2] if depth_test is None else bool(depth_test))
    return applyState(state)


def reset() -> bool:
    """恢复引擎默认状态（如给外部代码/自定义 pass 用）。"""
    return applyState(defaultState(), force=True)


def invalidate() -> None:
    """丢弃状态缓存。

    外部代码（例如 WBOIT 这种直接操作 per-draw-buffer 混合的 pass）绕过本模块
    改过 GL 状态后必须调用它，否则下次 ``setDefaults()`` 会因为缓存"看起来一样"
    而跳过真正的 GL 调用，把上一个 pass 的混合模式留给后面的绘制。
    """
    global _current
    _current = None
