"""单音钢琴：从"键按下去"到"声音消失"的一整段波形（浮点 PCM）。

.. code-block:: python

    from pyglgame.openal.synth import PianoVoice

    voice = PianoVoice(pitch=60, velocity=96, duration=0.8)   # 中音 C，按 0.8 秒
    voice.samples                  # → 单声道浮点 PCM（长度 = 按键时长 + 抬键尾巴）
    voice.partials, voice.strings  # 用了几根谐波 / 几根弦
    voice.loudnessCurveDb(256)     # → (时间, dB)：响度起始 / 衰减曲线（画图 / 调参用）

    PianoVoice(pitch=33, velocity=110, pedal=True)   # 踩住踏板：不抬键，让它自然衰完

渲染顺序
--------

1. **加法（模态）合成**：每条"谐波 × 齐奏弦"一条正弦 —— 频率来自不谐和公式、
   幅度来自击弦梳状 + ``1/n^γ`` + 锤子低通、包络是双段指数。
   包络走**粗网格 + 线性插值**（指数变化很慢，每 32 个采样点插值一次听不出来，
   却省掉一大半 ``exp``）；
2. **起始 / 抬键**：整段乘一个全局包络（smoothstep 起音 × 制音器衰减）
   —— 放在求和之后做，省掉"每个谐波算一遍"；
3. **击弦噪声 / 键噪**：带通噪声（幅度 ∝ 力度）叠在最前面几毫秒；
4. **音板**：整段过频域 EQ（低频共振峰 + 高频缓降）；
5. **电平**：峰值归一 → 乘"力度 → 响度"增益与音域增益，
   末尾一定淡出到 0（换音 / 掐断都不会咔哒）。

性能
----

成本 ≈ 谐波数 × 弦数 × 采样点数：中音区一个 1 秒的音约 20~40ms（numpy 向量化）。
所以**一个音只该渲染一次**：播放请走
:class:`~pyglgame.openal.synth.Synthesizer.Synthesizer`（自带缓存）。
"""

import math
import time

import numpy as np

from .Envelope import (attackEnvelope, decayEnvelope, envelopeIndices, releaseEnvelope,
                       smoothstep, t60ToTau, tailFade, velocityToGain)
from .Filters import applySpectralEQ, noiseBurst, normalizePeak, toInt16
from .PianoHarmonics import PianoHarmonics
from .PianoPreset import PianoPreset

#: 输出峰值余量（-1dB）：归一化到这个值再乘力度增益，转 int16 时绝不会削顶
OUTPUT_PEAK = 0.89

#: 击弦噪声最长合成多久（秒）：噪声 τ 只有几毫秒，再长也没有能量，纯浪费 FFT
NOISE_WINDOW_SECONDS = 0.06

#: 加法合成的分段长度（秒）：每段开头把"已经衰到听不见"的谐波剔掉
SEGMENT_SECONDS = 0.25

#: 击弦 / 键噪的上升时间（秒）：只要保证第 0 个采样点是 0（不然是一声咔哒）
NOISE_ATTACK_SECONDS = 0.0004

#: 整段波形最前面的淡入（秒）：音板 EQ 是零相位滤波，会把第 0 个采样点抬起来
ONSET_FADE_SECONDS = 0.0006


class PianoVoice:
    """一个钢琴音（构造时就渲染好，之后只读）。"""

    def __init__(self, pitch, velocity=100, duration=None, pedal=False,
                 preset: PianoPreset = None, seed: int = None) -> None:
        self.preset = preset if preset is not None else PianoPreset()
        pitch = int(pitch)
        if not 0 <= pitch <= 127:
            raise ValueError("pitch 必须是 0~127 的 MIDI 音符号（收到 %r）" % (pitch,))
        velocity = int(velocity)
        if velocity < 0:
            raise ValueError("velocity 不能为负（收到 %r）" % (velocity,))
        self.pitch = pitch
        #: 力度 0 会被夹到 1（MIDI 里 velocity=0 是 note_off）
        self.velocity = int(min(max(velocity, 1), self.preset.velocity_max))
        if duration is not None:
            duration = float(duration)
            if not math.isfinite(duration) or duration <= 0.0:
                raise ValueError("duration 必须是正的秒数（收到 %r）" % (duration,))
        else:
            duration = None
        self.duration = duration
        self.pedal = bool(pedal)
        self.seed = int(self.preset.seed if seed is None else seed)
        self.harmonics = PianoHarmonics(self.preset)
        self.pan = self.harmonics.pan(self.pitch)
        self.sample_rate = int(self.preset.sample_rate)
        self.partials = self.harmonics.partialCount(self.pitch)
        self.strings = self.harmonics.unisonCount(self.pitch)
        #: 抬键时刻（秒）；踏板音 / 不指定 duration 时为 None（自然衰完）
        self.release_start = None
        #: 自然衰减到静音门限要多久（踏板音时长就用它）
        self.natural_seconds = 0.0
        self.seconds = 0.0
        self.peak = 0.0
        self.render_ms = 0.0
        self.samples = np.zeros(0, dtype=np.float64)
        self.render()

    # === 渲染 ===

    def render(self) -> np.ndarray:
        """（重新）渲染这个音，返回单声道浮点 PCM。"""
        started = time.perf_counter()
        preset = self.preset
        sample_rate = self.sample_rate
        harmonics = self.harmonics
        self.partials = harmonics.partialCount(self.pitch)
        self.strings = harmonics.unisonCount(self.pitch)
        amplitudes = harmonics.partialAmplitudes(self.pitch, self.velocity, self.partials)
        frequencies = harmonics.partialFrequencies(self.pitch, self.partials)
        taus = harmonics.partialDecayTau(self.pitch, self.partials)
        attack = harmonics.attackSeconds(self.pitch)
        self.natural_seconds = harmonics.naturalSeconds(self.pitch, amplitudes, taus)

        # --- 1) 时长：抬键（带尾巴）或自然衰完 ---
        release_tau = None
        if self.pedal or self.duration is None:
            self.release_start = None
            seconds = self.natural_seconds + attack
        else:
            release_tau = t60ToTau(harmonics.releaseT60(self.pitch))
            self.release_start = float(self.duration)
            seconds = self.duration + preset.release_tail_scale * release_tau
        seconds = float(min(max(seconds, 0.02), preset.max_duration))
        count = max(int(round(seconds * sample_rate)), 128)
        self.seconds = count / float(sample_rate)

        # --- 2) 全局包络（起音 × 制音器）---
        times = np.arange(count, dtype=np.float64) / float(sample_rate)
        envelope = attackEnvelope(times, attack)
        if self.release_start is not None:
            envelope *= releaseEnvelope(times, self.release_start, release_tau)

        # --- 3) 加法合成 + 瞬态 + 音板 ---
        signal = self._additive(count, times, frequencies, amplitudes, taus)
        signal *= envelope
        signal += self._transient(count, self._rawPeak(signal, envelope))
        signal = applySpectralEQ(signal, sample_rate, preset.soundboard_peaks,
                                 preset.soundboard_lowpass_hz,
                                 preset.soundboard_lowpass_order)

        # --- 4) 电平：峰值归一 → 力度（响度）/ 音域增益 → 首尾都归零 ---
        gain = velocityToGain(self.velocity, preset) \
            * harmonics.registerGain(self.pitch) * OUTPUT_PEAK
        signal = normalizePeak(signal, peak=gain)
        # 结尾淡出（防咔哒）+ 极短起音淡入：音板 EQ 是零相位滤波，会让第 0 个
        # 采样点被"未来"的样本抬起来（不为 0）—— 补一个 0.6ms 的淡入就干净了。
        signal *= tailFade(count, sample_rate, preset.tail_fade_ms)
        signal *= attackEnvelope(times, ONSET_FADE_SECONDS)
        self.peak = float(np.max(np.abs(signal))) if signal.size else 0.0
        self.samples = signal
        self.render_ms = (time.perf_counter() - started) * 1000.0
        return self.samples

    def _tracks(self, frequencies, amplitudes, taus):
        """展开成"谐波 × 齐奏弦"的轨道：``(角频率, 幅度, τ)`` 三个等长数组。"""
        preset = self.preset
        mu, amp, tau = [], [], []
        for cents, gain, decay_scale in self.harmonics.unisonLayout(self.pitch):
            mu.append(frequencies * (2.0 ** (cents / 1200.0)))
            amp.append(amplitudes * gain)
            tau.append(taus * decay_scale)
        mu = np.concatenate(mu)
        amp = np.concatenate(amp)
        tau = np.concatenate(tau)
        keep = (mu < 0.5 * self.sample_rate * preset.nyquist_ratio) & (amp > 0.0)
        return ((2.0 * math.pi * mu[keep]).astype(np.float32),
                amp[keep].astype(np.float32), tau[keep])

    def _additive(self, count, times, frequencies, amplitudes, taus) -> np.ndarray:
        """加法合成：逐块把（谐波 × 弦）叠加起来。

        长音会**分段剪枝**：某一时刻已经衰到 ``partial_floor_db`` 以下的轨道
        直接不参与这一段（它们的贡献听不见，但会实打实吃掉算力）。
        包络是单调递减的，所以某一段全静音就可以直接收工。
        """
        preset = self.preset
        omega, amp, tau = self._tracks(frequencies, amplitudes, taus)
        signal = np.zeros(count, dtype=np.float64)
        if omega.size == 0:
            return signal
        stride = max(1, int(preset.envelope_stride))
        coarse_count = count // stride + 2
        coarse_times = np.arange(coarse_count, dtype=np.float64) \
            * stride / float(self.sample_rate)
        # 块内运算用 float32：临时数组减半、sin 也更快（相位精度足够 —— 误差
        # 远小于一个周期）；累加缓冲仍是 float64，长音不会有累积舍入噪声。
        coarse = decayEnvelope(coarse_times, tau,
                               preset.double_decay_weight,
                               preset.double_decay_ratio).astype(np.float32)
        times32 = times.astype(np.float32)
        lo, frac = envelopeIndices(count, stride)
        frac = frac.astype(np.float32)
        block = max(64, int(preset.block_size))
        segment = max(block, int(SEGMENT_SECONDS * self.sample_rate))
        floor = 10.0 ** (preset.partial_floor_db / 20.0) * float(np.sum(amp))
        for seg_start in range(0, count, segment):
            seg_stop = min(seg_start + segment, count)
            anchor = min(seg_start // stride, coarse_count - 1)
            active = amp * coarse[:, anchor] > floor
            if not active.any():
                break
            omega_seg = omega[active][:, None]
            amp_seg = amp[active][:, None]
            coarse_seg = coarse[active]
            for start in range(seg_start, seg_stop, block):
                stop = min(start + block, seg_stop)
                slot = slice(start, stop)
                # 只取这一块需要的粗网格切片（连续内存 → 下面的取值命中 L2），
                # 直接对整张 (M, C) 粗网格乱序取值会慢好几倍（实测）。
                c0 = start // stride
                c1 = min(coarse_count, int(lo[stop - 1]) + 2)
                window = coarse_seg[:, c0:c1]
                local = lo[slot] - c0
                envelope = window[:, local] * (1.0 - frac[slot]) \
                    + window[:, local + 1] * frac[slot]
                envelope *= amp_seg
                phase = omega_seg * times32[slot][None, :]
                np.sin(phase, out=phase)
                phase *= envelope
                signal[slot] += phase.sum(axis=0)
        return signal

    def _transient(self, count: int, raw_peak: float) -> np.ndarray:
        """击弦噪声 + 键噪：``level`` 是相对"谐波之和峰值"的比例。"""
        preset = self.preset
        harmonics = self.harmonics
        noise = np.zeros(count, dtype=np.float64)
        if raw_peak <= 0.0:
            return noise
        rng = np.random.default_rng(self.seed)
        center, octaves, level = harmonics.hammerNoise(self.pitch, self.velocity)
        key_center, key_octaves, key_level = harmonics.keyNoise(self.velocity)
        length = min(count, max(64, int(NOISE_WINDOW_SECONDS * self.sample_rate)))
        if level > 0.0:
            noise[:length] += noiseBurst(self.sample_rate, length, center, octaves,
                                         t60ToTau(preset.hammer_noise_decay_ms / 1000.0),
                                         rng) * (level * raw_peak)
        if key_level > 0.0:
            noise[:length] += noiseBurst(self.sample_rate, length, key_center, key_octaves,
                                         t60ToTau(preset.key_noise_decay_ms / 1000.0),
                                         rng) * (key_level * raw_peak)
        # 噪声也要从 0 起（0.4ms 的极短上升）：否则第 0 个采样点就不为 0，
        # 播放时是一声"咔哒"（而不是击弦的那种"咔"）
        onset = np.minimum(1.0, np.arange(length, dtype=np.float64)
                           / max(1.0, NOISE_ATTACK_SECONDS * self.sample_rate))
        noise[:length] *= smoothstep(onset)
        return noise

    @staticmethod
    def _rawPeak(signal, envelope) -> float:
        """未乘全局包络时"谐波之和"的峰值（击弦噪声按它定标）。"""
        mask = envelope > 1e-6
        if not np.any(mask):
            return float(np.max(np.abs(signal))) if signal.size else 0.0
        raw = np.abs(signal[mask] / envelope[mask])
        return float(np.max(raw)) if raw.size else 0.0

    # === 分析（调参 / 演示 / 测试用）===

    def amplitudeEnvelope(self, count: int = 512, include_release: bool = True) -> np.ndarray:
        """解析幅度包络：``Σ 各谐波幅度 × 各自的双段衰减``。

        它与相位无关（看不到拍频凹陷），就是"这个音有多响"的曲线 ——
        也正是 :meth:`loudnessCurveDb` 画出来的东西。
        """
        preset = self.preset
        harmonics = self.harmonics
        amplitudes = harmonics.partialAmplitudes(self.pitch, self.velocity, self.partials)
        taus = harmonics.partialDecayTau(self.pitch, self.partials)
        total = self.seconds if self.seconds > 0.0 else 1.0
        times = np.linspace(0.0, total, int(max(count, 2)))
        wave = np.zeros(times.size, dtype=np.float64)
        for _cents, gain, decay_scale in harmonics.unisonLayout(self.pitch):
            decay = decayEnvelope(times, taus * decay_scale,
                                  preset.double_decay_weight, preset.double_decay_ratio)
            wave += np.sum(amplitudes[:, None] * gain * decay, axis=0)
        wave *= attackEnvelope(times, harmonics.attackSeconds(self.pitch))
        if include_release and self.release_start is not None:
            wave *= releaseEnvelope(times, self.release_start,
                                    t60ToTau(harmonics.releaseT60(self.pitch)))
        return wave

    def loudnessCurveDb(self, count: int = 256):
        """响度曲线 ``(时间, 相对峰值 dB)``：起音很陡 → 拖长尾 → 抬键再掉一段。"""
        envelope = self.amplitudeEnvelope(count)
        peak = float(np.max(envelope)) if envelope.size else 0.0
        if peak <= 0.0:
            peak = 1.0
        total = self.seconds if self.seconds > 0.0 else 1.0
        times = np.linspace(0.0, total, envelope.size)
        return times, 20.0 * np.log10(envelope / peak + 1e-12)

    def spectrum(self, start_seconds: float = 0.02, window_seconds: float = 0.35):
        """``(频率, 幅度)``：取一小段（默认起音后 0.02s 起 0.35s）做 rfft。

        用整段做 FFT 的话，衰减会把每个谐波峰抹宽；取前段才看得出峰在哪。
        """
        count = self.samples.size
        if count == 0:
            return np.zeros(0), np.zeros(0)
        start = min(int(max(0.0, start_seconds) * self.sample_rate), count - 1)
        length = max(2, min(int(max(64.0, window_seconds * self.sample_rate)), count - start))
        segment = self.samples[start:start + length]
        spectrum = np.abs(np.fft.rfft(segment * np.hanning(length)))
        frequencies = np.fft.rfftfreq(length, 1.0 / float(self.sample_rate))
        return frequencies, spectrum

    def partialTable(self) -> list:
        """``[(序号, 频率, 相对幅度, T60)]``：这次用了哪些谐波（调音色时很有用）。"""
        harmonics = self.harmonics
        frequencies = harmonics.partialFrequencies(self.pitch, self.partials)
        amplitudes = harmonics.partialAmplitudes(self.pitch, self.velocity, self.partials)
        t60 = harmonics.partialDecayT60(self.pitch, self.partials)
        return [(index + 1, float(frequencies[index]), float(amplitudes[index]),
                 float(t60[index])) for index in range(self.partials)]

    def pcm(self, gain: float = 1.0) -> np.ndarray:
        """单声道 ``int16`` PCM（自带软限幅 + 夹取）。"""
        return toInt16(self.samples, gain)

    def __repr__(self):
        return ("PianoVoice(pitch=%d, velocity=%d, duration=%r, pedal=%r, "
                "partials=%d, strings=%d, %.3fs)"
                % (self.pitch, self.velocity, self.duration, self.pedal,
                   self.partials, self.strings, self.seconds))
