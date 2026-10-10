"""image unit 的自动分配 —— 别再自己写 ``unit=0..7``。

GL 只保证 **8 个** image unit（合法编号 0..7，很多驱动就是 8），而引擎自己的
compute 管线已经把 0..7 全用上了：

| 单元 | 谁在用 | 绑定时机 |
| --- | --- | --- |
| 0 | Bloom 的输入（相机颜色纹理） | ``Camera.createBloom()`` **只绑一次** |
| 1..3 | Bloom pingpong / FFT ``WORK_UNITS`` / 体积 DEPTH·COLOR·SCATTER | 每次 dispatch 前重绑 |
| 4 | Bloom / FFT ``OUTPUT_UNIT`` / 体积 ``NOISE_UNIT`` | 同上 |
| 5,6 | Bloom out·ldr / FFT ``SPECTRUM_UNITS`` / 滤镜链 | 同上 |
| 7 | 2D 分形噪声 / 3D 体积噪声 / 滤镜链 / 模糊 | 同上 |

所以：

- **别长期占 0**（谁占谁画面糊：Bloom 会读到你的纹理，格式还不匹配）；
- 1..7 是"软共享"的（引擎每次 dispatch 前都会重绑），**短租**没问题；但也正因为如此，
  **你租的单元可能被引擎的下一次 dispatch 顶掉** —— 别依赖它长期有效，
  用之前再 `bindUnit(unit)` 一次（`glCompute` 就是在 dispatch 那一刻绑的）；
- 驱动给到 >8 个单元时，优先拿高编号（引擎完全碰不到）。

用法::

    from pyglgame.render import ImageUnits

    unit = ImageUnits.alloc("我的纹理")      # 自动挑一个空闲单元
    tex.bindUnit(unit)
    ...
    ImageUnits.free(unit, owner="我的纹理")

    with ImageUnits.lease("这一帧") as unit:  # 退出自动还
        tex.bindUnit(unit)

其实大多数情况连这都不用写：``TextureStorage2D(unit=None)``（默认）**不占** image
unit，``bindUnit()`` / ``glCompute`` 会在真要用的那一刻自动要一个、用完还掉。
"""

from OpenGL.GL import GL_MAX_IMAGE_UNITS, glGetIntegerv

__all__ = ["ENGINE_UNITS", "ENGINE_DURABLE_UNITS", "setMaxUnits", "maxUnits",
           "allocated", "alloc", "free", "release", "lease", "reset"]

#: 引擎 compute 管线用到的单元（见模块文档的表）
ENGINE_UNITS = frozenset(range(8))

#: 引擎里**只在创建时绑一次、之后不重绑**的单元 —— 长期占用 = 画面糊，别碰
ENGINE_DURABLE_UNITS = frozenset((0,))

#: 引擎每次 dispatch 前都会重绑的单元：短租安全（默认的候选池）
ENGINE_SOFT_UNITS = ENGINE_UNITS - ENGINE_DURABLE_UNITS

#: 没拿到上下文 / 查不到时按 GL 规范的下限算
DEFAULT_MAX_UNITS = 8

_MAX = [None]                      # 缓存；setMaxUnits 可覆盖（测试 / 特殊驱动）
_LEASED = {}                       # unit -> owner（只记我们自己租出去的）


def setMaxUnits(count) -> None:
    """覆盖"可用单元数"（``None`` = 下次重新查 GL；主要给测试用）。"""
    _MAX[0] = None if count is None else int(count)


def maxUnits(force: bool = False) -> int:
    """当前上下文的 ``GL_MAX_IMAGE_UNITS``（查不到就按 8 算）。"""
    if _MAX[0] is None or force:
        try:
            out = glGetIntegerv(GL_MAX_IMAGE_UNITS)
            count = int(out)
        except Exception:                       # 没上下文 / 驱动不支持
            count = DEFAULT_MAX_UNITS
        _MAX[0] = max(count, 1)
    return _MAX[0]


def allocated() -> dict:
    """快照：单元 -> 占用者（给报错信息 / 调试用）。"""
    return dict(_LEASED)


def alloc(owner=None, force: bool = False) -> int:
    """要一个空闲 image unit，返回它的编号（找不到就报错，不会静默覆盖别人）。

    - 先挑**引擎碰不到**的高编号（驱动给了 >8 个单元时）；
    - 再挑 1..7 里没人租的（引擎在这几个单元上每次 dispatch 前都会重绑）；
    - ``force=True`` 才允许拿 0（Bloom 的输入，会糊画面）。
    """
    limit = maxUnits()
    used = _LEASED
    # 1) 高于引擎占用范围的真·空闲单元（从高往低，留给引擎低编号）
    for unit in range(limit - 1, max(ENGINE_UNITS) , -1):
        if unit not in used:
            used[unit] = owner
            return unit
    # 2) 引擎的"软"单元
    for unit in sorted(ENGINE_SOFT_UNITS):
        if unit in used or unit >= limit:
            continue
        used[unit] = owner
        return unit
    # 3) 兜底：0
    if force and 0 not in used and limit > 0:
        used[0] = owner
        return 0
    raise RuntimeError(
        "image unit 用光了（本上下文只有 %d 个）：%s。"
        "先 release() 掉不用的纹理，或者用 `with ImageUnits.lease(...)` 短租；"
        "长期占着 0 还会让引擎的 Bloom 链画面糊掉。"
        % (limit, "、".join("unit %d <- %r" % (u, o)
                            for u, o in sorted(used.items())) or "（都空着，是编号不够用）"))


def free(unit, owner=None) -> bool:
    """还掉一个单元（``owner`` 给了就核对是不是它租的）。"""
    unit = int(unit)
    current = _LEASED.get(unit)
    if current is None:
        return False
    if owner is not None and current is not owner:
        return False
    del _LEASED[unit]
    return True


def release(owner) -> int:
    """还掉某个占用者的所有单元，返回还了几个。"""
    units = [unit for unit, holder in _LEASED.items() if holder is owner]
    for unit in units:
        del _LEASED[unit]
    return len(units)


def reset() -> None:
    """清空记账（换 GL 上下文之后调用：GL 对象都没了）。"""
    _LEASED.clear()
    _MAX[0] = None


class lease(object):
    """``with ImageUnits.lease("谁") as unit: ...`` —— 退出时自动还。"""

    __slots__ = ("owner", "force", "unit")

    def __init__(self, owner=None, force: bool = False) -> None:
        self.owner = owner
        self.force = bool(force)
        self.unit = None

    def __enter__(self) -> int:
        self.unit = alloc(self.owner, force=self.force)
        return self.unit

    def __exit__(self, *exc) -> bool:
        if self.unit is not None:
            free(self.unit, owner=self.owner)
            self.unit = None
        return False
