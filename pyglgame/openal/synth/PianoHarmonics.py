"""谐波 / 频谱模型：``音高 + 力度 → 频率、幅度、衰减时间``。

一个钢琴音的**频谱**由三件事决定，本模块就是这三件事的公式：

1. **部分音频率（谐波 + 不谐和）**：真钢琴的弦有刚度，所以第 ``n`` 个部分音不是
   整数倍而是

   .. math:: f_n = n \\cdot f_0 \\cdot \\sqrt{1 + B \\cdot n^2}

   ``B`` 是不谐和系数（弦越短越粗越大 —— 高音区明显，低音区几乎为 0）。
   它的效果是"高次谐波整体偏高"，也是钢琴调律要"拉伸"的原因。

2. **部分音幅度（音色）**：

   - 击弦点梳状：``|sin(π·n·p)|`` —— 锤子打在 1/8 弦长处，第 8 个谐波附近会被抵消
     （``notch_floor`` 用来填平理想零点，真实琴不会真的消失）；
   - 整体包络 ``1/n^γ``；
   - **锤子等效低通** ``1/(1+(f/f_c)^2)^{k/2}``，截止频率 ``f_c`` 随**力度**变大 ——
     这就是"轻按音暗、重按音亮"（力度 → 音色）。

3. **衰减时间**：每个部分音一个 ``T60``。基频的 ``T60`` 随音高变短
   （低音几十秒、高音不足 1 秒）；高次谐波因为换能损失 ∝ 频率而衰得更快：

   .. math:: \\frac{1}{T60_n} = \\frac{1}{T60_1} + k_1 (f_n - f_1) + k_2 (f_n^2 - f_1^2)

.. code-block:: python

    from pyglgame.openal.synth import PianoHarmonics

    h = PianoHarmonics()                       # 默认 = 三角琴
    h.pitchFrequency(69)                       # → 440.0
    h.partialFrequencies(60)                   # → [261.6, 523.7, 786.3, ...]（略高于整数倍）
    h.partialAmplitudes(60, velocity=110)      # → 每个谐波的相对幅度（最强者 = 1）
    h.partialDecayT60(60)                      # → 每个谐波的 T60（秒）
"""

import math

import numpy as np

from .Filters import pitchPan
from .PianoPreset import PianoPreset


class PianoHarmonics:
    """按 :class:`PianoPreset` 计算"某个音高 / 力度下有哪些谐波"。"""

    def __init__(self, preset: PianoPreset = None) -> None:
        self.preset = preset if preset is not None else PianoPreset()

    # === 音高 → 频率 ===

    @staticmethod
    def pitchFrequency(pitch: float) -> float:
        """MIDI 音符号 → 频率（A4 = 440Hz，十二平均律）。"""
        return float(440.0 * (2.0 ** ((float(pitch) - 69.0) / 12.0)))

    def inharmonicity(self, pitch: float) -> float:
        """不谐和系数 ``B``（越大 → 高次谐波偏高越多）。"""
        preset = self.preset
        f0 = self.pitchFrequency(pitch)
        base = preset.inharmonicity_base \
            * (f0 / preset.inharmonicity_reference) ** preset.inharmonicity_power
        return float(min(max(base, preset.inharmonicity_min), preset.inharmonicity_max))

    # === 部分音（谐波）===

    def partialIndex(self, pitch: float, count: int = None) -> np.ndarray:
        """谐波序号 ``[1, 2, 3, ...]``（``count`` 默认由 :meth:`partialCount` 决定）。"""
        if count is None:
            count = self.partialCount(pitch)
        return np.arange(1, int(count) + 1, dtype=np.int64)

    def partialFrequencies(self, pitch: float, count: int = None) -> np.ndarray:
        """每个部分音的频率（含不谐和；``f_n >= n·f0``）。"""
        index = self.partialIndex(pitch, count).astype(np.float64)
        f0 = self.pitchFrequency(pitch)
        b = self.inharmonicity(pitch)
        return index * f0 * np.sqrt(1.0 + b * index * index)

    def partialCount(self, pitch: float) -> int:
        """要合成几个部分音。

        判定用的是**最大力度**（截止频率最高）的幅度：低于
        ``partial_floor_db`` 或者超过 ``0.5·sr·nyquist_ratio`` 就不再算 ——
        这样算力花在听得见的谐波上，也让"算几个"与力度无关（性能可预测）。
        """
        preset = self.preset
        f0 = self.pitchFrequency(pitch)
        b = self.inharmonicity(pitch)
        nyquist = 0.5 * preset.sample_rate * preset.nyquist_ratio
        floor = 10.0 ** (preset.partial_floor_db / 20.0)
        cutoff = preset.brightness_cutoff_max
        order = preset.brightness_filter_order / 2.0
        count = 1
        for n in range(1, int(preset.max_partials) + 1):
            frequency = n * f0 * math.sqrt(1.0 + b * n * n)
            if frequency >= nyquist:
                break
            comb = preset.notch_floor + (1.0 - preset.notch_floor) \
                * abs(math.sin(math.pi * preset.strike_position * n))
            amplitude = comb / (n ** preset.spectral_rolloff) \
                / (1.0 + (frequency / cutoff) ** 2) ** order
            if n > 1 and amplitude < floor:
                break
            count = n
        return count

    # === 频谱（幅度）===

    def brightnessCutoff(self, velocity: float) -> float:
        """力度 → "锤子等效低通"截止频率（Hz）：越重越亮。"""
        preset = self.preset
        u = min(max(float(velocity), 0.0), float(preset.velocity_max)) / float(preset.velocity_max)
        u = u ** preset.brightness_velocity_power
        return float(preset.brightness_cutoff_min
                     * (preset.brightness_cutoff_max / preset.brightness_cutoff_min) ** u)

    def hammerLowpass(self, frequencies, cutoff: float) -> np.ndarray:
        """锤子等效低通的幅度响应：``1/(1+(f/fc)²)^{order/2}``。"""
        ratio = np.asarray(frequencies, dtype=np.float64) / float(cutoff)
        return 1.0 / (1.0 + ratio * ratio) ** (self.preset.brightness_filter_order / 2.0)

    def strikeComb(self, index) -> np.ndarray:
        """击弦点梳状谱 ``notch_floor + (1-notch_floor)·|sin(π n p)|``。"""
        preset = self.preset
        n = np.asarray(index, dtype=np.float64)
        raw = np.abs(np.sin(math.pi * preset.strike_position * n))
        return preset.notch_floor + (1.0 - preset.notch_floor) * raw

    def partialAmplitudes(self, pitch: float, velocity: float = 127,
                          count: int = None, normalize: bool = True) -> np.ndarray:
        """每个部分音的相对幅度（默认归一化到最强 = 1）。"""
        preset = self.preset
        index = self.partialIndex(pitch, count).astype(np.float64)
        frequencies = self.partialFrequencies(pitch, count)
        amplitudes = self.strikeComb(index) / (index ** preset.spectral_rolloff)
        amplitudes = amplitudes * self.hammerLowpass(
            frequencies, self.brightnessCutoff(velocity))
        if normalize and amplitudes.size:
            top = float(np.max(amplitudes))
            if top > 0.0:
                amplitudes = amplitudes / top
        return amplitudes

    def spectralCentroid(self, pitch: float, velocity: float = 127) -> float:
        """频谱质心（Hz）：调"力度 → 亮度"时看它最直观 —— 力度越大越靠右。"""
        frequencies = self.partialFrequencies(pitch)
        amplitudes = self.partialAmplitudes(pitch, velocity)
        total = float(np.sum(amplitudes))
        if total <= 0.0:
            return 0.0
        return float(np.sum(frequencies * amplitudes) / total)

    # === 衰减（响度曲线的时间尺度）===

    def partialDecayT60(self, pitch: float, count: int = None) -> np.ndarray:
        """每个部分音的 ``T60``（秒）：高次谐波更快衰完。"""
        preset = self.preset
        frequencies = self.partialFrequencies(pitch, count)
        f0 = self.pitchFrequency(pitch)
        base = preset.decay_t60_reference \
            * (preset.decay_reference_frequency / f0) ** preset.decay_pitch_power
        base = min(max(base, preset.decay_t60_min), preset.decay_t60_max)
        inverse = 1.0 / base \
            + preset.decay_damping_per_hz * (frequencies - frequencies[0]) \
            + preset.decay_damping_per_hz2 * (frequencies ** 2 - frequencies[0] ** 2)
        inverse = np.maximum(inverse, 1e-6)
        return 1.0 / inverse

    def partialDecayTau(self, pitch: float, count: int = None) -> np.ndarray:
        """每个部分音的时间常数 τ（``T60`` / ln1000）。"""
        return self.partialDecayT60(pitch, count) / math.log(1000.0)

    def naturalSeconds(self, pitch: float, amplitudes=None, taus=None) -> float:
        """自然衰减到 ``silence_db`` 需要多久（踏板音 / 长按音的时长）。

        用"各谐波幅度之和"做包络（与相位无关 → 是真实包络的上界，长度略保守）。
        """
        preset = self.preset
        if amplitudes is None:
            amplitudes = self.partialAmplitudes(pitch, preset.velocity_max)
        if taus is None:
            taus = self.partialDecayTau(pitch)
        amplitudes = np.asarray(amplitudes, dtype=np.float64).reshape(-1, 1)
        taus = np.maximum(np.asarray(taus, dtype=np.float64).reshape(-1, 1), 1e-6)
        peak = float(np.sum(amplitudes))
        if peak <= 0.0:
            return min(0.25, float(preset.max_duration))
        step = 0.02
        times = np.arange(0.0, preset.max_duration + step, step)
        slow = np.exp(-times[None, :] / taus)
        weight = float(preset.double_decay_weight)
        if weight > 0.0:
            fast = np.exp(-times[None, :] / (taus * float(preset.double_decay_ratio)))
            envelope = amplitudes * ((1.0 - weight) * slow + weight * fast)
        else:
            envelope = amplitudes * slow
        level = np.sum(envelope, axis=0) / peak
        below = np.nonzero(20.0 * np.log10(level + 1e-12) < preset.silence_db)[0]
        end = float(times[below[0]]) if below.size else float(preset.max_duration)
        return min(end, float(preset.max_duration))

    # === 起始 / 抬键 ===

    def attackSeconds(self, pitch: float) -> float:
        """锤击接触时间（秒）：低音 3~5ms、高音不到 1ms。"""
        preset = self.preset
        f0 = self.pitchFrequency(pitch)
        ms = preset.attack_ms \
            * (preset.attack_reference_frequency / f0) ** preset.attack_pitch_power
        return float(min(max(ms, preset.attack_ms_min), preset.attack_ms_max)) / 1000.0

    def releaseT60(self, pitch: float) -> float:
        """松键后制音器的 ``T60``（秒）：低音制音慢、高音按得死。"""
        preset = self.preset
        f0 = self.pitchFrequency(pitch)
        seconds = preset.release_t60 \
            * (preset.release_reference_frequency / f0) ** preset.release_pitch_power
        return float(min(max(seconds, preset.release_t60_min), preset.release_t60_max))

    # === 齐奏（一音多弦）===

    def unisonCount(self, pitch: float) -> int:
        """这个音由几根弦齐奏（低音 1 根 / 中音 2 根 / 高音 3 根）。"""
        low, mid = self.preset.unison_pitch_split
        pitch = int(pitch)
        if pitch < low:
            return 1
        if pitch < mid:
            return 2
        return 3

    def unisonLayout(self, pitch: float) -> list:
        """每根弦一份 ``(失谐音分, 增益, 衰减倍率)``（增益均值归一 = 1）。"""
        preset = self.preset
        count = self.unisonCount(pitch)
        detune = preset.unison_detune_cents
        gain_spread = preset.unison_gain_spread
        decay_spread = preset.unison_decay_spread
        if count == 1:
            layout = [(0.0, 1.0, 1.0)]
        elif count == 2:
            layout = [(-detune, 1.0, 1.0),
                      (detune, 1.0 - 2.0 * gain_spread, 1.0 + decay_spread)]
        else:
            layout = [(-detune, 1.0, 1.0),
                      (0.0, 1.0 - gain_spread, 1.0 + decay_spread),
                      (detune, 1.0 - 2.0 * gain_spread, 1.0 - decay_spread)]
        mean = sum(item[1] for item in layout) / len(layout)
        if mean > 0.0:
            layout = [(cents, gain / mean, scale) for cents, gain, scale in layout]
        return layout

    # === 音域均衡 / 声像 ===

    def registerGain(self, pitch: float) -> float:
        """音域整体增益（线性）：高音区轻一点，整首曲子听感更均匀。"""
        preset = self.preset
        octaves = (float(pitch) - float(preset.register_center_pitch)) / 12.0
        db = preset.register_tilt_db_per_octave * octaves
        limit = float(preset.register_tilt_limit_db)
        db = min(max(db, -limit), limit)
        return float(10.0 ** (db / 20.0))

    def pan(self, pitch: float, width: float = None) -> float:
        """音高 → 声像（默认取 ``preset.pan_width``）。"""
        preset = self.preset
        width = preset.pan_width if width is None else float(width)
        return pitchPan(pitch, width, preset.register_center_pitch)

    # === 噪声 ===

    def hammerNoise(self, pitch: float, velocity: float = 127) -> tuple:
        """击弦噪声 ``(中心 Hz, 带宽倍频程, 相对电平)``。

        中心频率随音高上移：低音是"闷响"（几百 Hz）、高音是"咔哒"（几 kHz）。
        """
        preset = self.preset
        f0 = self.pitchFrequency(pitch)
        center = preset.hammer_noise_center_hz \
            * (f0 / preset.inharmonicity_reference) ** preset.hammer_noise_center_power
        center = min(max(center, preset.hammer_noise_center_min),
                     preset.hammer_noise_center_max)
        u = min(max(float(velocity), 0.0), float(preset.velocity_max)) / float(preset.velocity_max)
        level = preset.hammer_noise_level * (u ** preset.hammer_noise_velocity_power)
        return float(center), float(preset.hammer_noise_octaves), float(level)

    def keyNoise(self, velocity: float = 127) -> tuple:
        """键 / 机械噪声 ``(中心 Hz, 带宽倍频程, 相对电平)``：轻按时才听得见。"""
        preset = self.preset
        u = min(max(float(velocity), 0.0), float(preset.velocity_max)) / float(preset.velocity_max)
        level = preset.key_noise_level * ((1.0 - u) ** 1.5)
        return (float(preset.key_noise_center_hz), float(preset.key_noise_octaves), float(level))
