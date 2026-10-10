"""响度包络：把"这一段声音有多响"写成时间 → 线性幅度的函数。

钢琴的响度曲线由三段拼接而成（这也是它听起来像钢琴的原因）：

1. **起始（锤击）**：锤子压在弦上几毫秒，弦被"推"起来 —— 包络从 0 平滑升到 1
   （:func:`attackEnvelope`，smoothstep，一阶导连续 → 不会有咔哒声）；
2. **自然衰减（双段）**：每个部分音有两个极化方向，垂直（垂直于音板）那一路
   衰减慢、平行那一路衰减快，于是"先快掉一段、再拖长尾"
   （:func:`decayEnvelope`，两个指数按权重叠加）；
3. **抬键（制音器）**：松键后毛毡把弦按住，整体再乘一段快得多的指数
   （:func:`releaseEnvelope`）。

另外提供 **dB ↔ 线性幅度** 与 **力度 → 响度** 的换算：
钢琴的力度不是线性映射到幅度，而是"每档差几 dB"，所以这里先算 dB 再转幅度
（:func:`velocityToGain`，``preset.loudness_min_db`` 就是 pp 与 ff 的响度差）。
"""

import math

import numpy as np

#: ``T60``（衰减 60dB 的时间）与指数时间常数的换算系数：exp(-T60/τ) = 1e-3
LN1000 = math.log(1000.0)


def dbToGain(db: float) -> float:
    """dB → 线性幅度。"""
    return float(10.0 ** (float(db) / 20.0))


def gainToDb(gain: float, floor: float = -240.0) -> float:
    """线性幅度 → dB（0/负数会被夹到 ``floor``，不会抛异常）。"""
    gain = float(gain)
    if gain <= 0.0:
        return float(floor)
    return max(float(floor), 20.0 * math.log10(gain))


def t60ToTau(t60: float) -> float:
    """``T60`` → 指数时间常数 τ（``exp(-t/τ)`` 在 ``T60`` 处掉 60dB）。"""
    return float(t60) / LN1000


def tauToT60(tau: float) -> float:
    """τ → ``T60``。"""
    return float(tau) * LN1000


def velocityToGain(velocity, preset) -> float:
    """MIDI 力度 → 线性幅度（**响度曲线**，不是线性的）。"""
    top = float(preset.velocity_max)
    u = min(max(float(velocity), 0.0), top) / top
    db = preset.loudness_max_db + (preset.loudness_min_db - preset.loudness_max_db) \
        * (1.0 - u ** preset.loudness_curve)
    return dbToGain(db)


def velocityToDb(velocity, preset) -> float:
    """:func:`velocityToGain` 的 dB 版本（调参/测试用）。"""
    top = float(preset.velocity_max)
    u = min(max(float(velocity), 0.0), top) / top
    return preset.loudness_max_db + (preset.loudness_min_db - preset.loudness_max_db) \
        * (1.0 - u ** preset.loudness_curve)


def smoothstep(x):
    """``3x² - 2x³``（夹到 [0,1]）：两端一阶导为 0，跨过 t=0 不会有咔哒。"""
    x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def attackEnvelope(times, attack_seconds: float):
    """起始包络：``0 → 1`` 的 smoothstep（``attack_seconds`` 之后恒为 1）。"""
    times = np.asarray(times, dtype=np.float64)
    attack = float(attack_seconds)
    if attack <= 0.0:
        return np.ones_like(times)
    return smoothstep(times / attack)


def decayEnvelope(times, taus, fast_weight: float = 0.0, fast_ratio: float = 0.12):
    """自然衰减包络（双段指数），形状 ``(taus.size, times.size)``。

    ``env(t) = (1-w)·exp(-t/τ) + w·exp(-t/(τ·ratio))``

    - ``taus`` 是每个部分音自己的 τ（由 ``PianoHarmonics`` 给出：高次谐波更小）；
    - ``fast_weight`` / ``fast_ratio`` 就是"双段衰减"：``w=0`` 退化成单指数。
    """
    times = np.asarray(times, dtype=np.float64)
    taus = np.asarray(taus, dtype=np.float64).reshape(-1, 1)
    taus = np.maximum(taus, 1e-6)
    slow = np.exp(-times[None, :] / taus)
    weight = float(fast_weight)
    if weight <= 0.0:
        return slow
    fast = np.exp(-times[None, :] / (taus * max(float(fast_ratio), 1e-6)))
    return (1.0 - weight) * slow + weight * fast


def releaseEnvelope(times, release_start: float, release_tau: float):
    """抬键（制音器）包络：``release_start`` 之前是 1，之后按 τ 指数掉下去。"""
    times = np.asarray(times, dtype=np.float64)
    start = float(release_start)
    after = np.maximum(times - start, 0.0)
    tail = np.exp(-after / max(float(release_tau), 1e-6))
    return np.where(times < start, 1.0, tail)


def envelopeIndices(count: int, stride: int):
    """粗网格线性插值用的下标/比例（对整个音长只算一次）。

    返回 ``(lo, frac)``：``env[i] = coarse[lo[i]]·(1-frac[i]) + coarse[lo[i]+1]·frac[i]``。
    """
    stride = max(1, int(stride))
    positions = np.arange(int(count), dtype=np.float64) / stride
    lo = positions.astype(np.int64)
    frac = positions - lo
    return lo, frac


def expandEnvelope(coarse, lo, frac):
    """把粗网格包络 ``coarse``（``(M, coarse_count)``）插值到每个采样点。"""
    lo = np.asarray(lo, dtype=np.int64)
    frac = np.asarray(frac, dtype=np.float64)
    return coarse[:, lo] * (1.0 - frac) + coarse[:, lo + 1] * frac


def tailFade(count: int, sample_rate: int, fade_ms: float):
    """结尾淡出窗（长度为 ``count`` 的系数数组）：保证波形以 0 结束。"""
    fade = int(round(max(0.0, float(fade_ms)) * 0.001 * int(sample_rate)))
    window = np.ones(int(count), dtype=np.float64)
    fade = min(fade, int(count))
    if fade > 1:
        window[-fade:] = 0.5 * (1.0 + np.cos(np.linspace(0.0, math.pi, fade)))
    return window
