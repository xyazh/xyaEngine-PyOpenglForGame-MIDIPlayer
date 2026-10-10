import sys
import os
import ctypes
import ctypes.util

from .al_lib import lib

ALC_FALSE = 0
ALC_TRUE = 1
ALC_FREQUENCY = 0x1007
ALC_REFRESH = 0x1008
ALC_SYNC = 0x1009
ALC_MONO_SOURCES = 0x1010
ALC_STEREO_SOURCES = 0x1011
ALC_NO_ERROR = ALC_FALSE
ALC_INVALID_DEVICE = 0xA001
ALC_INVALID_CONTEXT = 0xA002
ALC_INVALID_ENUM = 0xA003
ALC_INVALID_VALUE = 0xA004
ALC_OUT_OF_MEMORY = 0xA005
ALC_DEFAULT_DEVICE_SPECIFIER = 0x1004
ALC_DEVICE_SPECIFIER = 0x1005
ALC_EXTENSIONS = 0x1006
ALC_MAJOR_VERSION = 0x1000
ALC_MINOR_VERSION = 0x1001
ALC_ATTRIBUTES_SIZE = 0x1002
ALC_ALL_ATTRIBUTES = 0x1003
ALC_CAPTURE_DEVICE_SPECIFIER = 0x310
ALC_CAPTURE_DEFAULT_DEVICE_SPECIFIER = 0x311
ALC_CAPTURE_SAMPLES = 0x312

alc_enums = {}
local_items = list(locals().items())
for k, v in local_items:
    if type(v) != int: continue
    if not v in alc_enums:
        alc_enums[v] = []
    alc_enums[v].append(k)

class ALCError(Exception):
    pass

def alc_check_error(result, func, arguments):
    err = alcGetError(0)
    if err:
        raise ALCError(alc_enums[err][0])
    return result

alcCreateContext = lib.alcCreateContext
alcCreateContext.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
alcCreateContext.restype = ctypes.c_void_p
alcCreateContext.errcheck = alc_check_error

alcMakeContextCurrent = lib.alcMakeContextCurrent
alcMakeContextCurrent.argtypes = [ctypes.c_void_p]
alcMakeContextCurrent.restype = ctypes.c_uint8
alcMakeContextCurrent.errcheck = alc_check_error

alcProcessContext = lib.alcProcessContext
alcProcessContext.argtypes = [ctypes.c_void_p]
alcProcessContext.restype = None
alcProcessContext.errcheck = alc_check_error

alcSuspendContext = lib.alcSuspendContext
alcSuspendContext.argtypes = [ctypes.c_void_p]
alcSuspendContext.restype = None
alcSuspendContext.errcheck = alc_check_error

alcDestroyContext = lib.alcDestroyContext
alcDestroyContext.argtypes = [ctypes.c_void_p]
alcDestroyContext.restype = None
alcDestroyContext.errcheck = alc_check_error

alcGetCurrentContext = lib.alcGetCurrentContext
alcGetCurrentContext.argtypes = []
alcGetCurrentContext.restype = ctypes.c_void_p
alcGetCurrentContext.errcheck = alc_check_error

alcGetContextsDevice = lib.alcGetContextsDevice
alcGetContextsDevice.argtypes = [ctypes.c_void_p]
alcGetContextsDevice.restype = ctypes.c_void_p
alcGetContextsDevice.errcheck = alc_check_error

alcOpenDevice = lib.alcOpenDevice
alcOpenDevice.argtypes = [ctypes.c_char_p]
alcOpenDevice.restype = ctypes.c_void_p
alcOpenDevice.errcheck = alc_check_error

alcCloseDevice = lib.alcCloseDevice
alcCloseDevice.argtypes = [ctypes.c_void_p]
alcCloseDevice.restype = ctypes.c_uint8
alcCloseDevice.errcheck = alc_check_error

alcGetError = lib.alcGetError
alcGetError.argtypes = [ctypes.c_void_p]
alcGetError.restype = ctypes.c_int

alcIsExtensionPresent = lib.alcIsExtensionPresent
alcIsExtensionPresent.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
alcIsExtensionPresent.restype = ctypes.c_uint8
alcIsExtensionPresent.errcheck = alc_check_error

alcGetProcAddress = lib.alcGetProcAddress
alcGetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
alcGetProcAddress.restype = ctypes.c_void_p
alcGetProcAddress.errcheck = alc_check_error

alcGetEnumValue = lib.alcGetEnumValue
alcGetEnumValue.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
alcGetEnumValue.restype = ctypes.c_int
alcGetEnumValue.errcheck = alc_check_error

alcGetString = lib.alcGetString
alcGetString.argtypes = [ctypes.c_void_p, ctypes.c_int]
alcGetString.restype = ctypes.c_char_p
alcGetString.errcheck = alc_check_error

alcGetIntegerv = lib.alcGetIntegerv
alcGetIntegerv.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alcGetIntegerv.restype = None
alcGetIntegerv.errcheck = alc_check_error

alcCaptureOpenDevice = lib.alcCaptureOpenDevice
alcCaptureOpenDevice.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int]
alcCaptureOpenDevice.restype = ctypes.c_void_p
alcCaptureOpenDevice.errcheck = alc_check_error

alcCaptureCloseDevice = lib.alcCaptureCloseDevice
alcCaptureCloseDevice.argtypes = [ctypes.c_void_p]
alcCaptureCloseDevice.restype = ctypes.c_uint8
alcCaptureCloseDevice.errcheck = alc_check_error

alcCaptureStart = lib.alcCaptureStart
alcCaptureStart.argtypes = [ctypes.c_void_p]
alcCaptureStart.restype = None
alcCaptureStart.errcheck = alc_check_error

alcCaptureStop = lib.alcCaptureStop
alcCaptureStop.argtypes = [ctypes.c_void_p]
alcCaptureStop.restype = None
alcCaptureStop.errcheck = alc_check_error

alcCaptureSamples = lib.alcCaptureSamples
alcCaptureSamples.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
alcCaptureSamples.restype = None
alcCaptureSamples.errcheck = alc_check_error
