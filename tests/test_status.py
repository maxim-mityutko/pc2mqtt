import json
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import pytest

from pc2mqtt import PC2MQTT
from pc2mqtt.integrations.linux.status import Status
from pc2mqtt.integrations.windows.status import Status as WindowsStatus
from pc2mqtt.publishing import expiry_properties


class TestStatus:
    @pytest.fixture(autouse=True)
    def setup(self, make_integration):
        self.h = make_integration(Status, capabilities=set())
        self.h.client.socket.return_value.getsockname.return_value = ('192.168.1.10', 1234)
        self.sensor = self.h.integration
        self.sensor.config()

    def poll(self, now):
        self.h.clock.return_value = now
        self.sensor.poll()

    def test_discovery_and_offline_last_seen(self):
        configs = {
            json.loads(c.kwargs['payload'])['name']: json.loads(c.kwargs['payload'])
            for c in self.h.client.publish.call_args_list
            if c.kwargs['topic'].endswith('/config') and c.kwargs['payload']
        }
        assert configs['IP address']['availability_topic'] == 'connection'
        assert configs['Last seen']['device_class'] == 'timestamp'
        assert 'availability_topic' not in configs['Last seen']
        assert configs['Last seen']['unique_id'] != configs['IP address']['unique_id']

    def test_cadence_ip_changes_and_utc_timestamp(self):
        self.h.client.reset_mock()
        self.poll(0)
        seen, ip = [c.kwargs for c in self.h.client.publish.call_args_list]
        assert ip['payload'] == '192.168.1.10'
        assert datetime.fromisoformat(seen['payload']).tzinfo == timezone.utc
        for message in (seen, ip):
            assert message['retain']
            assert message['properties'].MessageExpiryInterval == 43200
        self.poll(59)
        assert self.h.client.publish.call_count == 2
        self.h.client.socket.return_value.getsockname.return_value = ('2001:db8::1', 1234)
        self.poll(60)
        assert self.h.client.publish.call_args.kwargs['payload'] == '2001:db8::1'

    def test_disconnect_and_reconnect(self):
        self.h.client.reset_mock()
        self.h.client.is_connected.return_value = False
        self.poll(0)
        self.h.client.publish.assert_not_called()
        self.h.client.is_connected.return_value = True
        self.poll(1)
        self.sensor.config()
        self.h.client.publish.reset_mock()
        self.poll(2)
        assert self.h.client.publish.call_count == 2

    def test_socket_failure_does_not_prevent_heartbeat(self):
        self.h.client.reset_mock()
        self.h.client.socket.return_value.getsockname.side_effect = OSError('closed')
        self.poll(0)
        assert self.h.client.publish.call_count == 1
        assert self.h.client.publish.call_args.kwargs['topic'].endswith('/last_seen/state')
        self.h.logger.warning.assert_called_once()
        self.h.client.socket.return_value.getsockname.side_effect = None
        self.poll(60)
        assert self.h.client.publish.call_count == 3

    def test_native_detection_failure_preserves_portable_sensors(self):
        self.h.backend.supported_features.side_effect = OSError('native API unavailable')
        self.sensor.config()
        self.h.client.reset_mock()
        self.poll(0)
        topics = {call.kwargs['topic'] for call in self.h.client.publish.call_args_list}
        assert topics == {f'{self.sensor.topics[key]}/state' for key in ('ip_address', 'last_seen')}

    def test_read_only_integration_allocates_no_command_queues(self):
        assert self.sensor.pending is None
        assert self.sensor.processes is None


class TestRetention:
    def test_expiry_serializes(self):
        properties = expiry_properties()
        assert properties.MessageExpiryInterval == 43200
        assert (43200).to_bytes(4, 'big') in properties.pack()

    def test_protocol_will_and_discovery_expiry(self, make_app):
        h = make_app()
        h.app.on_connect(h.client, None, None, 0, None)
        will = h.client.will_set.call_args.kwargs
        assert will['retain']
        assert will['properties'].MessageExpiryInterval == 43200
        for call in h.client.publish.call_args_list:
            assert call.kwargs['retain']
            assert call.kwargs['properties'].MessageExpiryInterval == 43200

    def test_default_client_uses_mqtt5(self, monkeypatch):
        from unittest.mock import Mock

        factory = Mock()
        monkeypatch.setattr('pc2mqtt.mqtt.Client', factory)
        PC2MQTT('broker', integration_factory=lambda system: ())
        factory.assert_called_once_with(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)

    @pytest.mark.parametrize('now,configs,messages', [(59, 0, 0), (60, 0, 1), (21600, 1, 1)])
    def test_discovery_and_availability_refresh(
        self, mqtt_client, monkeypatch, now, configs, messages
    ):
        from unittest.mock import Mock

        integration = Mock()
        app = PC2MQTT(
            'broker',
            client=mqtt_client,
            integration_factory=lambda system: [lambda *args: integration],
        )
        monkeypatch.setattr('pc2mqtt.time.monotonic', lambda: 0)
        app.on_connect(mqtt_client, None, None, 0, None)
        integration.reset_mock()
        mqtt_client.reset_mock()
        monkeypatch.setattr('pc2mqtt.time.monotonic', lambda: now)
        app.poll()
        assert integration.config.call_count == configs
        assert mqtt_client.publish.call_count == messages


class TestConnectionStatus:
    @pytest.mark.parametrize('status_type', [Status, WindowsStatus])
    @pytest.mark.parametrize('detection_fails', [False, True])
    def test_discovery_without_native_support(self, make_integration, status_type, detection_fails):
        h = make_integration(status_type, capabilities=set())
        if detection_fails:
            h.backend.supported_features.side_effect = OSError('native API unavailable')
        h.integration.config()
        topic = 'homeassistant/binary_sensor/pc/status/config'
        config = next(
            json.loads(call.kwargs['payload'])
            for call in h.client.publish.call_args_list
            if call.kwargs['topic'] == topic
        )
        assert config['name'] == 'Status'
        assert config['unique_id'] == 'computer_pc_status'
        assert config['device'] == h.integration.device
        assert config['device_class'] == 'connectivity'
        assert config['state_topic'] == 'connection'
        assert config['payload_on'] == 'online'
        assert config['payload_off'] == 'offline'
        # Gating availability would hide the off state when the computer disconnects.
        assert 'availability' not in config
        assert 'availability_topic' not in config
        assert 'command_topic' not in config
        h.client.reset_mock()
        h.integration.poll()
        h.backend.read.assert_not_called()
        assert all('/status/' not in call.kwargs['topic'] for call in h.client.publish.call_args_list)

    @pytest.mark.parametrize('system', ['Linux', 'Windows'])
    def test_connection_shutdown_and_will_match_discovered_state(self, make_app, system):
        h = make_app(system=system)
        for _ in range(2):
            h.client.publish.reset_mock()
            h.app.on_connect(h.client, None, None, 0)
            messages = {call.kwargs['topic']: call.kwargs for call in h.client.publish.call_args_list}
            config_message = messages['homeassistant/binary_sensor/desktop/status/config']
            config = json.loads(config_message['payload'])
            state = messages[config['state_topic']]
            assert config_message['retain']
            assert state['retain']
            assert state['payload'] == config['payload_on']
            will = h.client.will_set.call_args
            assert will.args[0] == config['state_topic']
            assert will.kwargs['payload'] == config['payload_off']
            assert will.kwargs['retain']
        h.client.publish.reset_mock()
        h.app.close()
        state = h.client.publish.call_args.kwargs
        assert state['topic'] == config['state_topic']
        assert state['payload'] == config['payload_off']
        assert state['retain']
        h.client.publish.return_value.wait_for_publish.assert_called_once_with(timeout=2)
        h.client.disconnect.assert_called_once()
