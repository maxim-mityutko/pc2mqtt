"""Platform registration, entity ownership, and MQTT capability filtering."""
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pc2mqtt import PC2MQTT
from pc2mqtt.integrations import integration_types


OWNERS = {
    'audio': {'audio_playing', 'volume', 'mute'},
    'power': {'shutdown', 'sleep', 'restart', 'displays_off'},
    'status': {'ip_address', 'last_seen', 'uptime'},
    'user': {'session_locked', 'idle_time', 'lock_session'},
}


def publications(client):
    return {call.kwargs['topic']: call.kwargs['payload'] for call in client.publish.call_args_list}


def subscriptions(client):
    return {call.args[0] if call.args else call.kwargs['topic'] for call in client.subscribe.call_args_list}


class IntegrationTests(unittest.TestCase):
    def make(self, cls, features):
        client = Mock()
        client.socket.return_value.getsockname.return_value = ('192.0.2.1', 1883)
        integration = cls(client, 'pc', {'model': 'Test', 'name': 'Computer PC'}, 'connection', Mock())
        integration.backend = Mock()
        integration.backend.supported_features.return_value = features
        integration.backend.unsupported_reason.return_value = 'missing dependency'
        integration.config()
        return integration, client

    def test_both_platforms_have_identical_exclusive_entity_ownership(self):
        for system in ('Linux', 'Windows'):
            seen = set()
            for cls in integration_types(system):
                domain = cls.__module__.rsplit('.', 1)[-1]
                with self.subTest(system=system, domain=domain):
                    integration, client = self.make(cls, OWNERS[domain])
                    configs = [json.loads(value) for topic, value in publications(client).items()
                               if topic.endswith('/config') and value]
                    keys = {cfg['unique_id'].removeprefix('computer_pc_') for cfg in configs}
                    self.assertEqual(keys, OWNERS[domain])
                    self.assertFalse(seen & keys)
                    seen.update(keys)
                    for cfg in configs:
                        key = cfg['unique_id'].removeprefix('computer_pc_')
                        for name in ('command_topic', 'state_topic'):
                            if name in cfg:
                                self.assertIn(f'/pc/{key}/', cfg[name])
            self.assertEqual(len(seen), 13)

    def test_no_native_capabilities_leaves_only_portable_heartbeat(self):
        for system in ('Linux', 'Windows'):
            for cls in integration_types(system):
                with self.subTest(system=system, integration=cls.__name__):
                    integration, client = self.make(cls, set())
                    messages = publications(client)
                    configs = [json.loads(value) for topic, value in messages.items()
                               if topic.endswith('/config') and value]
                    self.assertEqual({cfg['name'] for cfg in configs},
                                     {'IP address', 'Last seen'} if cls.__name__ == 'Status' else set())
                    client.subscribe.assert_not_called()
                    client.reset_mock()
                    integration.poll()
                    integration.backend.read.assert_not_called()
                    integration.backend.is_audio_playing.assert_not_called()

    def test_partial_capabilities_cleanup_and_recovery(self):
        for system in ('Linux', 'Windows'):
            for cls in integration_types(system):
                domain = cls.__module__.rsplit('.', 1)[-1]
                for missing in OWNERS[domain] - {'ip_address', 'last_seen'}:
                    with self.subTest(system=system, domain=domain, missing=missing):
                        integration, client = self.make(cls, OWNERS[domain])
                        original = publications(client)
                        topic = next(topic.removesuffix('/config') for topic, value in original.items()
                                     if topic.endswith('/config') and value and json.loads(value)['unique_id'] == f'computer_pc_{missing}')
                        cfg = json.loads(original[f'{topic}/config'])
                        client.reset_mock()
                        integration.backend.supported_features.return_value = OWNERS[domain] - {missing}
                        integration.config()
                        messages = publications(client)
                        for suffix in ('config', 'state', 'availability'):
                            self.assertEqual(messages[f'{topic}/{suffix}'], '')
                        if 'command_topic' in cfg:
                            self.assertNotIn(cfg['command_topic'], subscriptions(client))
                            client.unsubscribe.assert_any_call(cfg['command_topic'])
                            integration.on_message(SimpleNamespace(topic=cfg['command_topic'], payload=b'PRESS', retain=False))
                            with patch('pc2mqtt.integrations._shared.subprocess.Popen') as launch:
                                integration.poll()
                            integration.backend.execute.assert_not_called()
                            launch.assert_not_called()
                        client.reset_mock()
                        integration.backend.supported_features.return_value = OWNERS[domain]
                        integration.config()
                        self.assertEqual(json.loads(publications(client)[f'{topic}/config'])['unique_id'], f'computer_pc_{missing}')
                        if 'command_topic' in cfg:
                            self.assertIn(cfg['command_topic'], subscriptions(client))

    def test_reconnect_discards_commands_even_if_capability_detection_fails(self):
        for domain, key in [('power', 'shutdown'), ('user', 'lock_session'), ('audio', 'mute')]:
            cls = next(cls for cls in integration_types('Linux') if cls.__name__.lower() == domain)
            integration, client = self.make(cls, OWNERS[domain])
            topic = next(topic for topic in subscriptions(client) if f'/{key}/' in topic)
            payload = b'ON' if key == 'mute' else b'PRESS'
            integration.on_message(SimpleNamespace(topic=topic, payload=payload, retain=False))
            integration.backend.supported_features.side_effect = OSError('temporary detection failure')
            integration.config()
            with patch('pc2mqtt.integrations._shared.subprocess.Popen') as launch:
                integration.poll()
            integration.backend.execute.assert_not_called()
            launch.assert_not_called()

    def test_app_selects_platform_once_per_instance(self):
        for system in ('Linux', 'Windows'):
            with patch('pc2mqtt.mqtt.Client'), patch('pc2mqtt.platform.system', return_value=system), \
                 patch('pc2mqtt.integration_types', wraps=integration_types) as select:
                pc = PC2MQTT('broker')
                select.assert_called_once_with(system)
                self.assertTrue(all(f'.{system.lower()}.' in type(item).__module__ for item in pc.integrations))

    def test_loading_one_platform_does_not_import_the_other_or_native_libraries(self):
        for system, other in [('Linux', 'windows'), ('Windows', 'linux')]:
            script = (
                'import sys\n'
                'from pc2mqtt.integrations import integration_types\n'
                f'integration_types({system!r})\n'
                f'assert not any(name.startswith("pc2mqtt.integrations.{other}") for name in sys.modules)\n'
                'assert "pycaw" not in sys.modules\n'
                'assert "comtypes" not in sys.modules\n'
            )
            result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
