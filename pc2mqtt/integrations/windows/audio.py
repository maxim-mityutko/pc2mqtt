"""Windows Core Audio playback and default-output volume/mute controls."""
from contextlib import contextmanager
import ctypes as C
from importlib.util import find_spec

from .._shared import AudioIntegration


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


class Backend:
    def supported_features(self):
        if all(find_spec(name) is not None for name in ('comtypes', 'pycaw')):
            return {'audio_playing', 'volume', 'mute'}
        return set()

    def unsupported_reason(self, key):
        return 'requires the Windows comtypes and pycaw libraries'

    def read(self, key):
        if key not in ('volume', 'mute'):
            raise ValueError(key)
        with endpoint() as audio:
            return round(audio.GetMasterVolumeLevelScalar() * 100) if key == 'volume' else bool(audio.GetMute())

    def execute(self, key, value):
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
                interface = device.Activate(
                    IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None
                )
                manager = interface.QueryInterface(IAudioSessionManager2)
                sessions = manager.GetSessionEnumerator()
                for session_index in range(sessions.GetCount()):
                    # AudioSessionStateActive = 1; inactive/expired sessions are idle.
                    if sessions.GetSession(session_index).GetState() == 1:
                        return True
            return False
        finally:
            comtypes.CoUninitialize()


class Audio(AudioIntegration):
    backend_type = Backend
    entities = {
        'volume': ('number', 'Volume', {
            'min': 0, 'max': 100, 'step': 1, 'mode': 'slider',
            'unit_of_measurement': '%', 'icon': 'mdi:volume-high',
        }),
        'mute': ('switch', 'Mute', {'icon': 'mdi:volume-off'}),
    }
