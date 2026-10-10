"""钢琴预设：一份"这台钢琴是什么样"的参数表（可 ``copy`` 派生、有命名预设）。

.. code-block:: python

    from pyglgame.openal.synth import PianoPreset, PianoVoice

    voice = PianoVoice(pitch=60, velocity=96, duration=1.0,
                       preset=PianoPreset.honkyTonk())   # 走调的酒馆琴

参数分组（全部都有物理含义，调参 = 换琴）
----------------------------------------

- **谐波 / 频谱**：``max_partials``（最多算几根谐波）、``strike_position``
  （击弦点，决定哪些谐波被"梳状"抵消）、``spectral_rolloff``（``1/n^γ``）、
  ``brightness_cutoff_*`` + ``brightness_velocity_power``（**力度 → 亮度**：
  软锤等效低通低 = 音暗，硬锤高 = 音亮）；
- **不谐和**：``inharmonicity_*``（弦刚度 → 高次谐波偏高：
  ``f_n = n·f0·√(1 + B·n²)``，高音区更明显）；
- **衰减（响度曲线）**：``decay_t60_*``（基频 T60 随音高变短）、
  ``decay_damping_per_hz``（换能损失 ∝ 频率 → **高次谐波衰减更快**）、
  ``double_decay_*``（双段衰减：击弦后先快掉一段，再拖长尾）；
- **起始 / 抬键**：``attack_ms``（锤击接触时间）、``release_t60``
  （松键后制音器把弦按死的 T60）、``release_tail_scale``；
- **齐奏（一音多弦）**：``unison_pitch_split``（低音 1 弦 / 中音 2 弦 / 高音 3 弦）、
  ``unison_detune_cents``（弦间失谐 → 拍音与双段衰减）、``unison_decay_spread``；
- **力度 / 响度**：``loudness_min_db``（pp 比 ff 低多少 dB）、``loudness_curve``
  （力度 → 响度的曲线指数）、``register_*``（高音区整体轻一点，听感更均匀）；
- **噪声**：``hammer_noise_*``（击弦"咔哒"）、``key_noise_*``（轻按时的键噪）；
- **音板**：``soundboard_peaks``（几个低频共振峰）+ ``soundboard_lowpass_hz``；
- **渲染**：``silence_db``（自然衰减判定的静音门限）/ ``max_duration`` /
  ``tail_fade_ms`` / ``block_size`` / ``envelope_stride`` / ``seed``。
"""

import math

#: 允许出现在 ``PianoPreset(**)`` / ``copy(**)`` 里的参数名（写错会报错）
PARAMETERS = (
    # 输出
    "sample_rate", "stereo", "pan_width",
    # 谐波 / 频谱
    "max_partials", "nyquist_ratio", "partial_floor_db",
    "strike_position", "notch_floor", "spectral_rolloff",
    "brightness_cutoff_min", "brightness_cutoff_max",
    "brightness_velocity_power", "brightness_filter_order",
    # 不谐和
    "inharmonicity_base", "inharmonicity_reference", "inharmonicity_power",
    "inharmonicity_min", "inharmonicity_max",
    # 衰减
    "decay_t60_reference", "decay_reference_frequency", "decay_pitch_power",
    "decay_t60_min", "decay_t60_max",
    "decay_damping_per_hz", "decay_damping_per_hz2",
    "double_decay_weight", "double_decay_ratio",
    # 起始 / 抬键
    "attack_ms", "attack_reference_frequency", "attack_pitch_power",
    "attack_ms_min", "attack_ms_max",
    "release_t60", "release_reference_frequency", "release_pitch_power",
    "release_t60_min", "release_t60_max", "release_tail_scale",
    # 齐奏
    "unison_pitch_split", "unison_detune_cents",
    "unison_decay_spread", "unison_gain_spread",
    # 力度 / 响度
    "velocity_max", "loudness_min_db", "loudness_max_db", "loudness_curve",
    "register_center_pitch", "register_tilt_db_per_octave",
    "register_tilt_limit_db",
    # 噪声
    "hammer_noise_level", "hammer_noise_velocity_power", "hammer_noise_decay_ms",
    "hammer_noise_center_hz", "hammer_noise_center_power",
    "hammer_noise_center_min", "hammer_noise_center_max", "hammer_noise_octaves",
    "key_noise_level", "key_noise_center_hz", "key_noise_octaves",
    "key_noise_decay_ms",
    # 音板
    "soundboard_peaks", "soundboard_lowpass_hz", "soundboard_lowpass_order",
    # 渲染
    "silence_db", "max_duration", "tail_fade_ms", "block_size",
    "envelope_stride", "seed",
)


class PianoPreset:
    """一台钢琴的参数表（见模块文档）。只有数据，不持有任何音频资源。"""

    PARAMETERS = PARAMETERS

    def __init__(self, **overrides) -> None:
        # === 输出 ===
        self.sample_rate: int = 44100
        #: True = 按音高做立体声展开（离线/2D 用）；False = 单声道，交给 OpenAL 定位
        self.stereo: bool = False
        self.pan_width: float = 0.4
        # === 谐波 / 频谱 ===
        self.max_partials: int = 24
        #: 只保留 0.5·sr·nyquist_ratio 以下的部分音（留一点余量防混叠）
        self.nyquist_ratio: float = 0.92
        #: 相对最强谐波低于这个 dB 的谐波直接不算（省算力）
        self.partial_floor_db: float = -66.0
        #: 击弦点（弦长比例，0~0.5）：|sin(π·n·p)| 的梳状零点
        self.strike_position: float = 0.115
        #: 0 = 理想零点（该谐波完全消失），1 = 完全填平（真实琴有弦宽/刚度，不该归零）
        self.notch_floor: float = 0.45
        #: 谐波幅度整体包络 1/n^γ
        self.spectral_rolloff: float = 1.15
        #: 力度最轻 / 最重时"锤子等效低通"的截止频率（Hz）
        self.brightness_cutoff_min: float = 1200.0
        self.brightness_cutoff_max: float = 9500.0
        #: 力度 → 截止频率的曲线指数（1 = 线性）
        self.brightness_velocity_power: float = 0.9
        #: 低通阶数：1 = 6dB/oct，2 = 12dB/oct
        self.brightness_filter_order: float = 2.0
        # === 不谐和（弦刚度）===
        self.inharmonicity_base: float = 1.2e-4
        self.inharmonicity_reference: float = 261.63
        #: B ∝ (f0/f_ref)^power（高音区弦短而粗 → 更不谐和）
        self.inharmonicity_power: float = 0.9
        self.inharmonicity_min: float = 1e-5
        self.inharmonicity_max: float = 4e-3
        # === 衰减（响度曲线）===
        #: 基频 T60（秒，掉 60dB 的时间），参考频率处
        self.decay_t60_reference: float = 8.0
        self.decay_reference_frequency: float = 440.0
        #: T60_1 ∝ (f_ref/f0)^power（高音区衰减快得多）
        self.decay_pitch_power: float = 0.75
        self.decay_t60_min: float = 0.35
        self.decay_t60_max: float = 45.0
        #: 换能（弦 → 音板 / 空气）损失 ∝ 频率：1/T60 每 Hz 的增加量
        self.decay_damping_per_hz: float = 2.6e-4
        #: 弦内摩擦 / 桥损失 ∝ 频率²（默认 0；要更"闷"的琴可以调大）
        self.decay_damping_per_hz2: float = 0.0
        #: 双段衰减：快段权重（0 = 单指数）与快段 T60 相对慢段的比例
        self.double_decay_weight: float = 0.35
        self.double_decay_ratio: float = 0.12
        # === 起始（锤击）/ 抬键（制音器）===
        self.attack_ms: float = 1.8
        self.attack_reference_frequency: float = 440.0
        self.attack_pitch_power: float = 0.4
        self.attack_ms_min: float = 0.4
        self.attack_ms_max: float = 6.0
        self.release_t60: float = 0.18
        self.release_reference_frequency: float = 440.0
        self.release_pitch_power: float = 0.3
        self.release_t60_min: float = 0.05
        self.release_t60_max: float = 0.9
        #: 抬键尾巴长度 = release_tail_scale × τ（4τ ≈ -69dB）
        self.release_tail_scale: float = 4.0
        # === 齐奏（一音多弦）===
        #: 弦数分界：pitch < [0] → 1 弦；< [1] → 2 弦；其余 3 弦
        self.unison_pitch_split: tuple = (32, 48)
        #: 同一音多根弦之间的失谐（音分，±cent）→ 拍音
        self.unison_detune_cents: float = 1.2
        #: 各弦衰减时间的相对差
        self.unison_decay_spread: float = 0.06
        #: 各弦增益的相对差（真实琴的弦不是等响的）
        self.unison_gain_spread: float = 0.06
        # === 力度 / 响度 ===
        self.velocity_max: int = 127
        #: 最轻力度比最强力度低多少 dB
        self.loudness_min_db: float = -36.0
        self.loudness_max_db: float = 0.0
        #: 力度 → 响度曲线指数（>1 = 轻触更轻，接近真实钢琴手感）
        self.loudness_curve: float = 1.2
        #: 音域均衡：以这个音为中心（默认 A4），每高一个八度整体低 X dB
        self.register_center_pitch: int = 69
        self.register_tilt_db_per_octave: float = -1.2
        self.register_tilt_limit_db: float = 6.0
        # === 击弦噪声 / 键噪 ===
        self.hammer_noise_level: float = 0.22
        self.hammer_noise_velocity_power: float = 1.1
        self.hammer_noise_decay_ms: float = 7.0
        self.hammer_noise_center_hz: float = 1200.0
        #: 噪声带宽中心 ∝ (f0/261.63)^power（低音闷响、高音咔哒）
        self.hammer_noise_center_power: float = 0.7
        self.hammer_noise_center_min: float = 650.0
        self.hammer_noise_center_max: float = 9000.0
        #: 噪声带宽（以倍频程为单位的 σ）
        self.hammer_noise_octaves: float = 1.6
        self.key_noise_level: float = 0.05
        self.key_noise_center_hz: float = 800.0
        self.key_noise_octaves: float = 2.4
        self.key_noise_decay_ms: float = 25.0
        # === 音板（共鸣 / EQ）===
        #: (中心频率 Hz, Q, 线性增益) 列表
        self.soundboard_peaks: tuple = (
            (110.0, 1.4, 1.50), (220.0, 1.6, 1.35),
            (440.0, 1.8, 1.18), (880.0, 1.3, 1.06),
        )
        self.soundboard_lowpass_hz: float = 12000.0
        self.soundboard_lowpass_order: float = 1.0
        # === 渲染 ===
        #: 自然衰减到相对峰值这个 dB 就算"没声了"（踏板音 / 长按音的时长）
        self.silence_db: float = -55.0
        #: 单音最长渲染时长（秒）
        self.max_duration: float = 12.0
        #: 结尾淡出（毫秒）：任何情况都不会以非零值结束（防咔哒）
        self.tail_fade_ms: float = 25.0
        #: 逐块渲染的块长（采样点）
        self.block_size: int = 8192
        #: 包络（指数衰减）走粗网格 + 线性插值的步长（采样点）
        self.envelope_stride: int = 32
        #: 击弦噪声的随机种子（固定 → 每次渲染完全一致）
        self.seed: int = 12345
        self.apply(overrides)

    # === 构造 / 复制 / 校验 ===

    def apply(self, overrides: dict = None) -> "PianoPreset":
        """批量写入参数（**校验失败会回滚**，不会留下半新半旧的参数表）。"""
        pending = {}
        for key, value in (overrides or {}).items():
            if key not in PARAMETERS:
                raise TypeError("未知的钢琴参数 %r（可用: %s）"
                                % (key, ", ".join(PARAMETERS)))
            pending[key] = value
        if not pending:
            return self.validate()
        backup = {key: getattr(self, key) for key in pending}
        for key, value in pending.items():
            setattr(self, key, value)
        try:
            return self.validate()
        except BaseException:
            for key, value in backup.items():
                setattr(self, key, value)
            raise

    def copy(self, **overrides) -> "PianoPreset":
        """派生一份新参数表（改它不会影响原表）。"""
        clone = PianoPreset()
        clone.__dict__.update(self.__dict__)
        clone.apply(overrides)
        return clone

    def validate(self) -> "PianoPreset":
        """检查所有参数是否在物理上说得通（不合理就抛 ``ValueError``）。"""

        def number(name):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(float(value)):
                raise ValueError("%s 必须是有限数值（收到 %r）" % (name, value))
            return float(value)

        def positive(*names) -> None:
            for name in names:
                if number(name) <= 0.0:
                    raise ValueError("%s 必须大于 0（收到 %r）" % (name, getattr(self, name)))

        def nonNegative(*names) -> None:
            for name in names:
                if number(name) < 0.0:
                    raise ValueError("%s 不能为负数（收到 %r）" % (name, getattr(self, name)))

        def between(name, lo, hi) -> None:
            value = number(name)
            if not (lo <= value <= hi):
                raise ValueError("%s 必须落在 [%s, %s]（收到 %r）"
                                 % (name, lo, hi, getattr(self, name)))

        if not isinstance(self.sample_rate, int) or not 8000 <= self.sample_rate <= 192000:
            raise ValueError("sample_rate 必须是 8000~192000 的整数（收到 %r）"
                             % (self.sample_rate,))
        if not isinstance(self.stereo, bool):
            raise ValueError("stereo 必须是 bool（收到 %r）" % (self.stereo,))
        between("pan_width", 0.0, 1.0)
        if not isinstance(self.max_partials, int) or self.max_partials < 1:
            raise ValueError("max_partials 必须是 >=1 的整数（收到 %r）" % (self.max_partials,))
        between("nyquist_ratio", 0.01, 1.0)
        between("partial_floor_db", -120.0, -1.0)
        between("strike_position", 0.001, 0.5)
        between("notch_floor", 0.0, 1.0)
        positive("spectral_rolloff", "brightness_cutoff_min", "brightness_cutoff_max",
                 "brightness_velocity_power", "brightness_filter_order")
        if self.brightness_cutoff_max < self.brightness_cutoff_min:
            raise ValueError("brightness_cutoff_max 不能小于 brightness_cutoff_min")
        positive("inharmonicity_base", "inharmonicity_reference", "inharmonicity_min",
                 "inharmonicity_max")
        between("inharmonicity_power", 0.0, 4.0)
        if not (self.inharmonicity_min <= self.inharmonicity_base <= self.inharmonicity_max):
            raise ValueError("inharmonicity_base 必须落在 [inharmonicity_min, max] 之间")
        positive("decay_t60_reference", "decay_reference_frequency",
                 "decay_t60_min", "decay_t60_max", "decay_damping_per_hz")
        nonNegative("decay_damping_per_hz2", "decay_pitch_power")
        if not (self.decay_t60_min <= self.decay_t60_reference <= self.decay_t60_max):
            raise ValueError("decay_t60_reference 必须落在 [decay_t60_min, decay_t60_max] 之间")
        between("double_decay_weight", 0.0, 0.98)
        between("double_decay_ratio", 0.001, 1.0)
        positive("attack_ms", "attack_reference_frequency", "attack_ms_min", "attack_ms_max",
                 "release_t60", "release_reference_frequency",
                 "release_t60_min", "release_t60_max", "release_tail_scale")
        nonNegative("attack_pitch_power", "release_pitch_power")
        if not (self.attack_ms_min <= self.attack_ms <= self.attack_ms_max):
            raise ValueError("attack_ms 必须落在 [attack_ms_min, attack_ms_max] 之间")
        if not (self.release_t60_min <= self.release_t60 <= self.release_t60_max):
            raise ValueError("release_t60 必须落在 [release_t60_min, release_t60_max] 之间")
        split = tuple(self.unison_pitch_split)
        if len(split) != 2 or not (0 <= split[0] < split[1] <= 127):
            raise ValueError("unison_pitch_split 必须是两个递增的 MIDI 音高（收到 %r）"
                             % (self.unison_pitch_split,))
        nonNegative("unison_detune_cents")
        between("unison_decay_spread", 0.0, 0.95)
        between("unison_gain_spread", 0.0, 0.49)
        if not isinstance(self.velocity_max, int) or not 1 <= self.velocity_max <= 127:
            raise ValueError("velocity_max 必须是 1~127 的整数（收到 %r）" % (self.velocity_max,))
        if not (self.loudness_min_db <= self.loudness_max_db <= 0.0):
            raise ValueError("响度范围必须满足 loudness_min_db <= loudness_max_db <= 0")
        positive("loudness_curve")
        between("register_center_pitch", 0.0, 127.0)
        nonNegative("register_tilt_limit_db")
        number("register_tilt_db_per_octave")
        nonNegative("hammer_noise_level", "hammer_noise_velocity_power", "key_noise_level")
        positive("hammer_noise_decay_ms", "hammer_noise_center_hz",
                 "hammer_noise_center_min", "hammer_noise_center_max",
                 "hammer_noise_octaves", "key_noise_center_hz",
                 "key_noise_octaves", "key_noise_decay_ms")
        if not (self.hammer_noise_center_min <= self.hammer_noise_center_hz
                <= self.hammer_noise_center_max):
            raise ValueError("hammer_noise_center_hz 必须落在 [center_min, center_max] 之间")
        peaks = tuple(self.soundboard_peaks or ())
        for peak in peaks:
            if len(peak) != 3:
                raise ValueError("soundboard_peaks 每项必须是 (Hz, Q, 增益)（收到 %r）" % (peak,))
            for value in peak:
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not math.isfinite(float(value)) or float(value) <= 0.0:
                    raise ValueError("soundboard_peaks 的 Hz / Q / 增益都必须是正数（收到 %r）"
                                     % (peak,))
        positive("soundboard_lowpass_hz", "soundboard_lowpass_order")
        between("silence_db", -140.0, -6.0)
        positive("max_duration")
        nonNegative("tail_fade_ms")
        if not isinstance(self.block_size, int) or self.block_size < 64:
            raise ValueError("block_size 必须是 >=64 的整数（收到 %r）" % (self.block_size,))
        if not isinstance(self.envelope_stride, int) or self.envelope_stride < 1:
            raise ValueError("envelope_stride 必须是 >=1 的整数（收到 %r）"
                             % (self.envelope_stride,))
        if not isinstance(self.seed, int):
            raise ValueError("seed 必须是整数（收到 %r）" % (self.seed,))
        self.unison_pitch_split = split
        self.soundboard_peaks = peaks
        return self

    def __repr__(self):
        return ("PianoPreset(sample_rate=%d, max_partials=%d, strike_position=%g, "
                "inharmonicity_base=%g, decay_t60_reference=%g, "
                "unison_detune_cents=%g, loudness_min_db=%g)"
                % (self.sample_rate, self.max_partials, self.strike_position,
                   self.inharmonicity_base, self.decay_t60_reference,
                   self.unison_detune_cents, self.loudness_min_db))

    # === 命名预设（每次调用都是新的一份，改它不影响别人）===

    @classmethod
    def grand(cls) -> "PianoPreset":
        """三角琴（默认值）：长衰减、不谐和最小、音板低沉。"""
        return cls()

    @classmethod
    def upright(cls) -> "PianoPreset":
        """立式琴：弦短 → 衰减快、不谐和更大、琴体小 → 音板共振更高更窄。"""
        return cls(
            decay_t60_reference=6.0, decay_damping_per_hz=3.4e-4,
            inharmonicity_base=2.2e-4,
            strike_position=0.13, brightness_cutoff_max=8000.0,
            hammer_noise_level=0.28, unison_detune_cents=2.0,
            release_t60=0.16, max_duration=10.0,
            soundboard_peaks=((120.0, 1.2, 1.45), (260.0, 1.4, 1.30),
                              (560.0, 1.6, 1.15), (1100.0, 1.2, 1.05)),
        )

    @classmethod
    def honkyTonk(cls) -> "PianoPreset":
        """酒馆琴：齐奏弦明显失谐（拍音很响），衰减短一些。"""
        return cls(
            unison_detune_cents=6.0, unison_gain_spread=0.12,
            unison_decay_spread=0.10, strike_position=0.13,
            decay_t60_reference=5.5, max_duration=10.0,
            soundboard_peaks=((140.0, 1.0, 1.50), (300.0, 1.2, 1.35),
                              (700.0, 1.4, 1.20), (1400.0, 1.1, 1.10)),
        )

