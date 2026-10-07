"""Tray lifecycle tests without a desktop or Windows GUI dependencies."""

from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, Mock, call, patch

from pc2mqtt import PC2MQTT
from pc2mqtt.app import main
from pc2mqtt.tray import WindowsTray


class TrayTests(unittest.TestCase):
    def setUp(self):
        self.pc = Mock()
        self.pc.client.is_connected.return_value = False
        self.icon = Mock(title='pc2mqtt — Connecting')
        self.pystray = SimpleNamespace(
            Icon=Mock(return_value=self.icon),
            Menu=lambda *items: items,
            MenuItem=lambda text, action, **kwargs: SimpleNamespace(text=text, action=action, **kwargs),
        )
        with patch.dict(sys.modules, {
            'pystray': self.pystray,
            'PIL': SimpleNamespace(Image=Mock(), ImageDraw=Mock()),
        }):
            self.tray = WindowsTray(self.pc, Path('pc2mqtt.log'))

    def test_disconnected_app_stays_available_and_status_tracks_connection(self):
        self.assertIn('retrying', self.tray._status())
        self.pc.client.is_connected.return_value = True
        self.pc.poll.side_effect = self.tray.stopped.set
        self.tray._work()
        self.assertEqual(self.tray._status(), 'MQTT connected')
        self.assertEqual(self.icon.title, 'pc2mqtt — MQTT connected')
        self.pc.client.loop_start.assert_called_once()
        self.pc.close.assert_called_once()

    def test_quit_stops_worker_and_closes_connection(self):
        polled = Event()
        self.pc.poll.side_effect = polled.set

        def run_icon(setup):
            setup(self.icon)
            self.assertTrue(polled.wait(2))
            menu = self.pystray.Icon.call_args.kwargs['menu']
            next(item for item in menu if item.text == 'Quit').action(self.icon, None)

        self.icon.run.side_effect = run_icon
        self.tray.run()
        self.assertTrue(self.icon.visible)
        self.assertFalse(self.tray.worker.is_alive())
        self.pc.close.assert_called_once()
        self.icon.stop.assert_called_once()

    def test_worker_failure_is_logged_and_visible(self):
        self.pc.poll.side_effect = RuntimeError('failed sensor')
        self.tray._work()
        self.pc.logger.exception.assert_called_once()
        self.assertIn('Error', self.tray._status())
        self.assertIn('Error', self.icon.title)
        self.pc.close.assert_called_once()
        self.icon.stop.assert_not_called()  # Keep the log and Quit actions available.

    def test_open_log_uses_registered_windows_application(self):
        with patch('pc2mqtt.tray.os.startfile', create=True) as startfile:
            self.tray._open_log(self.icon, None)
        startfile.assert_called_once_with('pc2mqtt.log')


class ApplicationLifecycleTests(unittest.TestCase):
    def test_tray_connects_asynchronously(self):
        with patch('pc2mqtt.mqtt.Client'):
            pc = PC2MQTT('broker', connect_async=True)
        pc.client.connect.assert_not_called()
        pc.client.connect_async.assert_called_once_with(host='broker', port=1883, keepalive=60)

    def test_close_announces_offline_before_disconnecting(self):
        with patch('pc2mqtt.mqtt.Client'):
            pc = PC2MQTT('broker')
        pc.client.is_connected.return_value = True
        pc.client.reset_mock()
        pc.close()
        self.assertEqual(pc.client.mock_calls, [
            call.is_connected(),
            call.publish(topic=pc.availability_topic, payload='offline', retain=True, properties=ANY),
            call.publish().wait_for_publish(timeout=2),
            call.disconnect(), call.loop_stop(),
        ])

    def test_close_disconnected_or_failed_publish_still_cleans_up(self):
        for connected in (False, True):
            with self.subTest(connected=connected), patch('pc2mqtt.mqtt.Client'):
                pc = PC2MQTT('broker')
                pc.client.is_connected.return_value = connected
                pc.client.publish.return_value.wait_for_publish.side_effect = RuntimeError('lost socket')
                pc.close()
                pc.client.disconnect.assert_called_once()
                pc.client.loop_stop.assert_called_once()
                if not connected:
                    pc.client.publish.assert_not_called()

    def test_linux_console_path_does_not_construct_tray(self):
        with patch('pc2mqtt.app.sys.platform', 'linux'), \
             patch('pc2mqtt.app.PC2MQTT') as factory, \
             patch('pc2mqtt.tray.WindowsTray') as tray:
            factory.return_value.state.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                main(['--host', 'broker'])
            factory.return_value.close.assert_called_once()
            tray.assert_not_called()
            with self.assertRaises(SystemExit) as raised:
                main(['--host', 'broker', '--tray'])
            self.assertEqual(raised.exception.code, 2)

    def test_packaged_windows_defaults_to_tray(self):
        with patch('pc2mqtt.app.sys.platform', 'win32'), \
             patch('pc2mqtt.app.sys.frozen', True, create=True), \
             patch('pc2mqtt.app.PC2MQTT') as factory, \
             patch('pc2mqtt.tray.configure_logging', return_value=Path('log')), \
             patch('pc2mqtt.tray.WindowsTray') as tray:
            main(['--host', 'broker'])
        factory.assert_called_once_with('broker', 1883, 60, display_name=None, connect_async=True)
        tray.assert_called_once_with(factory.return_value, Path('log'))
        tray.return_value.run.assert_called_once()
