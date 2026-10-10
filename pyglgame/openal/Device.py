import ctypes
import os
import sys
import platform

class Device:
    app_instance:"Device" = None

    def __init__(self, lib_dir="lib") -> None:
        dll_name = self._default_library_name()

        script_dir = os.path.dirname(os.path.abspath(__file__))
        dll_path = os.path.join(script_dir, lib_dir, dll_name)
        if not os.path.exists(dll_path):
            cwd_path = os.path.join(os.getcwd(), dll_name)
            if os.path.exists(cwd_path):
                dll_path = cwd_path
            else:
                raise FileNotFoundError(f"OpenAL 库未找到: {dll_name}")

        self.openal = ctypes.cdll.LoadLibrary(dll_path)
        self._init_ctypes()

        self.device = self.alcOpenDevice(None)
        if not self.device:
            raise RuntimeError("无法打开 OpenAL 设备")
        self.context = self.alcCreateContext(self.device, None)
        if not self.context:
            raise RuntimeError("无法创建 OpenAL 上下文")
        if not self.alcMakeContextCurrent(self.context):
            raise RuntimeError("无法设置上下文为当前")

    def _default_library_name(self):
        system = sys.platform
        arch, _ = platform.architecture()
        if system.startswith("win"):
            return "OpenAL64.dll" if "64" in arch else "OpenAL32.dll"
        elif system.startswith("linux"):
            return "libopenal.so"
        elif system.startswith("darwin"):
            return "OpenAL"
        else:
            raise RuntimeError(f"未知平台: {system}")

    def _init_ctypes(self) -> None:
        alc = self.openal
        self.alcOpenDevice = alc.alcOpenDevice
        self.alcOpenDevice.restype = ctypes.c_void_p
        self.alcOpenDevice.argtypes = [ctypes.c_char_p]

        self.alcCreateContext = alc.alcCreateContext
        self.alcCreateContext.restype = ctypes.c_void_p
        self.alcCreateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        self.alcMakeContextCurrent = alc.alcMakeContextCurrent
        self.alcMakeContextCurrent.restype = ctypes.c_bool
        self.alcMakeContextCurrent.argtypes = [ctypes.c_void_p]

        self.alcDestroyContext = alc.alcDestroyContext
        self.alcDestroyContext.restype = None
        self.alcDestroyContext.argtypes = [ctypes.c_void_p]

        self.alcCloseDevice = alc.alcCloseDevice
        self.alcCloseDevice.restype = ctypes.c_bool
        self.alcCloseDevice.argtypes = [ctypes.c_void_p]

    def cleanup(self) -> None:
        self.alcDestroyContext(self.context)
        self.alcCloseDevice(self.device)


if __name__ == "__main__":
    device = Device()
    print("OpenAL 初始化成功")
    device.cleanup()