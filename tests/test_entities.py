import json
from types import SimpleNamespace

import pytest

from pc2mqtt.integrations._shared import EntityIntegration
from pc2mqtt.integrations.linux.audio import Audio
from pc2mqtt.integrations.linux.power import Power
from pc2mqtt.integrations.linux.status import Status
from pc2mqtt.integrations.linux.user import User

ENTITIES = {
    key: entity
    for cls in (Audio, Power, Status, User)
    for key, entity in cls.entities.items()
    if entity.behavior == 'sample' and entity.reader is None
}


class ExampleIntegration(EntityIntegration):
    entities = ENTITIES


class TestEntity:
    @pytest.fixture(autouse=True)
    def setup(self, make_integration):
        self.values = {
            'session_locked': False,
            'idle_time': 12,
            'uptime': 500,
            'volume': 40,
            'mute': False,
        }
        self.h = make_integration(ExampleIntegration, values=self.values)
        self.values = self.h.values
        self.client = self.h.client
        self.integration = self.h.integration
        self.integration.config()
        self.client.reset_mock()

    def poll(self, now):
        self.h.clock.return_value = now
        self.integration.poll()

    def messages(self):
        return {c.kwargs['topic']: c.kwargs['payload'] for c in self.client.publish.call_args_list}

    def message(self, key, payload, retain=False):
        return SimpleNamespace(
            topic=f'{self.integration.topics[key]}/set', payload=payload, retain=retain
        )

    def test_unsupported_features_removed_and_never_polled_or_executed(self):
        self.integration.backend.supported_features.return_value = {'uptime'}
        self.integration.config()
        messages = self.messages()
        assert messages[f'{self.integration.topics["uptime"]}/config']
        for key in ENTITIES.keys() - {'uptime'}:
            for suffix in ('config', 'state', 'availability'):
                assert messages[f'{self.integration.topics[key]}/{suffix}'] == ''
        self.client.subscribe.assert_not_called()
        self.client.unsubscribe.assert_any_call(f'{self.integration.topics["volume"]}/set')
        self.integration.on_message(self.message('lock_session', b'PRESS'))
        self.client.reset_mock()
        self.integration.backend.read.reset_mock()
        self.poll(0)
        self.integration.backend.execute.assert_not_called()
        self.integration.backend.read.assert_called_once_with('uptime')
        assert all(('/uptime/' in topic for topic in self.messages()))

    def test_unsupported_logs_once_per_reason_and_again_after_support_changes(self):
        self.integration.backend.supported_features.return_value = set(ENTITIES) - {'displays_off'}
        self.integration.backend.unsupported_reason.return_value = 'requires X11 or Sway'
        self.integration.config()
        self.integration.config()
        self.poll(0)
        self.integration.logger.info.assert_called_once_with(
            '%s disabled: %s', 'Turn off displays', 'requires X11 or Sway'
        )
        self.integration.backend.supported_features.return_value = set(ENTITIES)
        self.integration.config()
        self.integration.backend.supported_features.return_value = set(ENTITIES) - {'displays_off'}
        self.integration.config()
        assert self.integration.logger.info.call_count == 2

    def test_new_capabilities_appear_on_next_discovery(self):
        self.integration.backend.supported_features.return_value = {'uptime'}
        self.integration.config()
        self.client.reset_mock()
        self.integration.backend.supported_features.return_value = {'uptime', 'volume'}
        self.integration.config()
        assert self.messages()[f'{self.integration.topics["volume"]}/config']
        self.client.subscribe.assert_called_once_with(
            topic=f'{self.integration.topics["volume"]}/set'
        )

    def test_detection_failure_preserves_existing_discovery(self):
        self.integration.backend.supported_features.side_effect = OSError('temporary error')
        self.integration.config()
        assert all(call.kwargs['payload'] for call in self.client.publish.call_args_list)
        assert self.integration.supported == set(ENTITIES)

    def test_discovery_and_commands(self):
        self.integration.config()
        messages = self.messages()
        configs = {
            key: json.loads(messages[f'{topic}/config'])
            for key, topic in self.integration.topics.items()
        }
        assert len(configs) == 7
        assert len({cfg['unique_id'] for cfg in configs.values()}) == 7
        assert configs['volume']['max'] == 100
        assert configs['mute']['payload_on'] == 'ON'
        for key in ('idle_time', 'uptime'):
            assert configs[key]['unit_of_measurement'] == 'h'
            assert configs[key]['value_template'] == '{{ value | float / 3600 }}'
            assert configs[key]['suggested_display_precision'] == 2
        assert 'state_topic' not in configs['lock_session']
        assert self.client.subscribe.call_count == 4
        for key, cfg in configs.items():
            assert cfg['availability_mode'] == 'all'
            assert cfg['availability'][0] == {'topic': 'connection'}
            assert cfg['device']['name'] == 'Computer PC'
            assert messages[f'{self.integration.topics[key]}/availability'] == 'offline'
        for c in self.client.publish.call_args_list:
            assert c.kwargs['retain']
            assert c.kwargs['properties'].MessageExpiryInterval == 43200

    def test_cadence_changes_and_heartbeat(self):
        self.poll(0)
        assert self.messages()[f'{self.integration.topics["volume"]}/state'] == '40'
        self.client.reset_mock()
        self.poll(9)
        self.client.publish.assert_not_called()
        self.poll(10)
        self.client.publish.assert_not_called()
        self.values['session_locked'] = True
        self.poll(20)
        assert self.messages()[f'{self.integration.topics["session_locked"]}/state'] == 'ON'
        self.client.reset_mock()
        self.poll(60)
        assert f'{self.integration.topics["volume"]}/state' in self.messages()

    def test_invalid_retained_and_unknown_commands_do_not_execute(self):
        for key, payload in [
            ('volume', b'nan'),
            ('volume', b'inf'),
            ('volume', b'-1'),
            ('volume', b'101'),
            ('volume', b'50; touch bad'),
            ('mute', b'toggle'),
            ('mute', b'\xff'),
            ('lock_session', b'OFF'),
            ('displays_off', b'OFF'),
        ]:
            assert self.integration.on_message(self.message(key, payload))
        for key, payload in [
            ('volume', b'50'),
            ('mute', b'ON'),
            ('lock_session', b'PRESS'),
            ('displays_off', b'PRESS'),
        ]:
            self.integration.on_message(self.message(key, payload, retain=True))
        assert not self.integration.on_message(SimpleNamespace(topic='unknown'))
        self.poll(0)
        self.integration.backend.execute.assert_not_called()

    def test_each_control_is_queued_and_read_back(self):
        for key, payload, expected in [
            ('volume', b'23.5', 23.5),
            ('mute', b'ON', True),
            ('mute', b'OFF', False),
            ('lock_session', b'PRESS', 'PRESS'),
            ('displays_off', b'PRESS', 'PRESS'),
        ]:
            self.integration.backend.execute.reset_mock()
            self.integration.on_message(self.message(key, payload))
            self.integration.backend.execute.assert_not_called()
            self.poll(1)
            self.integration.backend.execute.assert_called_once_with(key, expected)
            assert f'{self.integration.topics["volume"]}/state' in self.messages()

    def test_volume_and_mute_publish_actual_readback(self):
        self.poll(0)
        self.client.reset_mock()

        def execute(key, value):
            self.values[key] = value

        self.integration.backend.execute.side_effect = execute
        self.integration.on_message(self.message('volume', b'25'))
        self.poll(1)
        assert self.messages()[f'{self.integration.topics["volume"]}/state'] == '25.0'
        self.integration.on_message(self.message('mute', b'ON'))
        self.poll(2)
        assert self.messages()[f'{self.integration.topics["mute"]}/state'] == 'ON'

    def test_backend_failure_is_isolated_and_recovers(self):

        def read(key):
            if key == 'idle_time':
                raise NotImplementedError('no idle monitor')
            return self.values[key]

        self.integration.backend.read.side_effect = read
        self.poll(0)
        assert self.messages()[f'{self.integration.topics["idle_time"]}/availability'] == 'offline'
        assert self.messages()[f'{self.integration.topics["uptime"]}/state'] == '500'
        self.poll(10)
        self.integration.logger.warning.assert_called_once()
        self.integration.backend.read.side_effect = lambda key: self.values[key]
        self.poll(20)
        assert self.messages()[f'{self.integration.topics["idle_time"]}/availability'] == 'online'

    def test_action_failure_not_reported_as_success(self):
        self.integration.backend.execute.side_effect = OSError('denied')
        self.integration.on_message(self.message('lock_session', b'PRESS'))
        self.poll(0)
        assert (
            self.messages()[f'{self.integration.topics["lock_session"]}/availability'] == 'offline'
        )
        assert f'{self.integration.topics["volume"]}/state' in self.messages()

    def test_disconnect_and_reconnect_clear_stale_commands(self):
        self.integration.on_message(self.message('lock_session', b'PRESS'))
        self.client.is_connected.return_value = False
        self.poll(0)
        self.integration.backend.execute.assert_not_called()
        self.integration.backend.read.assert_not_called()
        self.integration.config()
        self.client.is_connected.return_value = True
        self.poll(1)
        self.integration.backend.execute.assert_not_called()
