"""把钢琴音接到引擎的播放层：**渲染一次、缓存起来、按键就播**。

.. code-block:: python

    from pyglgame.openal.synth import PianoPreset, Synthesizer

    synth = Synthesizer(PianoPreset.grand())
    synth.play(60, velocity=96, duration=0.8)      # 中音 C 按 0.8 秒（自动带抬键制音）
    synth.play(60, 96, pedal=True)                 # 踩住踏板：不抬键，自然衰完
    synth.play(36, 110, duration=2.0)              # 低音：自带更长的制音尾巴

    # 交互式演奏（键一直按着，松手才制音；淡出走 update 逐帧收敛，不会有咔哒声）
    src = synth.noteOn(64, 100)
    synth.noteOff(src)
    synth.update(dt)                               # 每帧调一次

    # 离线：不需要音频设备也能渲染
    pcm = synth.renderPcm(60, 96, duration=1.0)     # → int16 numpy（单声道/立体声交错）
    synth.exportWav("c4.wav", 60, 96, duration=1.0)

    # 整曲：一次混好（起音落在采样点上）→ 一次播出去 / 落盘
    song = synth.renderSong([(0.00, 60, 96, 0.50),    # (时刻秒, 音高, 力度, 时长秒)
                             (0.25, 64, 96, 0.50)])
    synth.playPcm(song)                              # 整首曲子只有一次 alSourcePlay
    synth.writePcmWav("song.wav", song)

为什么要有这一层
----------------

- **缓存**：渲染一个音要 10~50ms（Python + numpy 的加法合成），一个音只该算一次。
  缓存键是（音高，力度，时长，是否踏板）**量化后**的组合：
  力度按 ``velocity_quantum`` 分档、时长按 ``duration_quantum`` 分档 ——
  相邻档的响度差 < 0.2dB、时长差 < 25ms，听不出区别，但命中率会高很多；
- **抬键**：静态缓冲没法"从当前幅度接着衰减"，所以
  :meth:`noteOn` / :meth:`noteOff` / :meth:`update` 用逐帧增益淡出来模拟制音器
  （每帧两三个 ``alSourcef``，开销可以忽略）；
- **资源生命周期**：OpenAL 的 buffer 不能在还挂在 source 上时被删掉，
  所以被淘汰/播完的 :class:`~pyglgame.openal.AudioResource.AudioResource`
  会先进"待回收"队列，等这个音**肯定播完了**再释放（默认多留 2 秒）；
- **预热**：交互场景用 :meth:`Synthesizer.prerender`（音高 × 力度 × 时长的笛卡尔积），
  已知曲谱（MIDI 可视化 / 出片）用 :meth:`Synthesizer.prerenderNotes` —— 只算
  曲子里真正用到的那些音，去重后一次烘进缓存，之后每个音都是纯拷贝；
- **整曲**：:meth:`Synthesizer.renderSong` 把一整首曲子离线混成**一条 PCM** ——
  每个音的起音都落在**采样点**上（误差 < 1/采样率）。实时逐音触发给不了这个精度：
  "什么时候出声"取决于"哪一帧轮到了它"（60fps 就是 16.7ms 一格），同一帧里还要
  现建 buffer（和弦会被拉散）。混好的 PCM 用 :meth:`playPcm` 播（一首曲子只有
  一次 ``alSourcePlay``）、用 :meth:`writePcmWav` / :meth:`exportSongWav` 落盘。

没有音频设备（DLL 缺失 / 无设备）时全部方法静默降级：:meth:`play` 返回 0，
:meth:`renderPcm` 照样能用（做波形分析、导出 wav 都不需要设备）。
"""

import math
import time
import wave

import numpy as np

from ..AudioResource import AudioResource, AudioUnavailableError
from ..Device import Device
from ..Player import Player
from .Envelope import t60ToTau
from .Filters import interleaveStereo, panGain, scalePcmInt16, toInt16
from .PianoHarmonics import PianoHarmonics
from .PianoPreset import PianoPreset
from .PianoVoice import PianoVoice

#: 淡出到这个增益（-40dB）就直接把 source 停掉
FADE_FLOOR = 0.01

#: 播完一个音之后再多留多久才释放它的 buffer（秒）
RETIRE_MARGIN = 2.0


class _Entry:
    """缓存条目：单声道 int16 PCM + 它的元信息（时长、声像等）。

    注意：``voice.samples``（float 副本，体积是 int16 的两倍）会被**立刻释放** ——
    128 条缓存留着它要白吃上百 MB。要用波形本身请走
    :meth:`Synthesizer.renderVoice`。
    """

    __slots__ = ("pcm", "voice")

    def __init__(self, pcm, voice) -> None:
        self.pcm = pcm
        self.voice = voice


class Synthesizer:
    """钢琴合成器（引擎侧入口）。

    :param preset: 音色参数表（默认 :class:`PianoPreset`，即三角琴）
    :param player: 播放器；不给就用 ``Player.app_instance``（``App()`` 起来之后就有）
    :param device: 音频设备；不给就用 ``Device.app_instance``
    :param max_cache: PCM 缓存上限（超过就淘汰最旧的）
    :param velocity_quantum: 力度量化步长（缓存命中率 / 精度折中）
    :param duration_quantum: 时长量化步长（秒）
    """

    #: ``App.__first_init__`` 会把它设成当前实例（和 ``Player.app_instance`` 一个套路）
    app_instance: "Synthesizer" = None

    def __init__(self, preset: PianoPreset = None, player=None, device=None,
                 max_cache: int = 128, velocity_quantum: int = 4,
                 duration_quantum: float = 0.05) -> None:
        self.preset = preset if preset is not None else PianoPreset()
        self.harmonics = PianoHarmonics(self.preset)
        self._player = player
        self._device = device
        self.max_cache = max(1, int(max_cache))
        self.velocity_quantum = max(1, int(velocity_quantum))
        self.duration_quantum = max(1e-3, float(duration_quantum))
        self._cache: dict = {}
        self._in_flight: list = []        # [(到期时间, AudioResource)] 播完就释放
        self._voices: dict = {}           # src → 交互式演奏的语音状态
        self._pedal = False
        #: 造缓冲的工厂：``factory(data, sample_rate, channels, device)``；
        #: 单测 / 自定义音频后端可以替换它（默认就是 AudioResource）
        self.resource_factory = AudioResource.fromNpData
        self._stats = {"renders": 0, "cache_hits": 0, "evictions": 0,
                       "play_failed": 0,
                       "last_render_ms": 0.0, "max_render_ms": 0.0,
                       "cache_bytes": 0}       # 缓存里 PCM 的字节数（估算内存占用）

    # === 依赖（延迟取全局实例，方便单测注入假实现）===

    @property
    def player(self):
        if self._player is not None:
            return self._player
        if Player.app_instance is None:
            raise RuntimeError(
                "Player.app_instance 还没初始化：请先 App()（或给 Synthesizer(player=...)）")
        return Player.app_instance

    @property
    def device(self):
        if self._device is not None:
            return self._device
        return Device.app_instance

    @property
    def stats(self) -> dict:
        """统计：``renders`` / ``cache_hits`` / ``evictions`` / ``cache_bytes`` /
        ``last_render_ms`` 等。"""
        info = dict(self._stats)
        info["cache_size"] = len(self._cache)
        info["voices"] = len(self._voices)
        info["in_flight"] = len(self._in_flight)
        return info

    # === 参数表 ===

    def setPreset(self, preset: PianoPreset) -> "Synthesizer":
        """换音色（参数表变了 → 缓存全部作废）。"""
        self.preset = preset if preset is not None else PianoPreset()
        self.harmonics = PianoHarmonics(self.preset)
        self.clearCache()
        return self

    def setPedal(self, down: bool) -> "Synthesizer":
        """持音踏板：踩下 → 已经松开的音不再制音；抬起 → 这些音立刻开始制音。"""
        down = bool(down)
        if down == self._pedal:
            return self
        self._pedal = down
        for src, voice in list(self._voices.items()):
            if voice["held"]:
                continue
            if down:
                voice["fading"] = False
            else:
                self._startFade(voice)
        return self

    @property
    def pedal(self) -> bool:
        return self._pedal

    # === 渲染 / 缓存 ===

    def cacheKey(self, pitch, velocity, duration, pedal) -> tuple:
        """缓存键：力度 / 时长会先量化。相邻档响度差 < 0.2dB、时长差 ≤ 半档，
        听不出来，但命中率高很多。"""
        quantized_velocity = int(round(float(velocity) / self.velocity_quantum)
                                 * self.velocity_quantum)
        quantized_velocity = min(max(quantized_velocity, 1), int(self.preset.velocity_max))
        quantized_duration = None
        if duration is not None:
            quantized_duration = round(float(duration) / self.duration_quantum) \
                * self.duration_quantum
            quantized_duration = min(max(quantized_duration, self.duration_quantum),
                                     float(self.preset.max_duration))
        return (int(pitch), quantized_velocity,
                None if quantized_duration is None else round(quantized_duration, 4),
                bool(pedal))

    def renderVoice(self, pitch, velocity=100, duration=None, pedal=False) -> PianoVoice:
        """直接渲染一个 :class:`PianoVoice`（**不走缓存**；调参 / 可视化用这个）。"""
        return PianoVoice(pitch, velocity, duration, pedal, preset=self.preset)

    def _entry(self, pitch, velocity, duration, pedal) -> _Entry:
        key = self.cacheKey(pitch, velocity, duration, pedal)
        entry = self._cache.get(key)
        if entry is not None:
            self._stats["cache_hits"] += 1
            return entry
        voice = PianoVoice(key[0], key[1], key[2], key[3], preset=self.preset)
        entry = _Entry(toInt16(voice.samples), voice)
        # 缓存只留 int16：float 副本体积是它的两倍，留着纯属浪费内存
        voice.samples = np.zeros(0, dtype=np.float64)
        self._stats["renders"] += 1
        self._stats["last_render_ms"] = voice.render_ms
        self._stats["max_render_ms"] = max(self._stats["max_render_ms"], voice.render_ms)
        self._cache[key] = entry
        self._stats["cache_bytes"] += int(entry.pcm.nbytes)
        self._evict()
        return entry

    def _evict(self) -> None:
        while len(self._cache) > self.max_cache:
            key = next(iter(self._cache))
            self._stats["cache_bytes"] -= int(self._cache[key].pcm.nbytes)
            del self._cache[key]
            self._stats["evictions"] += 1

    def renderPcm(self, pitch, velocity=100, duration=None, pedal=False,
                  pan=None, use_cache: bool = True) -> np.ndarray:
        """渲染成 ``int16`` PCM（默认单声道；``preset.stereo`` 为真时左右交错）。

        不需要音频设备；``pan`` 只在立体声模式下有意义。
        """
        if use_cache:
            entry = self._entry(pitch, velocity, duration, pedal)
            pcm, voice_pan = entry.pcm, entry.voice.pan
        else:
            voice = self.renderVoice(pitch, velocity, duration, pedal)
            pcm, voice_pan = toInt16(voice.samples), voice.pan
        if not self.preset.stereo:
            return pcm
        left_gain, right_gain = panGain(voice_pan if pan is None else float(pan))
        return interleaveStereo(scalePcmInt16(pcm, left_gain),
                                scalePcmInt16(pcm, right_gain))

    def panForPitch(self, pitch) -> float:
        """这个音高在立体声里应该摆多偏（低音偏左、高音偏右）。"""
        return self.harmonics.pan(pitch)

    def toAudioResource(self, pitch, velocity=100, duration=None, pedal=False,
                        pan=None) -> AudioResource:
        """渲染成 :class:`~pyglgame.openal.AudioResource.AudioResource`（需要音频设备）。"""
        if self.device is None:
            raise AudioUnavailableError(
                "没有可用音频设备（App().audio_available 为假）；"
                "离线用途请改用 renderPcm() / exportWav()")
        return self._makeResource(self._entry(pitch, velocity, duration, pedal), pan)

    # === 播放 ===

    def play(self, pitch, velocity=100, duration=None, pedal=False,
             loop: bool = False, pan=None) -> int:
        """播一个音，返回 OpenAL 的 source（拿不到就返回 0，静默降级）。

        ``duration`` / ``pedal`` 的语义和 :class:`PianoVoice` 一致：
        给时长 = 到点抬键（带制音尾巴）、``pedal=True`` = 不抬键（自然衰完）。
        """
        self._prune()
        entry = self._entry(pitch, velocity, duration, pedal)
        try:
            resource = self._makeResource(entry, pan)
            src = self.player.play(resource, loop)
        except Exception:
            # 设备中途消失 / 初始化失败：静默降级（记一笔，不把整个游戏搞崩）
            self._stats["play_failed"] += 1
            return 0
        if src:
            self._in_flight.append(
                (time.monotonic() + entry.voice.seconds + RETIRE_MARGIN, resource))
            self._prune()
        return src

    def _makeResource(self, entry: _Entry, pan=None):
        device = self.device
        if device is None:
            raise AudioUnavailableError("没有可用音频设备")
        if self.preset.stereo:
            left_gain, right_gain = panGain(entry.voice.pan if pan is None else float(pan))
            data = interleaveStereo(scalePcmInt16(entry.pcm, left_gain),
                                    scalePcmInt16(entry.pcm, right_gain))
            channels = 2
        else:
            data, channels = entry.pcm, 1
        return self.resource_factory(np.ascontiguousarray(data),
                                     sample_rate=self.preset.sample_rate,
                                     channels=channels, device=device)

    def pcmToResource(self, pcm, sample_rate=None, channels=None) -> AudioResource:
        """现成的 PCM → :class:`~pyglgame.openal.AudioResource.AudioResource`（需要设备）。

        与 :meth:`_makeResource` 的区别：不做单声道 + 声像摊平 —— 你给什么就传什么
        （:meth:`renderSong` 的结果已经是最终布局）。
        """
        device = self.device
        if device is None:
            raise AudioUnavailableError("没有可用音频设备")
        rate = int(self.preset.sample_rate if sample_rate is None else sample_rate)
        if channels is None:
            channels = 2 if self.preset.stereo else 1
        return self.resource_factory(np.ascontiguousarray(pcm, dtype=np.int16),
                                     sample_rate=rate, channels=int(channels),
                                     device=device)

    def playPcm(self, pcm, sample_rate=None, channels=None, loop: bool = False,
                seconds: float = None) -> int:
        """播一段**已经混好的** PCM（整曲 / 整段伴奏），返回 source（拿不到返回 0）。

        相对 :meth:`play` 的三点区别，都是为整曲准备的：

        - 整首曲子只有**一次** ``alSourcePlay``：起播延迟与设备混音周期只影响
          这一次，不会变成"每个音各自抖一下"（逐音播放时每个音都要过一次）；
        - 不占 64 个 source：逐音播放时和弦 + 长踏板会逼近上限，超了就抢占最老的
          音（:class:`Player` 的行为），那正是"和弦缺音 / 忽然后面的音变响"的来源；
        - 缓冲留在"待回收"队列里直到 ``seconds`` 到点（不给就按 PCM 长度算），
          所以播整曲时不会因为缓存淘汰或 ``_prune`` 而提前释放。
        """
        self._prune()
        rate = int(self.preset.sample_rate if sample_rate is None else sample_rate)
        if channels is None:
            channels = 2 if self.preset.stereo else 1
        channels = max(1, int(channels))
        if seconds is None:
            seconds = float(np.asarray(pcm).size) / (rate * channels)
        try:
            resource = self.pcmToResource(pcm, rate, channels)
            src = self.player.play(resource, loop)
        except Exception:
            # 和 play() 一样：设备中途消失就静默降级（记一笔，不把游戏搞崩）
            self._stats["play_failed"] += 1
            return 0
        if src:
            self._in_flight.append(
                (time.monotonic() + max(0.0, float(seconds)) + RETIRE_MARGIN, resource))
            self._prune()
        return src

    def noteOn(self, pitch, velocity=100, pedal: bool = None, pan=None) -> int:
        """按下琴键（长按）：缓冲不含制音尾巴，靠 :meth:`noteOff` / :meth:`update` 收尾。"""
        if pedal is None:
            pedal = self._pedal
        src = self.play(pitch, velocity, duration=None, pedal=True, pan=pan)
        if src:
            self._voices[src] = {"pitch": int(pitch), "gain": 1.0, "fading": False,
                                 "held": True, "pedal": bool(pedal),
                                 "tau": t60ToTau(self.harmonics.releaseT60(pitch)) * 2.0}
        return src

    def noteOff(self, src: int) -> None:
        """松开琴键：踩住踏板时让它继续响，否则开始制音淡出。"""
        voice = self._voices.get(src)
        if voice is None:
            return
        voice["held"] = False
        if voice["pedal"] or self._pedal:
            return
        self._startFade(voice)

    @staticmethod
    def _startFade(voice: dict) -> None:
        """开始制音淡出：τ 取制音器 T60 的 2 倍 —— 逐帧改增益本来就比连续的
        指数包络"台阶大"，τ 放大一点反而更顺（代价是松手后多响一点点）。"""
        voice["fading"] = True
        voice["tau"] = max(float(voice.get("tau") or 0.05), 1e-3)

    def stop(self, src: int) -> None:
        """立刻停掉某个音（不淡出）。"""
        if src <= 0:
            return
        self._voices.pop(src, None)
        self.player.stop(src)

    def stopAll(self) -> None:
        """停掉所有正在播的音。"""
        self._voices.clear()
        self.player.stopAll()

    def update(self, dt: float) -> None:
        """每帧调一次：处理"松开琴键"的制音淡出 + 回收播完的 buffer。"""
        if self._voices:
            for src, voice in list(self._voices.items()):
                if not voice["fading"]:
                    continue
                voice["gain"] *= math.exp(-float(dt) / voice["tau"])
                if voice["gain"] <= FADE_FLOOR:
                    self._voices.pop(src, None)
                    self.player.stop(src)
                    continue
                self.player.setVolume(src, voice["gain"])
        self._prune()

    # === 资源回收 / 清理 ===

    def _prune(self, now: float = None) -> None:
        """释放"肯定已经播完"的 buffer（还在播的绝不能删，否则声音会断）。"""
        now = time.monotonic() if now is None else now
        if not self._in_flight:
            return
        alive = []
        for expire, resource in self._in_flight:
            if now >= expire:
                try:
                    resource.cleanup()
                except Exception:
                    pass
            else:
                alive.append((expire, resource))
        self._in_flight = alive

    def clearCache(self) -> "Synthesizer":
        """清空 PCM 缓存（不影响正在播的音）。"""
        self._cache.clear()
        self._stats["cache_bytes"] = 0
        return self

    def release(self) -> None:
        """停播 + 释放缓存与所有 buffer（退出时调一次）。"""
        self.stopAll()
        self.clearCache()
        for _expire, resource in self._in_flight:
            try:
                resource.cleanup()
            except Exception:
                pass
        self._in_flight = []

    # === 批量预渲染 / 导出 ===

    def prerender(self, pitches, velocities=(48, 96, 127), durations=None,
                  pedal: bool = False) -> int:
        """批量预渲染：进游戏前先热一遍缓存，避免第一次弹到某个音时卡一下。

        ``pitches × velocities × durations`` 是**笛卡尔积**（适合"整键盘随便弹"的
        交互场景）；已知曲子里具体要弹哪些音（例如 MIDI 可视化）请用
        :meth:`prerenderNotes`，能少算好几个数量级。

        返回**实际渲染**（而不是命中缓存）的数量。
        """
        if durations is None:
            durations = (0.15, 0.4, 0.8, 1.6)
        before = self._stats["renders"]
        for pitch in pitches:
            for velocity in velocities:
                for duration in durations:
                    self._entry(pitch, velocity, duration, pedal)
        return self._stats["renders"] - before

    def prerenderNotes(self, notes, pedal: bool = False, grow_cache: bool = True,
                       progress=None) -> int:
        """按"曲子里真正要用到的音"预热缓存：``notes`` 是 ``(音高, 力度, 时长秒)``
        的可迭代对象（时长允许为 ``None`` = 不抬键）。

        和 :meth:`prerender` 的区别：那个取的是笛卡尔积，这个只算你给的这些音。
        一首 MIDI 里几百个音往往只有几十~上百个不同的（音高, 力度, 时长）组合，
        按 :meth:`cacheKey` 去重后（相邻档本来就命中同一条）**只渲染一次**；
        先给的先渲染，所以照播放顺序传进来最划算（万一中途被打断，最早要弹的
        音已经热好了）。

        ``grow_cache=True``：预热条目比 ``max_cache`` 多时先把容量撑到够用 ——
        否则 FIFO 淘汰会把刚烘好的音又丢掉，等于白算一遍（内存换取零卡顿）。

        ``progress(done, total)`` 可选：每渲染一个音回调一次（``total`` 是去重后的
        数量；命中已有缓存的条目几乎不花时间）——长曲预热时用它打进度。

        返回**实际渲染**（而不是命中缓存）的数量。
        """
        unique = []
        seen = set()
        for note in notes:
            pitch, velocity, duration = note
            key = self.cacheKey(pitch, velocity, duration, pedal)
            if key in seen:
                continue
            seen.add(key)
            unique.append((key[0], key[1], key[2]))    # 用缓存键里的量化值去渲染
        if grow_cache:
            self.max_cache = max(self.max_cache, len(unique))
        before = self._stats["renders"]
        total = len(unique)
        for done, (pitch, velocity, duration) in enumerate(unique, 1):
            self._entry(pitch, velocity, duration, pedal)
            if progress is not None:
                progress(done, total)
        return self._stats["renders"] - before

    # === 整曲混音（离线对齐） ===

    def renderSong(self, notes, lead: float = 0.0, tail: float = 0.5,
                   pedal: bool = False, sample_rate=None,
                   progress=None) -> np.ndarray:
        """把一整首曲子**离线混成一条 PCM**：每个音的起音都落在**采样点**上。

        实时逐音播放给不了这个精度 —— "什么时候出声"取决于哪一帧轮到了这个音
        （60fps 就是 16.7ms 一格；理想时刻永远落在两帧之间，于是整首曲子平均偏晚
        半帧），同一帧里还要现建 buffer（``PCM → int16 → 交错 → alBufferData``），
        和弦会被这段串行开销拉散。整曲混音把"起音时刻"从帧网格搬到采样网格
        （1/采样率 秒）上：误差 < 1 个采样，和弦严丝合缝，也不会整体偏晚。

        ``notes``：``(时刻秒, 音高, 力度, 时长秒)`` 的可迭代对象（时长 ``None``
        = 不抬键），``时刻``是相对**曲子零点**的秒数。顺序无所谓（每个音独立摆放），
        按播放顺序传进来 ``progress`` 的节奏更好看。音高 / 力度 / 时长的语义与
        :meth:`play` 完全一致（时长同样按 :meth:`cacheKey` 量化）——所以曲子里
        听到的就是缓存里那段波形，和逐音播放**同一个音色**。

        :param lead: 返回的 PCM 第 0 个采样对应曲子的第 ``lead`` 秒（= 从曲子的
            哪一刻开始播）。早于它的部分直接裁掉，用于"中途开始播"（比如录像从
            第 8 秒起）；默认 0 = 从头开始。
        :param tail: 最后一个音响完之后再多留几秒静音收尾。
        :param sample_rate: 必须与 ``preset.sample_rate`` 一致 —— 缓存里的 PCM 就是按
            ``preset`` 采样的，时间轴按别的采样率换算会把整曲拉长 / 压缩。要换采样率
            请先 ``setPreset(preset.copy(sample_rate=48000))`` 再混音。
        :param progress: ``progress(done, total)``，每摆好一个音回调一次（``total``
            = 音数）；缓存没命中时的渲染开销在 :meth:`_entry` 里，进度会卡一下。
        :return: ``int16`` PCM；``preset.stereo`` 为真时左右交错（``(2N,)``）。

        累加用 ``int32``：几个音在最响处叠加会超过 int16 的量程，用 int16 累加会
        绕回成噪声。最后一步按设备混音的做法**夹取**（不软限幅，避免整曲与实时
        播放听感不一致）。内存：最终 PCM = ``2 字节 × 采样率 × 声道数``（44100Hz
        单声道约 5MB/分钟、立体声约 10MB/分钟），混音期间另有一份同长度的 ``int32``
        累加缓冲 → 峰值约为最终大小的 1.5~2 倍；混完交给 :meth:`playPcm`（数据拷进
        OpenAL 的 buffer）或 :meth:`writePcmWav` 之后就能放掉。
        """
        rate = int(self.preset.sample_rate if sample_rate is None else sample_rate)
        if rate != int(self.preset.sample_rate):
            # 缓存里的 PCM 是按 preset 采样的：只换时间轴的换算比例会让每个音"变长/变短"
            raise ValueError(
                "renderSong 的 sample_rate 必须等于 preset.sample_rate"
                f"（{self.preset.sample_rate}）；要换采样率请先 "
                f"setPreset(preset.copy(sample_rate={rate})) 再混音")
        stereo = bool(self.preset.stereo)

        items = []
        end = 0.0
        for note in notes:
            at, pitch, velocity, duration = note
            entry = self._entry(pitch, velocity, duration, pedal)
            at = float(at)
            items.append((at, entry))
            end = max(end, at + entry.pcm.size / float(rate))
        total = max(0, int(round((end + max(0.0, float(tail)) - float(lead)) * rate)))

        mix = np.zeros(total, dtype=np.int32)
        mix_right = np.zeros(total, dtype=np.int32) if stereo else None
        for done, (at, entry) in enumerate(items, 1):
            offset = int(round((at - float(lead)) * rate))
            pcm = entry.pcm
            skip = 0
            if offset < 0:                     # 起点在 lead 之前：裁掉已经放过的部分
                skip, offset = -offset, 0
            take = min(pcm.size - skip, total - offset)
            if take > 0:
                if stereo:
                    left_gain, right_gain = panGain(entry.voice.pan)
                    chunk = pcm[skip:skip + take]
                    mix[offset:offset + take] += scalePcmInt16(chunk, left_gain)
                    mix_right[offset:offset + take] += scalePcmInt16(chunk, right_gain)
                else:
                    mix[offset:offset + take] += pcm[skip:skip + take]
            if progress is not None:
                progress(done, len(items))

        result = np.clip(mix, -32768, 32767).astype(np.int16)
        if not stereo:
            return result
        right = np.clip(mix_right, -32768, 32767).astype(np.int16)
        return interleaveStereo(result, right)

    def writePcmWav(self, path: str, pcm, sample_rate=None, channels=None) -> str:
        """把现成的 PCM（比如 :meth:`renderSong` 的结果）写成 16bit wav。

        ``channels`` 默认按 ``preset.stereo``；不需要音频设备，也不碰缓存。
        """
        rate = int(self.preset.sample_rate if sample_rate is None else sample_rate)
        if channels is None:
            channels = 2 if self.preset.stereo else 1
        handle = wave.open(path, "wb")
        try:
            handle.setnchannels(int(channels))
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(np.ascontiguousarray(pcm, dtype=np.int16).tobytes())
        finally:
            handle.close()
        return path

    def exportSongWav(self, path: str, notes, lead: float = 0.0, tail: float = 0.5,
                      pedal: bool = False, sample_rate=None, progress=None) -> str:
        """整曲混音（:meth:`renderSong`）→ 16bit wav（离线出片 / 对齐参考）。

        ``Game4`` 那类"画面里逐音触发、视频没有音轨"的用法：出片后用这份 wav
        并轨（``ffmpeg -i output.mp4 -i output.song.wav -c:v copy -c:a aac 成片.mp4``），
        或者拿它跟录像里听到的比一比 —— 帧网格量化误差有多大，一对就清楚。
        """
        pcm = self.renderSong(notes, lead=lead, tail=tail, pedal=pedal,
                              sample_rate=sample_rate, progress=progress)
        return self.writePcmWav(path, pcm, sample_rate=sample_rate)

    def exportWav(self, path: str, pitch, velocity=100, duration=None,
                  pedal: bool = False, pan=None) -> str:
        """把单音写成 16bit wav（离线试听 / 回归对比；不需要音频设备）。"""
        pcm = self.renderPcm(pitch, velocity, duration, pedal, pan)
        handle = wave.open(path, "wb")
        try:
            handle.setnchannels(2 if self.preset.stereo else 1)
            handle.setsampwidth(2)
            handle.setframerate(self.preset.sample_rate)
            handle.writeframes(pcm.tobytes())
        finally:
            handle.close()
        return path

    def __repr__(self):
        return ("Synthesizer(preset=%r, cache=%d/%d, in_flight=%d)"
                % (self.preset, len(self._cache), self.max_cache, len(self._in_flight)))

