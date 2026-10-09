"""Windows Core Audio playback and default-output volume/mute controls."""

import ctypes as C
from contextlib import contextmanager
from importlib.util import find_spec

from .._entities import Entity
from .._shared import EntityIntegration
from ._api import NativeAPI


class KeyboardInput(C.Structure):
    _fields_ = [
        ('key', C.c_uint16),
        ('scan', C.c_uint16),
        ('flags', C.c_uint32),
        ('time', C.c_uint32),
        ('extra', C.c_size_t),
    ]


class MouseInput(C.Structure):
    # INPUT's union must include MOUSEINPUT to have the correct Win32 ABI size.
    _fields_ = [
        ('x', C.c_int32),
        ('y', C.c_int32),
        ('data', C.c_uint32),
        ('flags', C.c_uint32),
        ('time', C.c_uint32),
        ('extra', C.c_size_t),
    ]


class InputData(C.Union):
    _fields_ = [('keyboard', KeyboardInput), ('mouse', MouseInput)]


class Input(C.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', C.c_uint32), ('data', InputData)]


@contextmanager
def endpoint():
    import comtypes
    from pycaw.api.endpointvolume import IAudioEndpointVolume
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        device = AudioUtilities.GetSpeakers()
        interface = device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
        yield C.cast(interface, C.POINTER(IAudioEndpointVolume))
    finally:
        comtypes.CoUninitialize()


class Backend(NativeAPI):
    features = ('play_pause',)

    def prepare(self, key):
        if key != 'play_pause':
            raise ValueError(key)
        self.send_input = self.bind(
            'user32', 'SendInput', C.c_uint32,
            (C.c_uint32, C.POINTER(Input), C.c_int),
        )

    def check(self, key):
        self.prepare(key)

    def supported_features(self):
        features = super().supported_features()
        if all(find_spec(name) is not None for name in ('comtypes', 'pycaw')):
            features.update(('audio_playing', 'volume', 'mute'))
        return features

    def unsupported_reason(self, key):
        if key == 'play_pause':
            return super().unsupported_reason(key)
        return 'requires the Windows comtypes and pycaw libraries'

    def read(self, key):
        if key == 'audio_playing':
            return self.is_audio_playing()
        if key not in ('volume', 'mute'):
            raise ValueError(key)
        with endpoint() as audio:
            return (
                round(audio.GetMasterVolumeLevelScalar() * 100)
                if key == 'volume'
                else bool(audio.GetMute())
            )

    def execute(self, key, value):
        if key == 'play_pause':
            self.prepare(key)
            # INPUT_KEYBOARD, VK_MEDIA_PLAY_PAUSE, KEYEVENTF_KEYUP.
            events = (Input * 2)()
            for event in events:
                event.type = 1
                event.keyboard.key = 0xB3
            events[1].keyboard.flags = 0x0002
            sent = self.send_input(2, events, C.sizeof(Input))
            if sent != 2:
                if sent == 1:
                    # Release the key if only the key-down event was accepted.
                    self.send_input(1, C.pointer(events[1]), C.sizeof(Input))
                raise RuntimeError('Unable to send the media play/pause key')
            return
        if key not in ('volume', 'mute'):
            raise ValueError(key)
        with endpoint() as audio:
            if key == 'volume':
                audio.SetMasterVolumeLevelScalar(value / 100, None)
            else:
                audio.SetMute(int(value), None)

    def is_audio_playing(self) -> bool:
        # Lazy imports keep the Windows-only COM dependencies off Linux.
        import comtypes
        from pycaw.api.audiopolicy import IAudioSessionManager2
        from pycaw.constants import DEVICE_STATE, EDataFlow
        from pycaw.pycaw import AudioUtilities

        comtypes.CoInitialize()
        try:
            enumerator = AudioUtilities.GetDeviceEnumerator()
            devices = enumerator.EnumAudioEndpoints(
                EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value
            )
            # Inspect every output, including playback routed away from the default.
            for index in range(devices.GetCount()):
                device = devices.Item(index)
                interface = device.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
                manager = interface.QueryInterface(IAudioSessionManager2)
                sessions = manager.GetSessionEnumerator()
                for session_index in range(sessions.GetCount()):
                    # AudioSessionStateActive = 1; inactive/expired sessions are idle.
                    if sessions.GetSession(session_index).GetState() == 1:
                        return True
            return False
        finally:
            comtypes.CoUninitialize()


class Audio(EntityIntegration):
    backend_type = Backend
    entities = {
        'play_pause': Entity('button', 'Play / Pause', {'icon': 'mdi:play-pause'}),
        'audio_playing': Entity(
            'binary_sensor',
            'Audio playing',
            {'icon': 'mdi:volume-high', 'expire_after': 90},
            interval=0,
            behavior='playback',
        ),
        'volume': Entity(
            'number',
            'Volume',
            {
                'min': 0,
                'max': 100,
                'step': 1,
                'mode': 'slider',
                'unit_of_measurement': '%',
                'icon': 'mdi:volume-high',
            },
        ),
        'mute': Entity('switch', 'Mute', {'icon': 'mdi:volume-off'}),
    }
