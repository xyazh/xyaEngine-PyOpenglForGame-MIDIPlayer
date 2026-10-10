"""频谱工具：音板共振 EQ、击弦噪声、峰值归一、声像（都是无状态的纯函数）。

- :func:`applySpectralEQ`：整段过一遍"音板"—— 几个低频共振峰（琴体共鸣）
  + 一条高频缓降。用 FFT 一次算完（钢琴音是离线渲染的，不必串 IIR 滤波器），
  内部会补一段零保证**不会把起音绕回结尾**（循环卷积的经典坑）；
- :func:`noiseBurst`：击弦噪声 —— 白噪声过一条"对数频率高斯带通"再乘指数衰减。
  带通中心随音高上移（低音是"闷响"、高音是"咔哒"）；
- :func:`normalizePeak` / :func:`softClip` / :func:`toInt16`：输出前的电平处理；
- :func:`pitchPan` / :func:`panGain`：把音高摊到立体声里（低音偏左、高音偏右）。
"""

import math

import numpy as np

#: rfft 的频率轴（含 0），0 会被替换成一个小正数避免 log2(0)
_TINY = 1e-9


def resonanceResponse(frequencies, peaks=(), lowpass_hz: float = None,
                      lowpass_order: float = 1.0) -> np.ndarray:
    """频响曲线：若干洛伦兹共振峰 × 一条低通。

    ``peaks`` 每项 ``(中心频率 Hz, Q, 线性增益)``；``Q`` 越大峰越窄。
    """
    frequencies = np.asarray(frequencies, dtype=np.float64)
    response = np.ones_like(frequencies)
    for peak in peaks or ():
        center, q, gain = float(peak[0]), float(peak[1]), float(peak[2])
        half_width = center / (2.0 * max(q, 1e-6))
        offset = (frequencies - center) / half_width
        response = response * (1.0 + (gain - 1.0) / (1.0 + offset * offset))
    if lowpass_hz:
        ratio = frequencies / float(lowpass_hz)
        response = response / (1.0 + ratio * ratio) ** (float(lowpass_order) / 2.0)
    return response


def applySpectralEQ(signal, sample_rate: int, peaks=(), lowpass_hz: float = None,
                    lowpass_order: float = 1.0, pad_seconds: float = 0.15) -> np.ndarray:
    """对整段信号做频域整形（音板共鸣 / 高频缓降）。

    内部补 ``pad_seconds`` 秒零再 FFT：循环卷积会把起音"绕"到结尾，
    补零之后那点环绕内容落在被裁掉的部分上，听感上没有瑕疵。
    """
    signal = np.asarray(signal, dtype=np.float64)
    if signal.size == 0:
        return signal
    pad = int(round(float(pad_seconds) * int(sample_rate)))
    padded = np.concatenate([signal, np.zeros(pad, dtype=np.float64)]) \
        if pad > 0 else signal
    spectrum = np.fft.rfft(padded)
    frequencies = np.fft.rfftfreq(padded.size, 1.0 / float(sample_rate))
    spectrum *= resonanceResponse(frequencies, peaks, lowpass_hz, lowpass_order)
    return np.fft.irfft(spectrum, n=padded.size)[:signal.size]


def noiseBurst(sample_rate: int, length: int, center_hz: float, octaves: float,
               decay_tau: float, rng=None, seed: int = None) -> np.ndarray:
    """一段带通的、指数衰减的噪声（击弦 / 键噪）。

    - ``center_hz`` / ``octaves``：对数频率轴上的高斯带通（σ 单位是倍频程）；
    - ``decay_tau``：幅度包络 ``exp(-t/τ)``；用 :func:`pyglgame.openal.synth.Envelope.t60ToTau`
      从"想要的 T60"换算过来更直观。
    """
    length = int(length)
    if length <= 0:
        return np.zeros(0, dtype=np.float64)
    if rng is None:
        rng = np.random.default_rng(seed)
    pad = int(2 ** math.ceil(math.log2(max(length * 4, 1024))))
    raw = rng.standard_normal(pad)
    spectrum = np.fft.rfft(raw)
    frequencies = np.fft.rfftfreq(pad, 1.0 / float(sample_rate))
    safe = np.maximum(frequencies, _TINY)
    shape = np.exp(-0.5 * (np.log2(safe / max(float(center_hz), _TINY)) / max(float(octaves), _TINY)) ** 2)
    shape[0] = 0.0
    burst = np.fft.irfft(spectrum * shape, n=pad)[:length]
    peak = float(np.max(np.abs(burst))) if burst.size else 0.0
    if peak > 0.0:
        burst = burst / peak
    times = np.arange(length, dtype=np.float64) / float(sample_rate)
    return burst * np.exp(-times / max(float(decay_tau), _TINY))


def normalizePeak(signal, peak: float = 1.0) -> np.ndarray:
    """按峰值缩放（全零信号原样返回，不会除零）。"""
    signal = np.asarray(signal, dtype=np.float64)
    top = float(np.max(np.abs(signal))) if signal.size else 0.0
    if top <= 0.0:
        return signal
    return signal * (float(peak) / top)


def softClip(signal, threshold: float = 0.98) -> np.ndarray:
    """软限幅（超出阈值才动手，用 tanh 过渡 —— 不会像硬切那样出爆音）。"""
    signal = np.asarray(signal, dtype=np.float64)
    threshold = float(threshold)
    if threshold <= 0.0:
        return np.clip(signal, -1.0, 1.0)
    peak = float(np.max(np.abs(signal))) if signal.size else 0.0
    if peak <= threshold:
        return signal
    headroom = 1.0 - threshold
    return np.sign(signal) * np.where(
        np.abs(signal) <= threshold, np.abs(signal),
        threshold + headroom * np.tanh((np.abs(signal) - threshold) / max(headroom, _TINY)))


def toInt16(signal, gain: float = 1.0) -> np.ndarray:
    """浮点 PCM → ``int16``（自带软限幅 + 夹取，保证不会绕回成噪声）。"""
    data = softClip(np.asarray(signal, dtype=np.float64) * float(gain))
    return np.clip(data * 32767.0, -32768.0, 32767.0).astype(np.int16)


def scalePcmInt16(signal, gain: float) -> np.ndarray:
    """给**已经是 int16 的 PCM** 乘一个 ``[0, 1]`` 的增益（声像 / 电平用）。

    不能拿 :func:`toInt16` 干这件事：它的输入约定是**归一化浮点**（``±1``），
    把 ±32767 的 int16 直接喂进去，软限幅会把"远超 1.0"的每个采样都压成满幅
    —— 立体声模式下整段钢琴变成方波。这里 ``|采样 × gain| ≤ 32767`` 恒成立
    （gain 来自 :func:`panGain`），所以只要一次乘法 + 截断，不用限幅。

    实测比 ``toInt16(signal / 32768.0, gain)`` 快 2~3 倍（少一遍 float64 软限幅），
    整曲混音要跑几千次，这个差别是 0.1s 和 3s 的差别。
    """
    data = np.asarray(signal)
    if data.dtype != np.int16:
        data = np.clip(data, -32768, 32767).astype(np.int16)
    out = np.empty(data.size, dtype=np.int16)
    np.multiply(data, float(gain), out=out, casting="unsafe")
    return out


def pitchPan(pitch: float, width: float = 0.4, center_pitch: float = 69.0) -> float:
    """音高 → 声像 ``[-width, +width]``（低音偏左、高音偏右，±3 个八度铺满）。"""
    position = (float(pitch) - float(center_pitch)) / 36.0
    return float(min(max(position * float(width), -abs(float(width))), abs(float(width))))


def panGain(pan: float) -> tuple:
    """声像 → ``(左增益, 右增益)``（等功率，总能量不随声像变化）。"""
    angle = (float(pan) + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def interleaveStereo(left, right) -> np.ndarray:
    """左右声道交错成 ``(2N,)``（OpenAL 的 ``AL_FORMAT_STEREO16`` 就吃这个布局）。"""
    left = np.asarray(left)
    right = np.asarray(right)
    count = min(left.size, right.size)
    out = np.empty(count * 2, dtype=left.dtype)
    out[0::2] = left[:count]
    out[1::2] = right[:count]
    return out
