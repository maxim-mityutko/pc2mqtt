"""Windows native API contracts, mocked so they also run on Linux."""

import ctypes as C
from unittest.mock import Mock, patch

import pytest

from pc2mqtt.integrations.windows import audio, power, status, user


class TestUser:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        self.dlls = {name: Mock() for name in ('kernel32', 'user32', 'wtsapi32')}
        monkeypatch.setattr(C, 'WinDLL', lambda name, **kwargs: self.dlls[name], raising=False)
        self.backend = user.Backend()

    def test_idle_handles_tick_wrap(self):
        self.dlls['kernel32'].GetTickCount64.return_value = 2**32 + 10000

        def last_input(pointer):
            C.cast(pointer, C.POINTER(user.LastInput)).contents.tick = 2**32 - 5000
            return 1

        self.dlls['user32'].GetLastInputInfo.side_effect = last_input
        assert self.backend.read('idle_time') == 15
        assert self.dlls['kernel32'].GetTickCount64.restype is C.c_uint64

    def test_wts_lock_state_and_buffer_cleanup(self):
        info = user.ExtendedSessionInfo()
        info.level = 1

        def query(server, session, kind, pointer, size):
            assert (session, kind) == (0xFFFFFFFF, 25)
            C.cast(pointer, C.POINTER(C.c_void_p))[0] = C.addressof(info)
            C.cast(size, C.POINTER(C.c_uint32))[0] = C.sizeof(info)
            return 1

        self.dlls['wtsapi32'].WTSQuerySessionInformationW.side_effect = query
        for flag, expected in [(0, True), (1, False)]:
            info.data.flags = flag
            assert self.backend.read('session_locked') == expected
        info.data.flags = -1
        with pytest.raises(RuntimeError):
            self.backend.read('session_locked')
        assert self.dlls['wtsapi32'].WTSFreeMemory.call_count == 3

    def test_lock_action(self):
        self.backend.execute('lock_session', 'PRESS')
        self.dlls['user32'].LockWorkStation.assert_called_once_with()

    def test_missing_wts_does_not_hide_other_capabilities(self):
        with patch(
            'ctypes.WinDLL',
            create=True,
            side_effect=lambda name, **kwargs: self.dlls[name] if name != 'wtsapi32' else None,
        ):
            assert self.backend.supported_features() == {'idle_time', 'lock_session'}
            assert 'WTSQuerySessionInformationW' in self.backend.unsupported_reason(
                'session_locked'
            )
        assert self.backend.supported_features() == {'session_locked', 'idle_time', 'lock_session'}


class TestAudio:
    def test_audio_readback_and_controls(self):
        backend = audio.Backend()
        with patch.object(audio, 'endpoint') as endpoint:
            native = endpoint.return_value.__enter__.return_value
            native.GetMasterVolumeLevelScalar.return_value = 0.42
            native.GetMute.return_value = 1
            assert backend.read('volume') == 42
            assert backend.read('mute')
            backend.execute('volume', 21)
            native.SetMasterVolumeLevelScalar.assert_called_once_with(0.21, None)
            backend.execute('mute', False)
            native.SetMute.assert_called_once_with(0, None)

    def test_missing_audio_libraries_hide_all_audio_entities(self):
        for missing in ('comtypes', 'pycaw'):
            with patch.object(
                audio, 'find_spec', side_effect=lambda name: None if name == missing else Mock()
            ):
                assert audio.Backend().supported_features() == set()
        with patch.object(audio, 'find_spec', return_value=Mock()):
            assert audio.Backend().supported_features() == {'audio_playing', 'volume', 'mute'}


class TestPower:
    def test_display_action_uses_async_native_api(self):
        with patch('ctypes.WinDLL', create=True) as dll:
            backend = power.Backend()
            backend.execute('displays_off', 'PRESS')
            native = dll.return_value.SendNotifyMessageW
            native.assert_called_once_with(0xFFFF, 0x112, 0xF170, 2)
            native.return_value = 0
            with pytest.raises(RuntimeError):
                backend.execute('displays_off', 'PRESS')

    def test_capabilities_are_independent(self):
        with (
            patch('ctypes.WinDLL', create=True, side_effect=OSError('no user32')),
            patch('shutil.which', side_effect=lambda name: name if name == 'shutdown' else None),
        ):
            assert power.Backend().supported_features() == {'shutdown', 'restart'}
        with patch('ctypes.WinDLL', create=True), patch('shutil.which', return_value=None):
            assert power.Backend().supported_features() == {'displays_off'}


class TestStatus:
    def test_uptime_uses_64_bit_api(self):
        with patch('ctypes.WinDLL', create=True) as dll:
            dll.return_value.GetTickCount64.return_value = 2**32 + 10000
            assert status.Backend().read('uptime') == (2**32 + 10000) // 1000
            assert dll.return_value.GetTickCount64.restype is C.c_uint64
        with patch('ctypes.WinDLL', create=True, side_effect=OSError('missing API')):
            assert status.Backend().supported_features() == set()
