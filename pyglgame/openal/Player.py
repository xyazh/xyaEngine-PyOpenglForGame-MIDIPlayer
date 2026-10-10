import ctypes
from .Device import Device
from .AudioResource import AudioResource


class Player:
    AL_PLAYING = 0x1012
    AL_SOURCE_STATE = 0x1010
    AL_BUFFER = 0x1009
    AL_POSITION = 0x1004
    AL_LOOPING = 0x1007
    AL_GAIN = 0x100A
    # 设备通常只允许 256 个 source（实测 soft_oal 就是 256）；超过后
    # alGenSources 会失败并返回 0，之后的音就全部静音了。
    DEFAULT_MAX_SOURCES = 64
    app_instance: "Player" = None

    def __init__(self, device: Device, max_sources: int = DEFAULT_MAX_SOURCES) -> None:
        self.device = device
        self.openal = device.openal
        self.max_sources = max(1, int(max_sources))
        self._sources: list[int] = []      # 已分配（长期复用）的 source
        self._busy: list[int] = []         # 正在播放的 source，按占用顺序
        self.source_created = 0
        self.source_steal = 0
        self.source_failed = 0
        self._initSourceFuncs()

    @property
    def source_count(self) -> int:
        return len(self._sources)

    def _initSourceFuncs(self) -> None:
        al = self.openal
        self.alSourcePlay = al.alSourcePlay
        self.alSourcePlay.argtypes = [ctypes.c_uint]
        self.alSourceStop = al.alSourceStop
        self.alSourceStop.argtypes = [ctypes.c_uint]
        self.alSourcei = al.alSourcei
        self.alSourcei.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
        self.alSource3f = al.alSource3f
        self.alSource3f.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_float, ctypes.c_float, ctypes.c_float]
        self.alSourcef = al.alSourcef
        self.alSourcef.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_float]
        self.alGetSourcei = al.alGetSourcei
        self.alGetSourcei.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]
        self.alGenSources = al.alGenSources
        self.alGenSources.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
        self.alGenSources.restype = None
        self.alDeleteSources = al.alDeleteSources
        self.alDeleteSources.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
        self.alDeleteSources.restype = None

    def _createSource(self):
        src = ctypes.c_uint(0)
        self.alGenSources(1, ctypes.byref(src))
        return src.value

    def _recycleFinished(self) -> None:
        """把已经播完的 source 放回空闲池（不删除，避免反复 gen/delete）。"""
        if not self._busy:
            return
        self._busy = [src for src in self._busy if self.isPlaying(src)]

    def _acquire(self) -> int:
        """取一个可用 source；空闲 → 新建 → 抢占最老的（保证不会超出设备上限）。"""
        self._recycleFinished()
        for src in self._sources:
            if src not in self._busy:
                return src
        if len(self._sources) < self.max_sources:
            src = self._createSource()
            if src:
                self._sources.append(src)
                self.source_created += 1
                return src
            self.source_failed += 1
        if self._busy:
            # 达到并发上限（或设备拒绝新建）：抢占最早占用的那个
            victim = self._busy.pop(0)
            self.alSourceStop(victim)
            self.source_steal += 1
            return victim
        return 0

    def play(self, resource: AudioResource, loop: bool = False):
        src = self._acquire()
        if src == 0:
            return 0
        buf_id = resource.buffer
        if isinstance(buf_id, ctypes.c_uint):
            buf_id = buf_id.value
        self.alSourcei(src, self.AL_BUFFER, buf_id)
        self.alSourcei(src, self.AL_LOOPING, 1 if loop else 0)
        self.alSourcePlay(src)
        if src not in self._busy:
            self._busy.append(src)
        return src


    def setPosition(self,src:int, x:float, y:float, z:float) -> None:
        self.alSource3f(src, self.AL_POSITION, x, y, z)

    def stopLoop(self, src: int) -> None:
        self.alSourcei(src, self.AL_LOOPING, 0)

    def setLoop(self, src: int, loop: bool) -> None:
        self.alSourcei(src, self.AL_LOOPING, 1 if loop else 0)

    def stop(self, src: int) -> None:
        """停止播放并把 source 放回池子（不再删除 source：避免设备上限被耗尽）。"""
        if src <= 0:
            return
        self.alSourceStop(src)
        if src in self._busy:
            self._busy.remove(src)

    def stopAll(self) -> None:
        for src in list(self._busy):
            self.alSourceStop(src)
        self._busy.clear()

    def isPlaying(self, src: int):
        state = ctypes.c_int(0)
        self.alGetSourcei(src, self.AL_SOURCE_STATE, ctypes.byref(state))
        return state.value == self.AL_PLAYING

    def setVolume(self, src: int, volume: float) -> None:
        self.alSourcef(src, self.AL_GAIN, float(volume))

    def release(self) -> None:
        """释放池里所有 source（通常在退出时调用）。"""
        self._busy.clear()
        for src in list(self._sources):
            self.alSourceStop(src)
            handle = ctypes.c_uint(src)
            self.alDeleteSources(1, ctypes.byref(handle))
        self._sources.clear()


class NullPlayer:
    """音频不可用时的空实现（接口与 :class:`Player` 一致，全部静默忽略）。"""

    app_instance: "NullPlayer" = None
    max_sources = 0
    source_count = 0

    def play(self, resource, loop: bool = False):
        return 0

    def setPosition(self, src: int, x: float, y: float, z: float) -> None:
        pass

    def stopLoop(self, src: int) -> None:
        pass

    def setLoop(self, src: int, loop: bool) -> None:
        pass

    def stop(self, src: int) -> None:
        pass

    def stopAll(self) -> None:
        pass

    def isPlaying(self, src: int):
        return False

    def setVolume(self, src: int, volume: float) -> None:
        pass

    def release(self) -> None:
        pass

