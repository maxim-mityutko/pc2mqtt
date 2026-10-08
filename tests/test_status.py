from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt

from pc2mqtt import PC2MQTT
from pc2mqtt.integrations._shared import HeartbeatSensors
from pc2mqtt.publishing import expiry_properties


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.socket.return_value.getsockname.return_value = ("192.168.1.10", 1234)
        self.sensor = HeartbeatSensors(
            self.client, "desktop", {"name": "Computer DESKTOP"}, "connection", Mock(),
        )

    def poll(self, now):
        with patch("pc2mqtt.integrations._shared.time.monotonic", return_value=now):
            self.sensor.poll()

    def test_discovery_and_offline_last_seen(self):
        self.sensor.config()
        ip, seen = [json.loads(c.kwargs['payload']) for c in self.client.publish.call_args_list]
        self.assertEqual(ip['availability_topic'], 'connection')
        self.assertEqual(seen['device_class'], 'timestamp')
        self.assertNotIn('availability_topic', seen)
        self.assertNotEqual(ip['unique_id'], seen['unique_id'])

    def test_cadence_ip_changes_and_utc_timestamp(self):
        self.poll(0)
        seen, ip = [c.kwargs for c in self.client.publish.call_args_list]
        self.assertEqual(ip['payload'], '192.168.1.10')
        self.assertEqual(datetime.fromisoformat(seen['payload']).tzinfo, timezone.utc)
        for message in (seen, ip):
            self.assertTrue(message['retain'])
            self.assertEqual(message['properties'].MessageExpiryInterval, 43200)
        self.poll(59)
        self.assertEqual(self.client.publish.call_count, 2)
        self.client.socket.return_value.getsockname.return_value = ('2001:db8::1', 1234, 0, 0)
        self.poll(60)
        self.assertEqual(self.client.publish.call_args.kwargs['payload'], '2001:db8::1')

    def test_disconnect_and_reconnect(self):
        self.client.is_connected.return_value = False
        self.poll(0)
        self.client.publish.assert_not_called()
        self.client.is_connected.return_value = True
        self.poll(1)
        self.sensor.config()
        self.client.publish.reset_mock()
        self.poll(2)
        self.assertEqual(self.client.publish.call_count, 2)

    def test_socket_failure_does_not_prevent_heartbeat(self):
        self.client.socket.return_value.getsockname.side_effect = OSError('closed')
        self.poll(0)
        self.assertEqual(self.client.publish.call_count, 1)
        self.assertTrue(self.client.publish.call_args.kwargs['topic'].endswith('/last_seen/state'))
        self.sensor.logger.warning.assert_called_once()
        self.client.socket.return_value.getsockname.side_effect = None
        self.poll(60)
        self.assertEqual(self.client.publish.call_count, 3)


class RetentionTests(unittest.TestCase):
    def test_expiry_serializes(self):
        properties = expiry_properties()
        self.assertEqual(properties.MessageExpiryInterval, 43200)
        self.assertIn((43200).to_bytes(4, 'big'), properties.pack())

    def test_protocol_will_and_all_discovery_expiry(self):
        with patch('pc2mqtt.mqtt.Client') as factory:
            pc = PC2MQTT('broker')
        factory.assert_called_once_with(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
        will = pc.client.will_set.call_args.kwargs
        self.assertTrue(will['retain'])
        self.assertEqual(will['properties'].MessageExpiryInterval, 43200)
        pc.on_connect(pc.client, None, None, 0, None)
        for call in pc.client.publish.call_args_list:
            self.assertTrue(call.kwargs['retain'])
            self.assertEqual(call.kwargs['properties'].MessageExpiryInterval, 43200)

    def test_discovery_and_availability_refresh(self):
        with patch('pc2mqtt.mqtt.Client'), patch('pc2mqtt.integration_types', return_value=()):
            pc = PC2MQTT('broker')
        with patch('pc2mqtt.time.monotonic', return_value=0):
            pc.on_connect(pc.client, None, None, 0, None)
        for now, expected_configs, expected_messages in [(59, 0, 0), (60, 0, 1), (21600, 1, 1)]:
            pc.client.publish.reset_mock()
            with patch.object(pc, 'config', wraps=pc.config) as config, patch(
                'pc2mqtt.time.monotonic', return_value=now,
            ), patch('pc2mqtt.time.sleep', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    pc.state()
            self.assertEqual(config.call_count, expected_configs)
            self.assertEqual(pc.client.publish.call_count, expected_messages)
