import json
import unittest
from unittest.mock import Mock, patch

from pc2mqtt import PC2MQTT
from pc2mqtt.app import main


class DisplayNameTests(unittest.TestCase):
    def discovery(self, display_name=None):
        with patch('pc2mqtt.platform.node', return_value='ABCD'), patch('pc2mqtt.mqtt.Client'):
            pc = PC2MQTT('broker', display_name=display_name)
        pc.integrations[-1].backend = Mock()
        pc.integrations[-1].backend.supported_features.return_value = set(pc.integrations[-1].topics)
        with patch('pc2mqtt.integrations.audio.audio_supported', return_value=True):
            pc.config()
        messages = {
            call.kwargs['topic']: json.loads(call.kwargs['payload'])
            for call in pc.client.publish.call_args_list if call.kwargs['payload'] and call.kwargs['topic'].endswith('/config')
        }
        return pc, messages

    def test_display_name_changes_without_changing_discovery_identity(self):
        original, original_messages = self.discovery()
        renamed, renamed_messages = self.discovery('foo')
        self.assertEqual(original.device['name'], 'Computer ABCD')
        self.assertEqual(renamed.device['name'], 'Computer foo')
        self.assertEqual(original.device['identifiers'], renamed.device['identifiers'])
        self.assertEqual(original.availability_topic, renamed.availability_topic)
        self.assertEqual(original_messages.keys(), renamed_messages.keys())
        self.assertEqual(len(renamed_messages), 13)
        for topic, message in renamed_messages.items():
            with self.subTest(topic=topic):
                self.assertEqual(message['device']['name'], 'Computer foo')
                self.assertEqual(message['unique_id'], original_messages[topic]['unique_id'])
                self.assertTrue(message['unique_id'].startswith('computer_abcd_'))

    def test_case_and_spaces_preserved_and_empty_rejected(self):
        pc, _ = self.discovery('  Living Room  ')
        self.assertEqual(pc.device['name'], 'Computer Living Room')
        for value in ('', '   '):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PC2MQTT('broker', display_name=value)

    def test_console_option_forwarded(self):
        with patch('pc2mqtt.app.sys.platform', 'linux'), patch('pc2mqtt.app.PC2MQTT') as factory:
            main(['--host', 'broker', '--display-name', 'foo'])
        factory.assert_called_once_with('broker', 1883, 60, display_name='foo')

    def test_tray_option_forwarded(self):
        with patch('pc2mqtt.app.sys.platform', 'win32'), \
             patch('pc2mqtt.app.PC2MQTT') as factory, \
             patch('pc2mqtt.tray.configure_logging'), patch('pc2mqtt.tray.WindowsTray'):
            main(['--host', 'broker', '--display-name', 'foo', '--tray'])
        factory.assert_called_once_with('broker', 1883, 60, display_name='foo', connect_async=True)

    def test_empty_cli_name_rejected_before_connecting(self):
        with patch('pc2mqtt.app.PC2MQTT') as factory:
            with self.assertRaises(SystemExit) as raised:
                main(['--host', 'broker', '--display-name', '  '])
            self.assertEqual(raised.exception.code, 2)
            factory.assert_not_called()
