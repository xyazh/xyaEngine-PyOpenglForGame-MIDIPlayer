"""钢琴音源合成器：加法（模态）综合 + 物理参数化的"音色"。

``pyglgame/openal`` 只管"把 PCM 交给 OpenAL 播出去"，音色从哪来它不管；
这个子包补上"音色"这一层 —— 给一个音高与力度，直接算出钢琴声音的 PCM。

.. code-block:: python

    from pyglgame.openal.synth import PianoPreset, PianoVoice, Synthesizer

    # 1) 只要一段波形（**不需要音频设备**：离线分析 / 出片 / 单测都行）
    voice = PianoVoice(pitch=60, velocity=96, duration=0.8)
    voice.samples                  # → 单声道浮点 PCM
    voice.loudnessCurveDb()        # → (时间, dB)：响度起始 / 衰减曲线
    voice.spectrum()               # → (频率, 幅度)：谐波峰在哪、有多高

    # 2) 要能直接播（``App()`` 起来之后 Player / Device 就有了）
    synth = Synthesizer(PianoPreset.grand())
    synth.play(60, 96, duration=0.8)     # 按键 → 自动带抬键制音
    synth.play(60, 96, pedal=True)       # 踩住踏板：不抬键，自然衰完

    # 3) 交互式演奏（松手才制音，每帧调一次 update）
    src = synth.noteOn(64, 100); synth.noteOff(src); synth.update(dt)

    # 4) 整曲离线混音（节奏要准就用这条：见下"整曲混音"）
    pcm = synth.renderSong([(0.0, 60, 96, 1.2), (0.5, 64, 96, 1.2)])
    synth.playPcm(pcm)                   # 一次 alSourcePlay 播完整条曲子
    synth.writePcmWav("song.wav", pcm)   # 或落盘，之后和视频并轨

整曲混音（为什么节奏更准）
--------------------------

逐音触发时"什么时候出声"取决于**哪一帧轮到了这个音**：60fps 的网格是 16.7ms，
理想时刻永远落在两帧之间 → 平均偏晚半帧、最坏偏晚一整帧；同一帧里的和弦还要
现建 buffer，会被串行开销拉散几十毫秒。

``Synthesizer.renderSong()`` 换成**离线混音**：每个音的起音摆在
``round(时刻 × 采样率)`` 采样上（误差 < 1/44100 秒），和弦在 int32 里相加后
夹取（不会绕回噪声）；之后整条 PCM 交给 ``playPcm()`` 一次播出去，时间轴由
**音频设备自己的时钟**走完 —— 渲染掉帧不会让音频变慢，画面只要保证"第一条渲染
帧 = 曲子零点"即可。⚠ 但反过来也必须成立：**画面得跟得上真实时间** —— 交给渲染的
``dt`` 必须是"这一帧真实花了多久"（``GameMainLoop`` 默认如此，含渲染/抓帧/限帧
sleep）。曾经 ``Game4.py`` 为了"视频时间轴 = 帧数/60"把渲染钉在固定步长上
（``app.setFixedRenderStep(1.0/fps)``），帧一慢画面就落后于音频、而且**越差越多**，
所以它的 ``PIANO_SONG_MIX`` 曾默认关、走逐音触发；现在那份固定步长已经去掉
（``app.setRealTimeUpdate(True)``，逻辑帧也是真实帧时长），前提已满足，默认关只是
因为逐音触发"由画面事件驱动出声"已经够用（详细原因见 ``docs/合成器.md`` 的 ⚠ 前提）。

模型（为什么这么算）
--------------------

一个钢琴音 = **若干谐波部分音** × **几样现实里的损耗**：

1. **谐波频率**（不谐和）：弦有刚度，第 n 个部分音不是整数倍而是
   ``f_n = n·f0·√(1 + B·n²)`` —— 高音区偏差更明显（见
   :class:`~pyglgame.openal.synth.PianoHarmonics.PianoHarmonics`）；
2. **谐波幅度（频谱 / 音色）**：击弦点梳状 ``|sin(π·n·p)|`` × 整体 ``1/n^γ``
   × "锤子等效低通"（截止频率随力度变大 → **轻按音暗、重按音亮**）；
3. **响度包络（起始 / 衰减曲线）**：起音是 smoothstep（锤子压弦几毫秒），
   衰减是**双段指数**（先快掉一段、再拖长尾），高次谐波衰减更快；
   松键时再叠一段制音器衰减（见
   :mod:`pyglgame.openal.synth.Envelope`）；
4. **击弦噪声 / 键噪**：几毫秒的带通噪声，低音是闷响、高音是咔哒，幅度 ∝ 力度；
5. **音板**：整段过一遍频域 EQ（几个低频共振峰 + 高频缓降），这是"钢琴味"的
   最后一道（见 :mod:`pyglgame.openal.synth.Filters`）。

清单
----

===========================  ====================================================
``PianoPreset``              音色参数表（三角琴 / 立式琴 / 酒馆琴 三个预设）
``PianoHarmonics``           谐波模型：频率、幅度、T60、齐奏布局、噪声参数
``PianoVoice``               单音渲染（浮点 PCM）+ 响度曲线 / 频谱分析
``Synthesizer``              引擎入口：缓存、播放、抬键淡出、预渲染、导出 wav
                             整曲离线混音（``renderSong`` / ``playPcm``）
``Envelope`` / ``Filters``   包络数学 / 频域整形等纯函数
===========================  ====================================================

演示：``Game4.py``（MIDI 钢琴可视化）在 ``PIANO_SYNTH = True`` 时用这里的合成器
出声；``PIANO_PRERENDER = True`` 会在启动时用 ``Synthesizer.prerenderNotes()``
把整首曲子用到的音一次算好（出片过程中不再"现算波形"）。
"""

from .Envelope import (attackEnvelope, dbToGain, decayEnvelope, gainToDb,
                       releaseEnvelope, t60ToTau, tauToT60, velocityToGain,
                       velocityToDb)
from .Filters import (applySpectralEQ, interleaveStereo, noiseBurst,
                      normalizePeak, panGain, pitchPan, resonanceResponse,
                      scalePcmInt16, softClip, toInt16)
from .PianoHarmonics import PianoHarmonics
from .PianoPreset import PARAMETERS, PianoPreset
from .PianoVoice import OUTPUT_PEAK, PianoVoice
from .Synthesizer import Synthesizer

__all__ = [
    "PianoPreset", "PARAMETERS",
    "PianoHarmonics", "PianoVoice", "Synthesizer", "OUTPUT_PEAK",
    "attackEnvelope", "decayEnvelope", "releaseEnvelope",
    "dbToGain", "gainToDb", "t60ToTau", "tauToT60",
    "velocityToGain", "velocityToDb",
    "applySpectralEQ", "resonanceResponse", "noiseBurst", "normalizePeak",
    "softClip", "toInt16", "scalePcmInt16", "pitchPan", "panGain",
    "interleaveStereo",
]
