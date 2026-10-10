import numpy as np
import wave
import ctypes
from .Device import Device
from ..ResourceLocation import ResourceLocation
from io import BytesIO


class AudioUnavailableError(RuntimeError):
    """音频设备不可用（OpenAL DLL 缺失/无设备）。"""


class AudioResource:
    AL_FORMAT_MONO16 = 0x1101
    AL_FORMAT_STEREO16 = 0x1103

    def __init__(self, data=None, sample_rate=44100, channels=1, device: Device = None) -> None:
        if device is None:
            device = Device.app_instance
        if device is None:
            raise AudioUnavailableError(
                "音频设备不可用：请检查 pyglgame/openal/lib 下的 OpenAL 动态库，"
                "或改用 App().audio_available 判断后再播放音频")
        self.openal = device.openal
        self.sample_rate = sample_rate
        self.channels = channels
        self.buffer = ctypes.c_uint(0)
        alGenBuffers = self.openal.alGenBuffers
        alGenBuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
        alGenBuffers.restype = None
        alGenBuffers(1, ctypes.byref(self.buffer))

        if data is not None:
            self.setData(data)

    @classmethod
    def fromWav(cls, wav:str|BytesIO|ResourceLocation, device: Device = None) -> "AudioResource":
        if isinstance(wav, ResourceLocation):
            wf = wave.open(wav.getIO(), "rb")
        else:
            wf = wave.open(wav, "rb")
        with wf:
            channels = wf.getnchannels()
            sample_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
            data = np.frombuffer(frames, dtype=np.int16)
        return cls(data, sample_rate, channels, device)

    @classmethod
    def fromNpData(cls, np_data, sample_rate=44100, channels=1, device: Device = None) -> "AudioResource":
        return cls(np_data, sample_rate, channels, device)

    @classmethod
    def genSineAudio(cls, frequency=440, duration_sec=10, volume=0.5, sample_rate=44100, channels=1, device: Device = None):
        t = np.arange(int(sample_rate * duration_sec)) / sample_rate
        wave = np.abs(np.sin(2 * np.pi * frequency * t))
        fade_len = int(sample_rate * 0.01)
        if fade_len*2 < len(wave):
            fade_in = np.linspace(0, 1, fade_len)
            fade_out = np.linspace(1, 0, fade_len)
            wave[:fade_len] *= fade_in
            wave[-fade_len:] *= fade_out
        audio_data = (wave * (2**15 - 1) * volume).astype(np.int16)
        return cls.fromNpData(audio_data, sample_rate=sample_rate, channels=channels, device=device)

    @classmethod
    def generateSmoothSine(cls, frequency=440, duration=1.0, volume=0.5, sample_rate=44100, channels=1, device: Device = None):
        t = np.linspace(0, duration, int(sample_rate*duration), endpoint=False)
        # 基波 + 高次谐波衰减
        wave = 0.6*np.sin(2*np.pi*frequency*t)        # 基波
        wave += 0.3*np.sin(2*np.pi*2*frequency*t)    # 二次谐波
        wave += 0.15*np.sin(2*np.pi*3*frequency*t)   # 三次谐波
        wave += 0.07*np.sin(2*np.pi*4*frequency*t)   # 四次谐波
        wave /= np.max(np.abs(wave))
        # 添加短暂击弦瞬态（白噪声，0.5%幅度）
        noise_len = int(sample_rate*0.005)
        noise = (np.random.rand(noise_len) - 0.5) * 0.01
        wave[:noise_len] += noise
        # ADSR 包络
        fade_in = int(sample_rate * 0.01)
        fade_out = int(sample_rate * 0.3)

        # 限制最大长度
        fade_in = min(fade_in, len(wave)//2)
        fade_out = min(fade_out, len(wave)//2)

        envelope = np.ones_like(wave)
        envelope[:fade_in] = np.linspace(0, 1, fade_in)
        envelope[-fade_out:] = np.linspace(1, 0, fade_out)
        wave *= envelope
        audio_data = (wave * (2**15 - 1) * volume).astype(np.int16)
        return cls.fromNpData(audio_data, sample_rate=sample_rate, channels=channels, device=device)

    @classmethod
    def generateNaturalSine(cls, frequency=440, duration=1.0, volume=0.5, sample_rate=44100, channels=1, device: Device = None):
        t = np.linspace(0, duration, int(sample_rate*duration), endpoint=False)
        # 基波 + 高次谐波衰减
        wave = np.sin(2*np.pi*frequency*t)        # 基波
        wave /= np.max(np.abs(wave))
        # 添加短暂击弦瞬态（白噪声，0.5%幅度）
        noise_len = int(sample_rate*0.005)
        noise = (np.random.rand(noise_len) - 0.5) * 0.01
        wave[:noise_len] += noise
        # ADSR 包络
        fade_in = int(sample_rate * 0.01)
        fade_out = int(sample_rate * 0.3)
        # 限制最大长度
        fade_in = min(fade_in, len(wave)//2)
        fade_out = min(fade_out, len(wave)//2)

        envelope = np.ones_like(wave)
        envelope[:fade_in] = np.linspace(0, 1, fade_in)
        envelope[-fade_out:] = np.linspace(1, 0, fade_out)
        wave *= envelope
        audio_data = (wave * (2**15 - 1) * volume).astype(np.int16)
        return cls.fromNpData(audio_data, sample_rate=sample_rate, channels=channels, device=device)

    def setData(self, np_data) -> None:
        self.data = np_data
        alBufferData = self.openal.alBufferData
        alBufferData.argtypes = [
            ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        alBufferData.restype = None
        format_ = self.AL_FORMAT_MONO16 if self.channels == 1 else self.AL_FORMAT_STEREO16
        ptr = np_data.ctypes.data_as(ctypes.c_void_p)
        alBufferData(self.buffer.value, format_, ptr,
                     np_data.nbytes, self.sample_rate)

    def cleanup(self) -> None:
        alDeleteBuffers = self.openal.alDeleteBuffers
        alDeleteBuffers.argtypes = [
            ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
        alDeleteBuffers.restype = None
        alDeleteBuffers(1, ctypes.byref(self.buffer))

    # 与 TextureBase 的命名保持一致
    release = cleanup

    def __del__(self) -> None:
        try:
            self.cleanup()
        except Exception:
            pass
