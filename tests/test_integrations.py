"""Platform registration, domain ownership, and MQTT lifecycle contracts."""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pc2mqtt import PC2MQTT
from pc2mqtt.integrations import integration_types
from pc2mqtt.integrations._entities import Entity
from pc2mqtt.integrations._shared import EntityIntegration

OWNERS = {
    'audio': {'audio_playing', 'volume', 'mute'},
    'power': {'shutdown', 'sleep', 'restart', 'displays_off'},
    'status': {'ip_address', 'last_seen', 'uptime'},
    'user': {'session_locked', 'idle_time', 'lock_session'},
}
PLATFORMS = ('Linux', 'Windows')


def publications(client):
    return {call.kwargs['topic']: call.kwargs['payload'] for call in client.publish.call_args_list}


def subscriptions(client):
    return {call.kwargs['topic'] for call in client.subscribe.call_args_list}


@pytest.mark.parametrize('system', PLATFORMS)
class TestIntegration:
    def test_identical_exclusive_entity_ownership(self, system, make_integration):
        seen = set()
        for cls in integration_types(system):
            domain = cls.__name__.lower()
            assert set(cls.entities) == OWNERS[domain]
            h = make_integration(cls)
            h.integration.config()
            configs = [
                json.loads(value)
                for topic, value in publications(h.client).items()
                if topic.endswith('/config') and value
            ]
            keys = {cfg['unique_id'].removeprefix('computer_pc_') for cfg in configs}
            assert keys == OWNERS[domain]
            assert not seen & keys
            seen.update(keys)
            for cfg in configs:
                key = cfg['unique_id'].removeprefix('computer_pc_')
                for name in ('command_topic', 'state_topic'):
                    if name in cfg:
                        assert f'/pc/{key}/' in cfg[name]
        assert len(seen) == 13

    @pytest.mark.parametrize('domain', OWNERS)
    def test_one_capability_snapshot_per_refresh(self, system, domain, make_integration):
        cls = next(cls for cls in integration_types(system) if cls.__name__.lower() == domain)
        h = make_integration(cls)
        for count in (1, 2):
            h.integration.config()
            assert h.backend.supported_features.call_count == count

    def test_no_native_capabilities_leaves_only_portable_heartbeat(self, system, make_integration):
        for cls in integration_types(system):
            h = make_integration(cls, capabilities=set())
            h.integration.config()
            configs = [
                json.loads(value)
                for topic, value in publications(h.client).items()
                if topic.endswith('/config') and value
            ]
            assert {cfg['name'] for cfg in configs} == (
                {'IP address', 'Last seen'} if cls.__name__ == 'Status' else set()
            )
            h.client.subscribe.assert_not_called()
            h.integration.poll()
            h.backend.read.assert_not_called()
            h.backend.check.assert_not_called()

    @pytest.mark.parametrize(
        'domain,missing',
        [
            (domain, key)
            for domain, keys in OWNERS.items()
            for key in sorted(keys)
            if key not in {'ip_address', 'last_seen'}
        ],
    )
    def test_partial_capabilities_cleanup_and_recovery(
        self, system, domain, missing, make_integration
    ):
        cls = next(cls for cls in integration_types(system) if cls.__name__.lower() == domain)
        h = make_integration(cls)
        integration, client = h.integration, h.client
        integration.config()
        topic = integration.topics[missing]
        cfg = json.loads(publications(client)[f'{topic}/config'])
        client.reset_mock()
        h.backend.supported_features.return_value = OWNERS[domain] - {missing}
        integration.config()
        messages = publications(client)
        for suffix in ('config', 'state', 'availability'):
            assert messages[f'{topic}/{suffix}'] == ''
        if 'command_topic' in cfg:
            assert cfg['command_topic'] not in subscriptions(client)
            client.unsubscribe.assert_any_call(cfg['command_topic'])
            integration.on_message(
                SimpleNamespace(topic=cfg['command_topic'], payload=b'PRESS', retain=False)
            )
            integration.poll()
            h.backend.execute.assert_not_called()
            h.backend.start.assert_not_called()
        client.reset_mock()
        h.backend.supported_features.return_value = OWNERS[domain]
        integration.config()
        assert (
            json.loads(publications(client)[f'{topic}/config'])['unique_id']
            == f'computer_pc_{missing}'
        )
        if 'command_topic' in cfg:
            assert cfg['command_topic'] in subscriptions(client)

    @pytest.mark.parametrize(
        'domain,key,payload',
        [
            ('power', 'shutdown', b'PRESS'),
            ('user', 'lock_session', b'PRESS'),
            ('audio', 'mute', b'ON'),
        ],
    )
    def test_reconnect_discards_commands_even_if_detection_fails(
        self, system, domain, key, payload, make_integration
    ):
        cls = next(cls for cls in integration_types(system) if cls.__name__.lower() == domain)
        h = make_integration(cls)
        h.integration.config()
        h.integration.on_message(
            SimpleNamespace(topic=f'{h.integration.topics[key]}/set', payload=payload, retain=False)
        )
        h.backend.supported_features.side_effect = OSError('temporary detection failure')
        h.integration.config()
        h.integration.poll()
        h.backend.execute.assert_not_called()
        h.backend.start.assert_not_called()

    def test_app_selects_platform_once(self, system, mqtt_client, monkeypatch):
        select = Mock(wraps=integration_types)
        monkeypatch.setattr('pc2mqtt.platform.system', lambda: system)
        pc = PC2MQTT('broker', client=mqtt_client, integration_factory=select)
        select.assert_called_once_with(system)
        assert all(f'.{system.lower()}.' in type(item).__module__ for item in pc.integrations)

    def test_platform_import_isolation(self, system):
        other = 'windows' if system == 'Linux' else 'linux'
        script = (
            'import sys\nfrom pc2mqtt.integrations import integration_types\n'
            f'integration_types({system!r})\n'
            f'assert not any(name.startswith("pc2mqtt.integrations.{other}") for name in sys.modules)\n'
            'assert "pycaw" not in sys.modules\nassert "comtypes" not in sys.modules\n'
        )
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


class TestApplication:
    def test_discovery_on_connect_and_reconnect(self, make_app):
        h = make_app()
        for _ in range(2):
            h.client.reset_mock()
            h.app.on_connect(h.client, None, None, 0)
            messages = publications(h.client)
            audio = json.loads(messages['homeassistant/binary_sensor/desktop/audio_playing/config'])
            shutdown = json.loads(messages['homeassistant/button/desktop/shutdown/config'])
            assert audio['device'] == shutdown['device']
            assert audio['unique_id'] != shutdown['unique_id']
            assert audio['expire_after'] == 90
            assert audio['payload_on'] == 'ON'
            assert audio['payload_off'] == 'OFF'
            assert audio['availability_mode'] == 'all'
            assert audio['availability'] == [
                {'topic': 'pc2mqtt/desktop/availability'},
                {'topic': 'homeassistant/binary_sensor/desktop/audio_playing/availability'},
            ]
            assert messages['pc2mqtt/desktop/availability'] == 'online'
            assert h.client.subscribe.call_count == 7

    def test_failed_connection_does_not_announce(self, make_app):
        h = make_app()
        h.app.on_connect(h.client, None, None, 5)
        h.client.publish.assert_not_called()

    def test_factories_receive_context_and_lifecycle_calls(self, mqtt_client):
        factories = [Mock(), Mock()]
        app = PC2MQTT('broker', client=mqtt_client, integration_factory=lambda system: factories)
        for factory in factories:
            factory.assert_called_once_with(
                mqtt_client,
                app.device['identifiers'][0],
                app.device,
                app.availability_topic,
                app.logger,
            )
        for _ in range(2):
            app.on_connect(mqtt_client, None, None, 0)
        app.poll()
        for factory in factories:
            assert factory.return_value.config.call_count == 2
            factory.return_value.poll.assert_called_once_with()

    def test_empty_registry_only_announces_connection(self, mqtt_client):
        app = PC2MQTT('broker', client=mqtt_client, integration_factory=lambda system: ())
        app.on_connect(mqtt_client, None, None, 0)
        app.poll()
        assert publications(mqtt_client) == {app.availability_topic: 'online'}
        assert mqtt_client.publish.call_count == 1

    def test_dispatches_to_owning_domain(self, make_app):
        h = make_app()
        h.app.config()
        h.app.on_message(
            h.client,
            None,
            SimpleNamespace(
                topic='homeassistant/switch/desktop/mute/set',
                payload=b'ON',
                retain=False,
            ),
        )
        h.app.poll()
        h.backends['audio'].execute.assert_called_once_with('mute', True)
        h.backends['power'].execute.assert_not_called()
        h.backends['user'].execute.assert_not_called()

    def test_polling_checks_playback_each_second(self, make_app, monkeypatch):
        h = make_app()
        h.app.config()
        h.clock.side_effect = [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]
        sleep = Mock(side_effect=[None, None, KeyboardInterrupt])
        monkeypatch.setattr('pc2mqtt.time.sleep', sleep)
        with pytest.raises(KeyboardInterrupt):
            h.app.state()
        assert [call.args for call in sleep.call_args_list] == [(1,), (1,), (1,)]
        assert (
            sum(call.args == ('audio_playing',) for call in h.backends['audio'].read.call_args_list)
            == 3
        )

    def test_new_process_action_requires_only_catalog_and_backend(self, make_integration):
        class Example(EntityIntegration):
            entities = {
                'hibernate': Entity(
                    'button', 'Hibernate', behavior='process', availability='connection'
                )
            }

        h = make_integration(Example)
        h.backend.start.return_value.poll.return_value = None
        h.integration.config()
        h.integration.on_message(
            SimpleNamespace(
                topic=f'{h.integration.topics["hibernate"]}/set', payload=b'PRESS', retain=False
            )
        )
        h.integration.poll()
        h.backend.start.assert_called_once_with('hibernate')
