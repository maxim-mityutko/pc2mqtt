import ctypes as C
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pc2mqtt.integrations.desktop import Desktop, ENTITIES
from pc2mqtt.integrations.desktop.linux import LinuxDesktop, run
from pc2mqtt.integrations.desktop.windows import WindowsDesktop, ExtendedSessionInfo, LastInput


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.is_connected.return_value = True
        self.desktop = Desktop(self.client, 'pc', {'name': 'Computer foo', 'model': 'Linux'}, 'connection', Mock())
        self.values = {'session_locked': False, 'idle_time': 12, 'uptime': 500,
                       'volume': 40, 'mute': False, 'lock_session': None, 'displays_off': None}
        self.desktop.backend = Mock()
        self.desktop.backend.read.side_effect = lambda key: self.values[key]

    def poll(self, now):
        with patch('pc2mqtt.integrations.desktop.time.monotonic', return_value=now):
            self.desktop.poll()

    def messages(self):
        return {c.kwargs['topic']: c.kwargs['payload'] for c in self.client.publish.call_args_list}

    def message(self, key, payload, retain=False):
        return SimpleNamespace(topic=f'{self.desktop.topics[key]}/set', payload=payload, retain=retain)

    def test_discovery_and_commands(self):
        self.desktop.config()
        messages = self.messages()
        configs = {key: json.loads(messages[f'{topic}/config']) for key, topic in self.desktop.topics.items()}
        self.assertEqual(len(configs), 7)
        self.assertEqual(len({cfg['unique_id'] for cfg in configs.values()}), 7)
        self.assertEqual(configs['volume']['max'], 100)
        self.assertEqual(configs['mute']['payload_on'], 'ON')
        for key in ('idle_time', 'uptime'):
            self.assertEqual(configs[key]['unit_of_measurement'], 'h')
            self.assertEqual(configs[key]['value_template'], '{{ value | float / 3600 }}')
            self.assertEqual(configs[key]['suggested_display_precision'], 2)
        self.assertNotIn('state_topic', configs['lock_session'])
        self.assertEqual(self.client.subscribe.call_count, 4)
        for key, cfg in configs.items():
            self.assertEqual(cfg['availability_mode'], 'all')
            self.assertEqual(cfg['availability'][0], {'topic': 'connection'})
            self.assertEqual(cfg['device']['name'], 'Computer foo')
            self.assertEqual(messages[f'{self.desktop.topics[key]}/availability'], 'offline')
        for c in self.client.publish.call_args_list:
            self.assertTrue(c.kwargs['retain'])
            self.assertEqual(c.kwargs['properties'].MessageExpiryInterval, 43200)

    def test_cadence_changes_and_heartbeat(self):
        self.poll(0)
        self.assertEqual(self.messages()[f'{self.desktop.topics["volume"]}/state'], '40')
        self.client.reset_mock()
        self.poll(9)
        self.client.publish.assert_not_called()
        self.poll(10)
        self.client.publish.assert_not_called()
        self.values['session_locked'] = True
        self.poll(20)
        self.assertEqual(self.messages()[f'{self.desktop.topics["session_locked"]}/state'], 'ON')
        self.client.reset_mock()
        self.poll(60)
        self.assertIn(f'{self.desktop.topics["volume"]}/state', self.messages())

    def test_invalid_retained_and_unknown_commands_do_not_execute(self):
        for key, payload in [('volume', b'nan'), ('volume', b'inf'), ('volume', b'-1'),
                             ('volume', b'101'), ('volume', b'50; touch bad'), ('mute', b'toggle'),
                             ('mute', b'\xff'), ('lock_session', b'OFF'), ('displays_off', b'OFF')]:
            self.assertTrue(self.desktop.on_message(self.message(key, payload)))
        for key, payload in [('volume', b'50'), ('mute', b'ON'), ('lock_session', b'PRESS'), ('displays_off', b'PRESS')]:
            self.desktop.on_message(self.message(key, payload, retain=True))
        self.assertFalse(self.desktop.on_message(SimpleNamespace(topic='unknown')))
        self.poll(0)
        self.desktop.backend.execute.assert_not_called()

    def test_each_control_is_queued_and_read_back(self):
        for key, payload, expected in [('volume', b'23.5', 23.5), ('mute', b'ON', True),
                                       ('mute', b'OFF', False), ('lock_session', b'PRESS', 'PRESS'),
                                       ('displays_off', b'PRESS', 'PRESS')]:
            with self.subTest(key=key, payload=payload):
                self.desktop.backend.execute.reset_mock()
                self.desktop.on_message(self.message(key, payload))
                self.desktop.backend.execute.assert_not_called()
                self.poll(1)
                self.desktop.backend.execute.assert_called_once_with(key, expected)
                self.assertIn(f'{self.desktop.topics["volume"]}/state', self.messages())

    def test_volume_and_mute_publish_actual_readback(self):
        self.poll(0)
        self.client.reset_mock()
        def execute(key, value):
            self.values[key] = value
        self.desktop.backend.execute.side_effect = execute
        self.desktop.on_message(self.message('volume', b'25'))
        self.poll(1)
        self.assertEqual(self.messages()[f'{self.desktop.topics["volume"]}/state'], '25.0')
        self.desktop.on_message(self.message('mute', b'ON'))
        self.poll(2)
        self.assertEqual(self.messages()[f'{self.desktop.topics["mute"]}/state'], 'ON')

    def test_backend_failure_is_isolated_and_recovers(self):
        def read(key):
            if key == 'idle_time':
                raise NotImplementedError('no idle monitor')
            return self.values[key]
        self.desktop.backend.read.side_effect = read
        self.poll(0)
        self.assertEqual(self.messages()[f'{self.desktop.topics["idle_time"]}/availability'], 'offline')
        self.assertEqual(self.messages()[f'{self.desktop.topics["uptime"]}/state'], '500')
        self.poll(10)
        self.desktop.logger.warning.assert_called_once()
        self.desktop.backend.read.side_effect = lambda key: self.values[key]
        self.poll(20)
        self.assertEqual(self.messages()[f'{self.desktop.topics["idle_time"]}/availability'], 'online')

    def test_action_failure_not_reported_as_success(self):
        self.desktop.backend.execute.side_effect = OSError('denied')
        self.desktop.on_message(self.message('lock_session', b'PRESS'))
        self.poll(0)
        self.assertEqual(self.messages()[f'{self.desktop.topics["lock_session"]}/availability'], 'offline')
        self.assertIn(f'{self.desktop.topics["volume"]}/state', self.messages())

    def test_disconnect_and_reconnect_clear_stale_commands(self):
        self.desktop.on_message(self.message('lock_session', b'PRESS'))
        self.client.is_connected.return_value = False
        self.poll(0)
        self.desktop.backend.execute.assert_not_called()
        self.desktop.backend.read.assert_not_called()
        self.desktop.config()
        self.client.is_connected.return_value = True
        self.poll(1)
        self.desktop.backend.execute.assert_not_called()


class LinuxDesktopTests(unittest.TestCase):
    def setUp(self):
        self.backend = LinuxDesktop()

    def test_native_commands_have_timeout_and_no_shell(self):
        with patch('pc2mqtt.integrations.desktop.linux.subprocess.run') as command:
            command.return_value.stdout = 'Mute: no\n'
            self.assertEqual(run('pactl', 'get-sink-mute', '@DEFAULT_SINK@'), 'Mute: no')
        self.assertEqual(command.call_args.kwargs['timeout'], 3)
        self.assertTrue(command.call_args.kwargs['check'])
        self.assertNotIn('shell', command.call_args.kwargs)
        self.assertEqual(command.call_args.kwargs['env']['LC_ALL'], 'C')

    def test_session_resolves_user_service_and_rejects_other_user(self):
        with patch.dict('os.environ', {}, clear=True), patch('pc2mqtt.integrations.desktop.linux.os.getuid', return_value=1000, create=True), \
             patch('pc2mqtt.integrations.desktop.linux.run', side_effect=['3', 'User=1000\nType=wayland\nLockedHint=yes']) as command:
            self.assertTrue(self.backend.read('session_locked'))
            command.assert_any_call('loginctl', 'show-user', '1000', '-p', 'Display', '--value')
        with patch.dict('os.environ', {'XDG_SESSION_ID': '3'}, clear=True), \
             patch('pc2mqtt.integrations.desktop.linux.os.getuid', return_value=1000, create=True), \
             patch('pc2mqtt.integrations.desktop.linux.run', return_value='User=2000\nType=x11'):
            with self.assertRaises(RuntimeError):
                self.backend.read('session_locked')

    def test_idle_gnome_and_x11_fallback(self):
        with patch('pc2mqtt.integrations.desktop.linux.run', return_value='(uint64 12500,)'):
            self.assertEqual(self.backend.read('idle_time'), 12)
        with patch.object(self.backend, 'x11', return_value=True), \
             patch('pc2mqtt.integrations.desktop.linux.run', side_effect=[FileNotFoundError(), '23400']):
            self.assertEqual(self.backend.read('idle_time'), 23)
        with patch.object(self.backend, 'x11', return_value=False), \
             patch('pc2mqtt.integrations.desktop.linux.run', side_effect=FileNotFoundError()):
            with self.assertRaises(NotImplementedError):
                self.backend.read('idle_time')

    def test_uptime_and_audio_values(self):
        with patch('pc2mqtt.integrations.desktop.linux.Path.read_text', return_value='12345.67 345.0'):
            self.assertEqual(self.backend.read('uptime'), 12345)
        with patch('pc2mqtt.integrations.desktop.linux.run', side_effect=['Volume: left: 40% right: 50%', 'Mute: yes', 'Mute: no', 'invalid']):
            self.assertEqual(self.backend.read('volume'), 50)
            self.assertTrue(self.backend.read('mute'))
            self.assertFalse(self.backend.read('mute'))
            with self.assertRaises(ValueError):
                self.backend.read('volume')

    def test_volume_mute_and_session_lock_commands(self):
        with patch.object(self.backend, 'session', return_value=('3', {})), \
             patch('pc2mqtt.integrations.desktop.linux.run') as command:
            self.backend.execute('volume', 25.5)
            command.assert_called_with('pactl', 'set-sink-volume', '@DEFAULT_SINK@', '25.5%')
            self.backend.execute('mute', False)
            command.assert_called_with('pactl', 'set-sink-mute', '@DEFAULT_SINK@', '0')
            self.backend.execute('lock_session', 'PRESS')
            command.assert_called_with('loginctl', 'lock-session', '3')

    def test_displays_x11_sway_and_unsupported_wayland(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(self.backend, 'x11', return_value=True), \
             patch('pc2mqtt.integrations.desktop.linux.shutil.which', return_value='/usr/bin/xset'), \
             patch('pc2mqtt.integrations.desktop.linux.run') as command:
            self.backend.execute('displays_off', 'PRESS')
            command.assert_called_with('xset', 'dpms', 'force', 'off')
        with patch.dict('os.environ', {'SWAYSOCK': '/run/sway.sock'}, clear=True), \
             patch('pc2mqtt.integrations.desktop.linux.shutil.which', return_value='/usr/bin/swaymsg'), \
             patch('pc2mqtt.integrations.desktop.linux.run', return_value='[{"success":false}]'):
            with self.assertRaises(RuntimeError):
                self.backend.execute('displays_off', 'PRESS')
        with patch.dict('os.environ', {}, clear=True), patch.object(self.backend, 'x11', return_value=False):
            with self.assertRaises(NotImplementedError):
                self.backend.read('displays_off')


class WindowsDesktopTests(unittest.TestCase):
    def setUp(self):
        with patch('ctypes.WinDLL', create=True) as dll:
            dll.side_effect = [Mock(), Mock(), Mock()]
            self.backend = WindowsDesktop()

    def test_uptime_is_64_bit_and_idle_handles_tick_wrap(self):
        self.backend.kernel.GetTickCount64.return_value = 2**32 + 10000
        self.assertEqual(self.backend.read('uptime'), (2**32 + 10000) // 1000)
        self.assertIs(self.backend.kernel.GetTickCount64.restype, C.c_uint64)
        def last_input(pointer):
            C.cast(pointer, C.POINTER(LastInput)).contents.tick = 2**32 - 5000
            return 1
        self.backend.user.GetLastInputInfo.side_effect = last_input
        self.assertEqual(self.backend.read('idle_time'), 15)

    def test_wts_lock_state_and_buffer_cleanup(self):
        info = ExtendedSessionInfo()
        info.level = 1
        def query(server, session, kind, pointer, size):
            self.assertEqual((session, kind), (0xffffffff, 25))
            C.cast(pointer, C.POINTER(C.c_void_p))[0] = C.addressof(info)
            C.cast(size, C.POINTER(C.c_uint32))[0] = C.sizeof(info)
            return 1
        self.backend.wts.WTSQuerySessionInformationW.side_effect = query
        for flag, expected in [(0, True), (1, False)]:
            info.data.flags = flag
            self.assertEqual(self.backend.read('session_locked'), expected)
        info.data.flags = -1
        with self.assertRaises(RuntimeError):
            self.backend.read('session_locked')
        self.assertEqual(self.backend.wts.WTSFreeMemory.call_count, 3)

    def test_audio_readback_and_controls(self):
        with patch('pc2mqtt.integrations.desktop.windows.endpoint') as endpoint:
            audio = endpoint.return_value.__enter__.return_value
            audio.GetMasterVolumeLevelScalar.return_value = .42
            audio.GetMute.return_value = 1
            self.assertEqual(self.backend.read('volume'), 42)
            self.assertTrue(self.backend.read('mute'))
            self.backend.execute('volume', 21)
            audio.SetMasterVolumeLevelScalar.assert_called_once_with(.21, None)
            self.backend.execute('mute', False)
            audio.SetMute.assert_called_once_with(0, None)

    def test_actions_use_native_apis(self):
        self.backend.execute('lock_session', 'PRESS')
        self.backend.user.LockWorkStation.assert_called_once_with()
        self.backend.execute('displays_off', 'PRESS')
        args = self.backend.user.SendNotifyMessageW.call_args.args
        self.assertEqual(args, (0xffff, 0x112, 0xf170, 2))
        self.backend.user.SendNotifyMessageW.return_value = 0
        with self.assertRaises(RuntimeError):
            self.backend.execute('displays_off', 'PRESS')
