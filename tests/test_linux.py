"""Linux capabilities and commands, independent of the host desktop."""
import unittest
from unittest.mock import patch

from pc2mqtt.integrations.linux import audio, power, status, user
from pc2mqtt.integrations.linux._session import Session, run


class UserTests(unittest.TestCase):
    def setUp(self):
        self.backend = user.Backend()

    def capabilities(self, properties, env, tools):
        with patch.dict('os.environ', env, clear=True), \
             patch.object(self.backend, 'session', return_value=('3', properties)), \
             patch('shutil.which', side_effect=lambda name: name if name in tools else None):
            return self.backend.supported_features()

    def test_wayland_capabilities_and_lock_hint(self):
        features = self.capabilities(
            {'Type': 'wayland', 'Desktop': 'KDE', 'LockedHint': 'no'}, {'DISPLAY': ':0'},
            {'loginctl', 'xprintidle', 'gdbus'})
        self.assertEqual(features, {'session_locked', 'lock_session'})
        self.assertEqual(self.capabilities({'Type': 'wayland'}, {}, {'loginctl'}), {'lock_session'})

    def test_gnome_and_x11_idle(self):
        for properties, env, tools in [
            ({'Type': 'wayland', 'Desktop': 'GNOME'}, {}, {'loginctl', 'gdbus'}),
            ({'Type': 'x11'}, {'DISPLAY': ':0'}, {'loginctl', 'xprintidle'}),
            ({}, {'XDG_SESSION_TYPE': 'x11', 'DISPLAY': ':0'}, {'xprintidle'}),
        ]:
            with self.subTest(properties=properties):
                self.assertIn('idle_time', self.capabilities(properties, env, tools))
        self.assertEqual(self.capabilities({}, {}, set()), set())

    def test_transient_session_failure_preserves_known_capabilities(self):
        expected = self.capabilities({'Type': 'wayland', 'Desktop': 'GNOME', 'LockedHint': 'yes'},
                                     {}, {'loginctl', 'gdbus'})
        with patch.dict('os.environ', {}, clear=True), \
             patch.object(self.backend, 'session', side_effect=OSError('bus unavailable')), \
             patch('shutil.which', side_effect=lambda name: name if name in {'loginctl', 'gdbus'} else None):
            self.assertEqual(self.backend.supported_features(), expected)
        with patch.dict('os.environ', {}, clear=True), \
             patch.object(self.backend, 'session', side_effect=RuntimeError('no graphical session')), \
             patch('shutil.which', return_value='/usr/bin/loginctl'):
            self.assertEqual(self.backend.supported_features(), set())

    def test_session_resolves_user_service_and_rejects_other_user(self):
        with patch.dict('os.environ', {}, clear=True), patch('os.getuid', return_value=1000, create=True), \
             patch('pc2mqtt.integrations.linux._session.run', side_effect=['3', 'User=1000\nType=wayland\nLockedHint=yes']) as command:
            self.assertTrue(self.backend.read('session_locked'))
            command.assert_any_call('loginctl', 'show-user', '1000', '-p', 'Display', '--value')
        with patch.dict('os.environ', {'XDG_SESSION_ID': '3'}, clear=True), \
             patch('os.getuid', return_value=1000, create=True), \
             patch('pc2mqtt.integrations.linux._session.run', return_value='User=2000\nType=x11'):
            with self.assertRaises(RuntimeError):
                self.backend.read('session_locked')

    def test_idle_gnome_and_x11_fallback(self):
        with patch.object(user, 'run', return_value='(uint64 12500,)'):
            self.assertEqual(self.backend.read('idle_time'), 12)
        with patch.object(self.backend, 'x11', return_value=True), \
             patch.object(user, 'run', side_effect=[FileNotFoundError(), '23400']):
            self.assertEqual(self.backend.read('idle_time'), 23)
        with patch.object(self.backend, 'x11', return_value=False), \
             patch.object(user, 'run', side_effect=FileNotFoundError()):
            with self.assertRaises(NotImplementedError):
                self.backend.read('idle_time')

    def test_lock_command(self):
        with patch.object(self.backend, 'session', return_value=('3', {})), patch.object(user, 'run') as command:
            self.backend.execute('lock_session', 'PRESS')
        command.assert_called_once_with('loginctl', 'lock-session', '3')


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.backend = power.Backend()

    def test_independent_power_dependencies_and_systemd_environment(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(self.backend, 'properties', return_value={}), \
             patch('shutil.which', return_value=None):
            self.assertEqual(self.backend.supported_features(), set())
        with patch.dict('os.environ', {}, clear=True), patch.object(self.backend, 'properties', return_value={}), \
             patch('shutil.which', side_effect=lambda name: name if name == 'shutdown' else None):
            self.assertEqual(self.backend.supported_features(), {'shutdown', 'restart'})
        with patch.dict('os.environ', {}, clear=True), patch.object(self.backend, 'properties', return_value={}), \
             patch('shutil.which', side_effect=lambda name: name if name == 'systemctl' else None), \
             patch.object(power.Path, 'is_dir', return_value=False):
            self.assertNotIn('sleep', self.backend.supported_features())
            with patch.object(power.Path, 'is_dir', return_value=True):
                self.assertEqual(self.backend.supported_features(), {'sleep'})

    def test_display_capabilities_follow_session(self):
        for env, expected in [
            ({'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':0'}, False),
            ({'XDG_SESSION_TYPE': 'x11', 'DISPLAY': ':0'}, True),
            ({'SWAYSOCK': '/run/sway.sock'}, True),
            ({}, False),
        ]:
            with self.subTest(env=env), patch.dict('os.environ', env, clear=True), \
                 patch.object(self.backend, 'properties', return_value={}), \
                 patch('shutil.which', side_effect=lambda name: name if name in {'xset', 'swaymsg'} else None):
                self.assertEqual('displays_off' in self.backend.supported_features(), expected)

    def test_displays_x11_sway_and_unsupported_wayland(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(self.backend, 'x11', return_value=True), \
             patch('shutil.which', return_value='/usr/bin/xset'), patch.object(power, 'run') as command:
            self.backend.execute('displays_off', 'PRESS')
            command.assert_called_with('xset', 'dpms', 'force', 'off')
        with patch.dict('os.environ', {'SWAYSOCK': '/run/sway.sock'}, clear=True), \
             patch('shutil.which', return_value='/usr/bin/swaymsg'), \
             patch.object(power, 'run', return_value='[{"success":false}]'):
            with self.assertRaises(RuntimeError):
                self.backend.execute('displays_off', 'PRESS')
        with patch.dict('os.environ', {}, clear=True), patch.object(self.backend, 'x11', return_value=False):
            with self.assertRaises(NotImplementedError):
                self.backend.read('displays_off')


class AudioTests(unittest.TestCase):
    def test_audio_values_and_invalid_responses(self):
        backend = audio.Backend()
        with patch.object(audio, 'run', side_effect=['Volume: left: 40% right: 50%', 'Mute: yes', 'Mute: no', 'invalid', 'invalid']):
            self.assertEqual(backend.read('volume'), 50)
            self.assertTrue(backend.read('mute'))
            self.assertFalse(backend.read('mute'))
            for key in ('volume', 'mute'):
                with self.assertRaises(ValueError):
                    backend.read(key)

    def test_audio_commands(self):
        backend = audio.Backend()
        with patch.object(audio, 'run') as command:
            backend.execute('volume', 25.5)
            command.assert_called_with('pactl', 'set-sink-volume', '@DEFAULT_SINK@', '25.5%')
            backend.execute('mute', False)
            command.assert_called_with('pactl', 'set-sink-mute', '@DEFAULT_SINK@', '0')


class StatusTests(unittest.TestCase):
    def test_uptime_and_missing_proc(self):
        backend = status.Backend()
        with patch.object(status.Path, 'read_text', return_value='12345.67 345.0'):
            self.assertEqual(backend.read('uptime'), 12345)
        with patch.object(status.Path, 'is_file', return_value=False):
            self.assertEqual(backend.supported_features(), set())


class SessionTests(unittest.TestCase):
    def test_native_commands_have_timeout_and_no_shell(self):
        with patch('pc2mqtt.integrations.linux._session.subprocess.run') as command:
            command.return_value.stdout = 'Mute: no\n'
            self.assertEqual(run('pactl', 'get-sink-mute', '@DEFAULT_SINK@'), 'Mute: no')
        self.assertEqual(command.call_args.kwargs['timeout'], 3)
        self.assertTrue(command.call_args.kwargs['check'])
        self.assertNotIn('shell', command.call_args.kwargs)
        self.assertEqual(command.call_args.kwargs['env']['LC_ALL'], 'C')

    def test_x11_without_loginctl_matches_capability_detection(self):
        with patch.dict('os.environ', {'DISPLAY': ':0', 'XDG_SESSION_TYPE': 'x11'}, clear=True), \
             patch('shutil.which', return_value=None):
            self.assertTrue(Session().x11())
