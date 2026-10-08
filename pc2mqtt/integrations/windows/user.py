"""Windows session lock state, last-input idle time, and workstation locking."""

import ctypes as C

from .._entities import Entity
from .._shared import EntityIntegration
from ._api import NativeAPI


class LastInput(C.Structure):
    _fields_ = [('size', C.c_uint32), ('tick', C.c_uint32)]


class SessionInfo(C.Structure):
    _fields_ = [
        ('session_id', C.c_uint32),
        ('state', C.c_int32),
        ('flags', C.c_int32),
        ('station', C.c_uint16 * 33),
        ('user', C.c_uint16 * 21),
        ('domain', C.c_uint16 * 18),
        ('logon', C.c_int64),
        ('connect', C.c_int64),
        ('disconnect', C.c_int64),
        ('last_input', C.c_int64),
        ('current_time', C.c_int64),
        ('counters', C.c_uint32 * 6),
    ]


class ExtendedSessionInfo(C.Structure):
    _fields_ = [('level', C.c_uint32), ('data', SessionInfo)]


class Backend(NativeAPI):
    features = ('session_locked', 'idle_time', 'lock_session')

    def prepare(self, key):
        if key == 'lock_session':
            self.lock = self.bind('user32', 'LockWorkStation', C.c_int)
        elif key == 'idle_time':
            self.ticks = self.bind('kernel32', 'GetTickCount64', C.c_uint64)
            self.last_input = self.bind(
                'user32', 'GetLastInputInfo', C.c_int, (C.POINTER(LastInput),)
            )
        elif key == 'session_locked':
            self.query = self.bind(
                'wtsapi32',
                'WTSQuerySessionInformationW',
                C.c_int,
                (C.c_void_p, C.c_uint32, C.c_int, C.POINTER(C.c_void_p), C.POINTER(C.c_uint32)),
            )
            self.free = self.bind('wtsapi32', 'WTSFreeMemory', None, (C.c_void_p,))
        else:
            raise ValueError(key)

    def locked(self):
        pointer, size = C.c_void_p(), C.c_uint32()
        # WTS_CURRENT_SESSION, WTSSessionInfoEx.
        if not self.query(None, 0xFFFFFFFF, 25, C.byref(pointer), C.byref(size)):
            raise C.WinError(C.get_last_error())
        try:
            if size.value < C.sizeof(ExtendedSessionInfo):
                raise RuntimeError('Incomplete WTS session information')
            info = C.cast(pointer, C.POINTER(ExtendedSessionInfo)).contents
            if info.level != 1 or info.data.flags not in (0, 1):
                raise RuntimeError('Unknown session lock state')
            return info.data.flags == 0
        finally:
            self.free(pointer)

    def read(self, key):
        self.prepare(key)
        if key == 'session_locked':
            return self.locked()
        if key == 'idle_time':
            info = LastInput(C.sizeof(LastInput), 0)
            if not self.last_input(C.byref(info)):
                raise C.WinError(C.get_last_error())
            return ((self.ticks() - info.tick) & 0xFFFFFFFF) // 1000
        raise ValueError(key)

    def check(self, key):
        if key != 'lock_session':
            raise ValueError(key)
        self.prepare(key)

    def execute(self, key, value):
        if key != 'lock_session':
            raise ValueError(key)
        self.prepare(key)
        if not self.lock():
            raise C.WinError(C.get_last_error())


class User(EntityIntegration):
    backend_type = Backend
    entities = {
        'session_locked': Entity('binary_sensor', 'Session locked', {'icon': 'mdi:lock'}),
        'idle_time': Entity(
            'sensor',
            'User idle time',
            {
                'device_class': 'duration',
                'unit_of_measurement': 'h',
                'value_template': '{{ value | float / 3600 }}',
                'suggested_display_precision': 2,
                'state_class': 'measurement',
            },
        ),
        'lock_session': Entity('button', 'Lock session', {'icon': 'mdi:lock'}),
    }
