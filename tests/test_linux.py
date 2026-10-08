"""Linux capabilities and commands, independent of the host desktop."""

from unittest.mock import patch

import pytest

from pc2mqtt.integrations.linux import audio, power, status, user
from pc2mqtt.integrations.linux._session import Session, run


class TestUser:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.backend = user.Backend()

    def capabilities(self, properties, env, tools):
        with (
            patch.dict('os.environ', env, clear=True),
            patch.object(self.backend, 'session', return_value=('3', properties)),
            patch('shutil.which', side_effect=lambda name: name if name in tools else None),
        ):
            return self.backend.supported_features()

    def test_wayland_capabilities_and_lock_hint(self):
        features = self.capabilities(
            {'Type': 'wayland', 'Desktop': 'KDE', 'LockedHint': 'no'},
            {'DISPLAY': ':0'},
            {'loginctl', 'xprintidle', 'gdbus'},
        )
        assert features == {'session_locked', 'lock_session'}
        assert self.capabilities({'Type': 'wayland'}, {}, {'loginctl'}) == {'lock_session'}

    def test_gnome_and_x11_idle(self):
        for properties, env, tools in [
            ({'Type': 'wayland', 'Desktop': 'GNOME'}, {}, {'loginctl', 'gdbus'}),
            ({'Type': 'x11'}, {'DISPLAY': ':0'}, {'loginctl', 'xprintidle'}),
            ({}, {'XDG_SESSION_TYPE': 'x11', 'DISPLAY': ':0'}, {'xprintidle'}),
        ]:
            assert 'idle_time' in self.capabilities(properties, env, tools)
        assert self.capabilities({}, {}, set()) == set()

    def test_transient_session_failure_preserves_known_capabilities(self):
        expected = self.capabilities(
            {'Type': 'wayland', 'Desktop': 'GNOME', 'LockedHint': 'yes'}, {}, {'loginctl', 'gdbus'}
        )
        with (
            patch.dict('os.environ', {}, clear=True),
            patch.object(self.backend, 'session', side_effect=OSError('bus unavailable')),
            patch(
                'shutil.which',
                side_effect=lambda name: name if name in {'loginctl', 'gdbus'} else None,
            ),
        ):
            assert self.backend.supported_features() == expected
        with (
            patch.dict('os.environ', {}, clear=True),
            patch.object(self.backend, 'session', side_effect=RuntimeError('no graphical session')),
            patch('shutil.which', return_value='/usr/bin/loginctl'),
        ):
            assert self.backend.supported_features() == set()

    def test_session_resolves_user_service_and_rejects_other_user(self):
        with (
            patch.dict('os.environ', {}, clear=True),
            patch('os.getuid', return_value=1000, create=True),
            patch(
                'pc2mqtt.integrations.linux._session.run',
                side_effect=['3', 'User=1000\nType=wayland\nLockedHint=yes'],
            ) as command,
        ):
            assert self.backend.read('session_locked')
            command.assert_any_call('loginctl', 'show-user', '1000', '-p', 'Display', '--value')
        with (
            patch.dict('os.environ', {'XDG_SESSION_ID': '3'}, clear=True),
            patch('os.getuid', return_value=1000, create=True),
            patch('pc2mqtt.integrations.linux._session.run', return_value='User=2000\nType=x11'),
        ):
            with pytest.raises(RuntimeError):
                self.backend.read('session_locked')

    def test_idle_gnome_and_x11_fallback(self):
        with patch.object(user, 'run', return_value='(uint64 12500,)'):
            assert self.backend.read('idle_time') == 12
        with (
            patch.object(self.backend, 'x11', return_value=True),
            patch.object(user, 'run', side_effect=[FileNotFoundError(), '23400']),
        ):
            assert self.backend.read('idle_time') == 23
        with (
            patch.object(self.backend, 'x11', return_value=False),
            patch.object(user, 'run', side_effect=FileNotFoundError()),
        ):
            with pytest.raises(NotImplementedError):
                self.backend.read('idle_time')

    def test_lock_command(self):
        with (
            patch.object(self.backend, 'session', return_value=('3', {})),
            patch.object(user, 'run') as command,
        ):
            self.backend.execute('lock_session', 'PRESS')
        command.assert_called_once_with('loginctl', 'lock-session', '3')


class TestPower:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.backend = power.Backend()

    def test_independent_power_dependencies_and_systemd_environment(self):
        with (
            patch.dict('os.environ', {}, clear=True),
            patch.object(self.backend, 'properties', return_value={}),
            patch('shutil.which', return_value=None),
        ):
            assert self.backend.supported_features() == set()
        with (
            patch.dict('os.environ', {}, clear=True),
            patch.object(self.backend, 'properties', return_value={}),
            patch('shutil.which', side_effect=lambda name: name if name == 'shutdown' else None),
        ):
            assert self.backend.supported_features() == {'shutdown', 'restart'}
        with (
            patch.dict('os.environ', {}, clear=True),
            patch.object(self.backend, 'properties', return_value={}),
            patch('shutil.which', side_effect=lambda name: name if name == 'systemctl' else None),
            patch.object(power.Path, 'is_dir', return_value=False),
        ):
            assert 'sleep' not in self.backend.supported_features()
            with patch.object(power.Path, 'is_dir', return_value=True):
                assert self.backend.supported_features() == {'sleep'}

    def test_display_capabilities_follow_session(self):
        for env, expected in [
            ({'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':0'}, False),
            ({'XDG_SESSION_TYPE': 'x11', 'DISPLAY': ':0'}, True),
            ({'SWAYSOCK': '/run/sway.sock'}, True),
            ({}, False),
        ]:
            with (
                patch.dict('os.environ', env, clear=True),
                patch.object(self.backend, 'properties', return_value={}),
                patch(
                    'shutil.which',
                    side_effect=lambda name: name if name in {'xset', 'swaymsg'} else None,
                ),
            ):
                assert ('displays_off' in self.backend.supported_features()) == expected

    def test_displays_x11_sway_and_unsupported_wayland(self):
        with (
            patch.dict('os.environ', {}, clear=True),
            patch.object(self.backend, 'x11', return_value=True),
            patch('shutil.which', return_value='/usr/bin/xset'),
            patch.object(power, 'run') as command,
        ):
            self.backend.execute('displays_off', 'PRESS')
            command.assert_called_with('xset', 'dpms', 'force', 'off')
        with (
            patch.dict('os.environ', {'SWAYSOCK': '/run/sway.sock'}, clear=True),
            patch('shutil.which', return_value='/usr/bin/swaymsg'),
            patch.object(power, 'run', return_value='[{"success":false}]'),
        ):
            with pytest.raises(RuntimeError):
                self.backend.execute('displays_off', 'PRESS')
        with (
            patch.dict('os.environ', {}, clear=True),
            patch.object(self.backend, 'x11', return_value=False),
        ):
            with pytest.raises(NotImplementedError):
                self.backend.check('displays_off')


class TestAudio:
    def test_audio_values_and_invalid_responses(self):
        backend = audio.Backend()
        with patch.object(
            audio,
            'run',
            side_effect=[
                'Volume: left: 40% right: 50%',
                'Mute: yes',
                'Mute: no',
                'invalid',
                'invalid',
            ],
        ):
            assert backend.read('volume') == 50
            assert backend.read('mute')
            assert not backend.read('mute')
            for key in ('volume', 'mute'):
                with pytest.raises(ValueError):
                    backend.read(key)

    def test_audio_commands(self):
        backend = audio.Backend()
        with patch.object(audio, 'run') as command:
            backend.execute('volume', 25.5)
            command.assert_called_with('pactl', 'set-sink-volume', '@DEFAULT_SINK@', '25.5%')
            backend.execute('mute', False)
            command.assert_called_with('pactl', 'set-sink-mute', '@DEFAULT_SINK@', '0')


class TestStatus:
    def test_uptime_and_missing_proc(self):
        backend = status.Backend()
        with patch.object(status.Path, 'read_text', return_value='12345.67 345.0'):
            assert backend.read('uptime') == 12345
        with patch.object(status.Path, 'is_file', return_value=False):
            assert backend.supported_features() == set()


class TestSession:
    def test_native_commands_have_timeout_and_no_shell(self):
        with patch('pc2mqtt.integrations.linux._session.subprocess.run') as command:
            command.return_value.stdout = 'Mute: no\n'
            assert run('pactl', 'get-sink-mute', '@DEFAULT_SINK@') == 'Mute: no'
        assert command.call_args.kwargs['timeout'] == 3
        assert command.call_args.kwargs['check']
        assert 'shell' not in command.call_args.kwargs
        assert command.call_args.kwargs['env']['LC_ALL'] == 'C'

    def test_x11_without_loginctl_matches_capability_detection(self):
        with (
            patch.dict('os.environ', {'DISPLAY': ':0', 'XDG_SESSION_TYPE': 'x11'}, clear=True),
            patch('shutil.which', return_value=None),
        ):
            assert Session().x11()
