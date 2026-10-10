import sys
import os
import ctypes
import ctypes.util

from .al_lib import lib

AL_NONE = 0
AL_FALSE = 0
AL_TRUE = 1
AL_SOURCE_RELATIVE = 0x202
AL_CONE_INNER_ANGLE = 0x1001
AL_CONE_OUTER_ANGLE = 0x1002
AL_PITCH = 0x1003
AL_POSITION = 0x1004
AL_DIRECTION = 0x1005
AL_VELOCITY = 0x1006
AL_LOOPING = 0x1007
AL_BUFFER = 0x1009
AL_GAIN = 0x100A
AL_MIN_GAIN = 0x100D
AL_MAX_GAIN = 0x100E
AL_ORIENTATION = 0x100F
AL_SOURCE_STATE = 0x1010
AL_INITIAL = 0x1011
AL_PLAYING = 0x1012
AL_PAUSED = 0x1013
AL_STOPPED = 0x1014
AL_BUFFERS_QUEUED = 0x1015
AL_BUFFERS_PROCESSED = 0x1016
AL_SEC_OFFSET = 0x1024
AL_SAMPLE_OFFSET = 0x1025
AL_BYTE_OFFSET = 0x1026
AL_SOURCE_TYPE = 0x1027
AL_STATIC = 0x1028
AL_STREAMING = 0x1029
AL_UNDETERMINED = 0x1030
AL_FORMAT_MONO8 = 0x1100
AL_FORMAT_MONO16 = 0x1101
AL_FORMAT_STEREO8 = 0x1102
AL_FORMAT_STEREO16 = 0x1103
AL_REFERENCE_DISTANCE = 0x1020
AL_ROLLOFF_FACTOR = 0x1021
AL_CONE_OUTER_GAIN = 0x1022
AL_MAX_DISTANCE = 0x1023
AL_FREQUENCY = 0x2001
AL_BITS = 0x2002
AL_CHANNELS = 0x2003
AL_SIZE = 0x2004
AL_UNUSED = 0x2010
AL_PENDING = 0x2011
AL_PROCESSED = 0x2012
AL_NO_ERROR = AL_FALSE
AL_INVALID_NAME = 0xA001
AL_INVALID_ENUM = 0xA002
AL_INVALID_VALUE = 0xA003
AL_INVALID_OPERATION = 0xA004
AL_OUT_OF_MEMORY = 0xA005
AL_VENDOR = 0xB001
AL_VERSION = 0xB002
AL_RENDERER = 0xB003
AL_EXTENSIONS = 0xB004
AL_DOPPLER_FACTOR = 0xC000
AL_DOPPLER_VELOCITY = 0xC001
AL_SPEED_OF_SOUND = 0xC003
AL_DISTANCE_MODEL = 0xD000
AL_INVERSE_DISTANCE = 0xD001
AL_INVERSE_DISTANCE_CLAMPED = 0xD002
AL_LINEAR_DISTANCE = 0xD003
AL_LINEAR_DISTANCE_CLAMPED = 0xD004
AL_EXPONENT_DISTANCE = 0xD005
AL_EXPONENT_DISTANCE_CLAMPED = 0xD006

al_enums = {}
local_items = list(locals().items())
for k, v in local_items:
    if type(v) != int: continue
    if not v in al_enums:
        al_enums[v] = []
    al_enums[v].append(k)

class ALError(Exception):
    pass

alGetError = lib.alGetError
alGetError.argtypes = []
alGetError.restype = ctypes.c_int

def al_check_error(result, func, arguments):
    err = alGetError()
    if err:
        raise ALError(al_enums[err][0])
    return result

alEnable = lib.alEnable
alEnable.argtypes = [ctypes.c_int]
alEnable.restype = None
alEnable.errcheck = al_check_error

alDisable = lib.alDisable
alDisable.argtypes = [ctypes.c_int]
alDisable.restype = None
alDisable.errcheck = al_check_error

alIsEnabled = lib.alIsEnabled
alIsEnabled.argtypes = [ctypes.c_int]
alIsEnabled.restype = ctypes.c_uint8
alIsEnabled.errcheck = al_check_error

alGetString = lib.alGetString
alGetString.argtypes = [ctypes.c_int]
alGetString.restype = ctypes.c_char_p
alGetString.errcheck = al_check_error

alGetBooleanv = lib.alGetBooleanv
alGetBooleanv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint8)]
alGetBooleanv.restype = None
alGetBooleanv.errcheck = al_check_error

alGetIntegerv = lib.alGetIntegerv
alGetIntegerv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alGetIntegerv.restype = None
alGetIntegerv.errcheck = al_check_error

alGetFloatv = lib.alGetFloatv
alGetFloatv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alGetFloatv.restype = None
alGetFloatv.errcheck = al_check_error

alGetDoublev = lib.alGetDoublev
alGetDoublev.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_double)]
alGetDoublev.restype = None
alGetDoublev.errcheck = al_check_error

alGetBoolean = lib.alGetBoolean
alGetBoolean.argtypes = [ctypes.c_int]
alGetBoolean.restype = ctypes.c_uint8
alGetBoolean.errcheck = al_check_error

alGetInteger = lib.alGetInteger
alGetInteger.argtypes = [ctypes.c_int]
alGetInteger.restype = ctypes.c_int
alGetInteger.errcheck = al_check_error

alGetFloat = lib.alGetFloat
alGetFloat.argtypes = [ctypes.c_int]
alGetFloat.restype = ctypes.c_float
alGetFloat.errcheck = al_check_error

alGetDouble = lib.alGetDouble
alGetDouble.argtypes = [ctypes.c_int]
alGetDouble.restype = ctypes.c_double
alGetDouble.errcheck = al_check_error



alIsExtensionPresent = lib.alIsExtensionPresent
alIsExtensionPresent.argtypes = [ctypes.c_char_p]
alIsExtensionPresent.restype = ctypes.c_uint8
alIsExtensionPresent.errcheck = al_check_error

alGetProcAddress = lib.alGetProcAddress
alGetProcAddress.argtypes = [ctypes.c_char_p]
alGetProcAddress.restype = ctypes.c_void_p
alGetProcAddress.errcheck = al_check_error

alGetEnumValue = lib.alGetEnumValue
alGetEnumValue.argtypes = [ctypes.c_char_p]
alGetEnumValue.restype = ctypes.c_int
alGetEnumValue.errcheck = al_check_error

alListenerf = lib.alListenerf
alListenerf.argtypes = [ctypes.c_int, ctypes.c_float]
alListenerf.restype = None
alListenerf.errcheck = al_check_error

alListener3f = lib.alListener3f
alListener3f.argtypes = [ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float]
alListener3f.restype = None
alListener3f.errcheck = al_check_error

alListenerfv = lib.alListenerfv
alListenerfv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alListenerfv.restype = None
alListenerfv.errcheck = al_check_error

alListeneri = lib.alListeneri
alListeneri.argtypes = [ctypes.c_int, ctypes.c_int]
alListeneri.restype = None
alListeneri.errcheck = al_check_error

alListener3i = lib.alListener3i
alListener3i.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
alListener3i.restype = None
alListener3i.errcheck = al_check_error

alListeneriv = lib.alListeneriv
alListeneriv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alListeneriv.restype = None
alListeneriv.errcheck = al_check_error

alGetListenerf = lib.alGetListenerf
alGetListenerf.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alGetListenerf.restype = None
alGetListenerf.errcheck = al_check_error

alGetListener3f = lib.alGetListener3f
alGetListener3f.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
alGetListener3f.restype = None
alGetListener3f.errcheck = al_check_error

alGetListenerfv = lib.alGetListenerfv
alGetListenerfv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alGetListenerfv.restype = None
alGetListenerfv.errcheck = al_check_error

alGetListeneri = lib.alGetListeneri
alGetListeneri.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alGetListeneri.restype = None
alGetListeneri.errcheck = al_check_error

alGetListener3i = lib.alGetListener3i
alGetListener3i.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
alGetListener3i.restype = None
alGetListener3i.errcheck = al_check_error

alGetListeneriv = lib.alGetListeneriv
alGetListeneriv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alGetListeneriv.restype = None
alGetListeneriv.errcheck = al_check_error

alGenSources = lib.alGenSources
alGenSources.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alGenSources.restype = None
alGenSources.errcheck = al_check_error

alDeleteSources = lib.alDeleteSources
alDeleteSources.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alDeleteSources.restype = None
alDeleteSources.errcheck = al_check_error

alIsSource = lib.alIsSource
alIsSource.argtypes = [ctypes.c_uint]
alIsSource.restype = ctypes.c_uint8
alIsSource.errcheck = al_check_error

alSourcef = lib.alSourcef
alSourcef.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float]
alSourcef.restype = None
alSourcef.errcheck = al_check_error

alSource3f = lib.alSource3f
alSource3f.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float]
alSource3f.restype = None
alSource3f.errcheck = al_check_error

alSourcefv = lib.alSourcefv
alSourcefv.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alSourcefv.restype = None
alSourcefv.errcheck = al_check_error

alSourcei = lib.alSourcei
alSourcei.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int]
alSourcei.restype = None
alSourcei.errcheck = al_check_error

alSource3i = lib.alSource3i
alSource3i.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
alSource3i.restype = None
alSource3i.errcheck = al_check_error

alSourceiv = lib.alSourceiv
alSourceiv.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alSourceiv.restype = None
alSourceiv.errcheck = al_check_error

alGetSourcef = lib.alGetSourcef
alGetSourcef.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alGetSourcef.restype = None
alGetSourcef.errcheck = al_check_error

alGetSource3f = lib.alGetSource3f
alGetSource3f.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
alGetSource3f.restype = None
alGetSource3f.errcheck = al_check_error

alGetSourcefv = lib.alGetSourcefv
alGetSourcefv.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alGetSourcefv.restype = None
alGetSourcefv.errcheck = al_check_error

alGetSourcei = lib.alGetSourcei
alGetSourcei.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alGetSourcei.restype = None
alGetSourcei.errcheck = al_check_error

alGetSource3i = lib.alGetSource3i
alGetSource3i.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
alGetSource3i.restype = None
alGetSource3i.errcheck = al_check_error

alGetSourceiv = lib.alGetSourceiv
alGetSourceiv.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alGetSourceiv.restype = None
alGetSourceiv.errcheck = al_check_error

alSourcePlayv = lib.alSourcePlayv
alSourcePlayv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alSourcePlayv.restype = None
alSourcePlayv.errcheck = al_check_error

alSourceStopv = lib.alSourceStopv
alSourceStopv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alSourceStopv.restype = None
alSourceStopv.errcheck = al_check_error

alSourceRewindv = lib.alSourceRewindv
alSourceRewindv.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alSourceRewindv.restype = None
alSourceRewindv.errcheck = al_check_error

alSourcePausev = lib.alSourcePausev
alSourcePausev.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alSourcePausev.restype = None
alSourcePausev.errcheck = al_check_error

alSourcePlay = lib.alSourcePlay
alSourcePlay.argtypes = [ctypes.c_uint]
alSourcePlay.restype = None
alSourcePlay.errcheck = al_check_error

alSourceStop = lib.alSourceStop
alSourceStop.argtypes = [ctypes.c_uint]
alSourceStop.restype = None
alSourceStop.errcheck = al_check_error

alSourceRewind = lib.alSourceRewind
alSourceRewind.argtypes = [ctypes.c_uint]
alSourceRewind.restype = None
alSourceRewind.errcheck = al_check_error

alSourcePause = lib.alSourcePause
alSourcePause.argtypes = [ctypes.c_uint]
alSourcePause.restype = None
alSourcePause.errcheck = al_check_error

alSourceQueueBuffers = lib.alSourceQueueBuffers
alSourceQueueBuffers.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alSourceQueueBuffers.restype = None
alSourceQueueBuffers.errcheck = al_check_error

alSourceUnqueueBuffers = lib.alSourceUnqueueBuffers
alSourceUnqueueBuffers.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alSourceUnqueueBuffers.restype = None
alSourceUnqueueBuffers.errcheck = al_check_error

alGenBuffers = lib.alGenBuffers
alGenBuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alGenBuffers.restype = None
alGenBuffers.errcheck = al_check_error

alDeleteBuffers = lib.alDeleteBuffers
alDeleteBuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
alDeleteBuffers.restype = None
alDeleteBuffers.errcheck = al_check_error

alIsBuffer = lib.alIsBuffer
alIsBuffer.argtypes = [ctypes.c_uint]
alIsBuffer.restype = ctypes.c_uint8
alIsBuffer.errcheck = al_check_error

alBufferData = lib.alBufferData
alBufferData.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
alBufferData.restype = None
alBufferData.errcheck = al_check_error

alBufferf = lib.alBufferf
alBufferf.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float]
alBufferf.restype = None
alBufferf.errcheck = al_check_error

alBuffer3f = lib.alBuffer3f
alBuffer3f.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float]
alBuffer3f.restype = None
alBuffer3f.errcheck = al_check_error

alBufferfv = lib.alBufferfv
alBufferfv.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alBufferfv.restype = None
alBufferfv.errcheck = al_check_error

alBufferi = lib.alBufferi
alBufferi.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int]
alBufferi.restype = None
alBufferi.errcheck = al_check_error

alBuffer3i = lib.alBuffer3i
alBuffer3i.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
alBuffer3i.restype = None
alBuffer3i.errcheck = al_check_error

alBufferiv = lib.alBufferiv
alBufferiv.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alBufferiv.restype = None
alBufferiv.errcheck = al_check_error

alGetBufferf = lib.alGetBufferf
alGetBufferf.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alGetBufferf.restype = None
alGetBufferf.errcheck = al_check_error

alGetBuffer3f = lib.alGetBuffer3f
alGetBuffer3f.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
alGetBuffer3f.restype = None
alGetBuffer3f.errcheck = al_check_error

alGetBufferfv = lib.alGetBufferfv
alGetBufferfv.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
alGetBufferfv.restype = None
alGetBufferfv.errcheck = al_check_error

alGetBufferi = lib.alGetBufferi
alGetBufferi.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alGetBufferi.restype = None
alGetBufferi.errcheck = al_check_error

alGetBuffer3i = lib.alGetBuffer3i
alGetBuffer3i.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
alGetBuffer3i.restype = None
alGetBuffer3i.errcheck = al_check_error

alGetBufferiv = lib.alGetBufferiv
alGetBufferiv.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
alGetBufferiv.restype = None
alGetBufferiv.errcheck = al_check_error

alDopplerFactor = lib.alDopplerFactor
alDopplerFactor.argtypes = [ctypes.c_float]
alDopplerFactor.restype = None
alDopplerFactor.errcheck = al_check_error

alDopplerVelocity = lib.alDopplerVelocity
alDopplerVelocity.argtypes = [ctypes.c_float]
alDopplerVelocity.restype = None
alDopplerVelocity.errcheck = al_check_error

alSpeedOfSound = lib.alSpeedOfSound
alSpeedOfSound.argtypes = [ctypes.c_float]
alSpeedOfSound.restype = None
alSpeedOfSound.errcheck = al_check_error

alDistanceModel = lib.alDistanceModel
alDistanceModel.argtypes = [ctypes.c_int]
alDistanceModel.restype = None
alDistanceModel.errcheck = al_check_error
