"""Windows desktop APIs; executed in the logged-in user's session.

WTS reports lock state, GetLastInputInfo reports session idle time, and
GetTickCount64 reports OS uptime (Fast Startup can preserve it across shutdown).
Core Audio controls the current default render endpoint, including muted playback.
"""

from contextlib import contextmanager
import ctypes as C


class LastInput(C.Structure):
    _fields_ = [('size', C.c_uint32), ('tick', C.c_uint32)]


class SessionInfo(C.Structure):
    _fields_ = [
        ('session_id', C.c_uint32), ('state', C.c_int32), ('flags', C.c_int32),
        ('station', C.c_uint16 * 33), ('user', C.c_uint16 * 21), ('domain', C.c_uint16 * 18),
        ('logon', C.c_int64), ('connect', C.c_int64), ('disconnect', C.c_int64),
        ('last_input', C.c_int64), ('current_time', C.c_int64),
        ('counters', C.c_uint32 * 6),
    ]


class ExtendedSessionInfo(C.Structure):
    _fields_ = [('level', C.c_uint32), ('data', SessionInfo)]


@contextmanager
def endpoint():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    from pycaw.api.endpointvolume import IAudioEndpointVolume

    comtypes.CoInitialize()
    try:
        device = AudioUtilities.GetSpeakers()
        interface = device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
        yield C.cast(interface, C.POINTER(IAudioEndpointVolume))
    finally:
        comtypes.CoUninitialize()


class WindowsDesktop:
    def __init__(self):
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.user = C.WinDLL('user32', use_last_error=True)
        self.wts = C.WinDLL('wtsapi32', use_last_error=True)
        self.kernel.GetTickCount64.restype = C.c_uint64
        self.user.GetLastInputInfo.argtypes = [C.POINTER(LastInput)]
        self.user.GetLastInputInfo.restype = C.c_int
        self.user.LockWorkStation.restype = C.c_int
        self.wts.WTSQuerySessionInformationW.argtypes = [C.c_void_p, C.c_uint32, C.c_int,
                                                       C.POINTER(C.c_void_p), C.POINTER(C.c_uint32)]
        self.wts.WTSQuerySessionInformationW.restype = C.c_int
        self.wts.WTSFreeMemory.argtypes = [C.c_void_p]
        self.wts.WTSFreeMemory.restype = None
        self.user.SendNotifyMessageW.argtypes = [C.c_void_p, C.c_uint32, C.c_size_t, C.c_ssize_t]
        self.user.SendNotifyMessageW.restype = C.c_int

    def supported_features(self):
        return {'session_locked', 'idle_time', 'uptime', 'volume', 'mute',
                'lock_session', 'displays_off'}

    def locked(self):
        pointer, size = C.c_void_p(), C.c_uint32()
        # WTS_CURRENT_SESSION, WTSSessionInfoEx.
        if not self.wts.WTSQuerySessionInformationW(None, 0xffffffff, 25, C.byref(pointer), C.byref(size)):
            raise C.WinError(C.get_last_error())
        try:
            if size.value < C.sizeof(ExtendedSessionInfo):
                raise RuntimeError('Incomplete WTS session information')
            info = C.cast(pointer, C.POINTER(ExtendedSessionInfo)).contents
            if info.level != 1 or info.data.flags not in (0, 1):
                raise RuntimeError('Unknown session lock state')
            return info.data.flags == 0
        finally:
            self.wts.WTSFreeMemory(pointer)

    def read(self, key):
        if key == 'session_locked':
            return self.locked()
        if key == 'uptime':
            return self.kernel.GetTickCount64() // 1000
        if key == 'idle_time':
            info = LastInput(C.sizeof(LastInput), 0)
            if not self.user.GetLastInputInfo(C.byref(info)):
                raise C.WinError(C.get_last_error())
            return ((self.kernel.GetTickCount64() - info.tick) & 0xffffffff) // 1000
        if key in ('volume', 'mute'):
            with endpoint() as audio:
                return round(audio.GetMasterVolumeLevelScalar() * 100) if key == 'volume' else bool(audio.GetMute())
        if key in ('lock_session', 'displays_off'):
            return None
        raise ValueError(key)

    def execute(self, key, value):
        if key in ('volume', 'mute'):
            with endpoint() as audio:
                if key == 'volume':
                    audio.SetMasterVolumeLevelScalar(value / 100, None)
                else:
                    audio.SetMute(int(value), None)
        elif key == 'lock_session':
            if not self.user.LockWorkStation():
                raise C.WinError(C.get_last_error())
        elif key == 'displays_off':
            # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, power off.
            # Asynchronous notification avoids waiting for other applications.
            if not self.user.SendNotifyMessageW(0xffff, 0x112, 0xf170, 2):
                raise RuntimeError('Display-off request failed')
        else:
            raise ValueError(key)
