import json
from unittest.mock import patch

import pytest

from pc2mqtt import PC2MQTT
from pc2mqtt.app import main


class TestDisplayName:
    @pytest.fixture(autouse=True)
    def setup(self, make_app):
        self.make_app = make_app

    def discovery(self, display_name=None):
        h = self.make_app(display_name=display_name)
        h.app.config()
        messages = {
            call.kwargs['topic']: json.loads(call.kwargs['payload'])
            for call in h.client.publish.call_args_list
            if call.kwargs['payload'] and call.kwargs['topic'].endswith('/config')
        }
        return h.app, messages

    def test_display_name_changes_without_changing_discovery_identity(self):
        original, original_messages = self.discovery()
        renamed, renamed_messages = self.discovery('foo')
        assert original.device['name'] == 'Computer DESKTOP'
        assert renamed.device['name'] == 'Computer foo'
        assert original.device['identifiers'] == renamed.device['identifiers']
        assert original.availability_topic == renamed.availability_topic
        assert original_messages.keys() == renamed_messages.keys()
        assert len(renamed_messages) == 13
        for topic, message in renamed_messages.items():
            assert message['device']['name'] == 'Computer foo'
            assert message['unique_id'] == original_messages[topic]['unique_id']
            assert message['unique_id'].startswith('computer_desktop_')

    def test_case_and_spaces_preserved_and_empty_rejected(self):
        pc, _ = self.discovery('  Living Room  ')
        assert pc.device['name'] == 'Computer Living Room'
        for value in ('', '   '):
            with pytest.raises(ValueError):
                PC2MQTT('broker', display_name=value)

    def test_console_option_forwarded(self):
        with patch('pc2mqtt.app.sys.platform', 'linux'), patch('pc2mqtt.app.PC2MQTT') as factory:
            main(['--host', 'broker', '--display-name', 'foo'])
        factory.assert_called_once_with('broker', 1883, 60, display_name='foo')

    def test_tray_option_forwarded(self):
        with (
            patch('pc2mqtt.app.sys.platform', 'win32'),
            patch('pc2mqtt.app.PC2MQTT') as factory,
            patch('pc2mqtt.tray.configure_logging'),
            patch('pc2mqtt.tray.WindowsTray'),
        ):
            main(['--host', 'broker', '--display-name', 'foo', '--tray'])
        factory.assert_called_once_with('broker', 1883, 60, display_name='foo', connect_async=True)

    def test_empty_cli_name_rejected_before_connecting(self):
        with patch('pc2mqtt.app.PC2MQTT') as factory:
            with pytest.raises(SystemExit) as raised:
                main(['--host', 'broker', '--display-name', '  '])
            assert raised.value.code == 2
            factory.assert_not_called()
