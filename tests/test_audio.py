import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from pc2mqtt import PC2MQTT
from pc2mqtt.audio import is_audio_playing


class AudioDetectionTests(unittest.TestCase):
    @patch("pc2mqtt.audio.subprocess.run")
    def test_linux_output_states(self, run):
        for output, expected in [
            ("", False),
            ("0\tspeaker\tdriver\ts16le 2ch 48000Hz\tIDLE\n", False),
            ("0\tspeaker\tdriver\ts16le 2ch 48000Hz\tSUSPENDED\n", False),
            ("0\tspeaker\tdriver\ts16le 2ch 48000Hz\tIDLE\n"
             "1\theadphones\tdriver\ts16le 2ch 48000Hz\tRUNNING\n", True),
        ]:
            with self.subTest(output=output):
                run.return_value.stdout = output
                self.assertEqual(is_audio_playing("Linux"), expected)
        self.assertEqual(run.call_args.args[0], ["pactl", "list", "short", "sinks"])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    @patch("pc2mqtt.audio.subprocess.run")
    def test_linux_errors_propagate(self, run):
        for error in [FileNotFoundError(), subprocess.TimeoutExpired("pactl", 5),
                      subprocess.CalledProcessError(1, "pactl")]:
            with self.subTest(error=error):
                run.side_effect = error
                with self.assertRaises(type(error)):
                    is_audio_playing("Linux")

    def test_unsupported_platform(self):
        with self.assertRaises(NotImplementedError):
            is_audio_playing("Darwin")

    def test_windows_outputs_and_com_cleanup(self):
        com = Mock()
        utilities = Mock()
        devices = utilities.GetDeviceEnumerator.return_value.EnumAudioEndpoints.return_value
        modules = {
            "comtypes": com,
            "pycaw": Mock(),
            "pycaw.api": Mock(),
            "pycaw.api.audiopolicy": Mock(),
            "pycaw.constants": SimpleNamespace(
                DEVICE_STATE=SimpleNamespace(ACTIVE=SimpleNamespace(value=1)),
                EDataFlow=SimpleNamespace(eRender=SimpleNamespace(value=0)),
            ),
            "pycaw.pycaw": SimpleNamespace(AudioUtilities=utilities),
        }
        with patch.dict(sys.modules, modules):
            for states, expected in [([], False), ([0, 2], False), ([0, 1], True)]:
                with self.subTest(states=states):
                    outputs = []
                    for state in states:
                        output = Mock()
                        sessions = output.Activate.return_value.QueryInterface.return_value.GetSessionEnumerator.return_value
                        sessions.GetCount.return_value = 1
                        sessions.GetSession.return_value.GetState.return_value = state
                        outputs.append(output)
                    devices.GetCount.return_value = len(outputs)
                    devices.Item.side_effect = outputs
                    self.assertEqual(is_audio_playing("Windows"), expected)
            devices.GetCount.side_effect = RuntimeError("audio service unavailable")
            with self.assertRaises(RuntimeError):
                is_audio_playing("Windows")
        self.assertEqual(com.CoInitialize.call_count, 4)
        self.assertEqual(com.CoUninitialize.call_count, 4)
        utilities.GetDeviceEnumerator.return_value.EnumAudioEndpoints.assert_called_with(0, 1)


class MQTTTests(unittest.TestCase):
    def setUp(self):
        with patch("pc2mqtt.mqtt.Client"), patch("pc2mqtt.platform.node", return_value="desktop"):
            self.pc = PC2MQTT("broker")
        self.pc.logger = Mock()
        self.audio = self.pc.integrations[0]
        self.audio.logger = Mock()

    def publications(self):
        return [call.kwargs for call in self.pc.client.publish.call_args_list]

    def test_discovery_on_connect_and_reconnect(self):
        for _ in range(2):
            self.pc.client.reset_mock()
            self.pc.on_connect(self.pc.client, None, None, 0)
            audio, legacy, shutdown, sleep, restart, ip, last_seen, connection = self.publications()
            self.assertEqual(connection["topic"], "pc2mqtt/desktop/availability")
            self.assertEqual(connection["payload"], "online")
            sensor = json.loads(audio["payload"])
            self.assertEqual(audio["topic"], "homeassistant/binary_sensor/desktop/audio_playing/config")
            self.assertTrue(audio["retain"])
            self.assertEqual(sensor["device"], json.loads(shutdown["payload"])["device"])
            self.assertNotEqual(sensor["unique_id"], json.loads(shutdown["payload"])["unique_id"])
            self.assertEqual(sensor["payload_on"], "ON")
            self.assertEqual(sensor["payload_off"], "OFF")
            self.assertEqual(sensor["expire_after"], 90)
            self.assertEqual(sensor["availability_mode"], "all")
            self.assertEqual(sensor["availability"], [
                {"topic": "pc2mqtt/desktop/availability"},
                {"topic": "homeassistant/binary_sensor/desktop/audio_playing/availability"},
            ])
            self.assertEqual(self.pc.client.subscribe.call_count, 3)

    def test_failed_connection_does_not_announce(self):
        self.pc.on_connect(self.pc.client, None, None, 5)
        self.pc.client.publish.assert_not_called()

    def sample_audio(self, now, playing):
        self.pc.client.reset_mock()
        with patch("pc2mqtt.audio.time.monotonic", return_value=now), patch(
            "pc2mqtt.audio.is_audio_playing", return_value=playing,
            side_effect=playing if isinstance(playing, Exception) else None,
        ):
            self.audio.poll()
        return self.publications()

    def test_idle_updates_once_per_minute(self):
        self.assertEqual(self.sample_audio(0, False)[0]["payload"], "OFF")
        self.assertEqual(self.sample_audio(1, False), [])
        self.assertEqual(self.sample_audio(59, False), [])
        self.assertEqual(self.sample_audio(60, False)[0]["payload"], "OFF")
        self.assertEqual(self.sample_audio(119, False), [])
        self.assertEqual(self.sample_audio(120, False)[0]["payload"], "OFF")

    def test_sustained_playback_publishes_early_once(self):
        self.sample_audio(0, False)
        self.assertEqual(self.sample_audio(10, True), [])
        self.assertEqual(self.sample_audio(11.99, True), [])
        self.assertEqual(self.sample_audio(12, True)[0]["payload"], "ON")
        self.assertEqual(self.sample_audio(14, True), [])
        self.assertEqual(self.sample_audio(59, True), [])
        self.assertEqual(self.sample_audio(60, True)[0]["payload"], "ON")
        self.assertEqual(self.sample_audio(61, False), [])
        self.assertEqual(self.sample_audio(119, False), [])
        self.assertEqual(self.sample_audio(120, False)[0]["payload"], "OFF")
        self.assertEqual(self.sample_audio(121, True), [])
        self.assertEqual(self.sample_audio(123, True)[0]["payload"], "ON")

    def test_short_bursts_reset_active_timer(self):
        self.sample_audio(0, False)
        for now, playing in [(10, True), (11, False), (12, True),
                             (13, False), (59, True)]:
            self.assertEqual(self.sample_audio(now, playing), [])
        self.assertEqual(self.sample_audio(60, True)[0]["payload"], "OFF")
        self.assertEqual(self.sample_audio(61, True)[0]["payload"], "ON")

    def test_active_at_startup_waits_two_seconds(self):
        self.assertEqual(self.sample_audio(0, True)[0]["payload"], "OFF")
        self.assertEqual(self.sample_audio(1, True), [])
        self.assertEqual(self.sample_audio(2, True)[0]["payload"], "ON")

    def test_detection_failure_and_recovery(self):
        self.sample_audio(0, False)
        self.sample_audio(10, True)
        failed = self.sample_audio(11, RuntimeError("backend unavailable"))
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["payload"], "offline")
        self.assertEqual(self.sample_audio(12, RuntimeError("backend unavailable")), [])
        recovered = self.sample_audio(13, True)
        self.assertEqual(recovered[0]["payload"], "OFF")
        self.assertEqual(recovered[1]["payload"], "online")
        self.assertEqual(self.sample_audio(14, True), [])
        self.assertEqual(self.sample_audio(15, True)[0]["payload"], "ON")

    def test_reconnect_forces_fresh_state(self):
        self.sample_audio(0, False)
        self.pc.on_connect(self.pc.client, None, None, 0)
        self.assertEqual(self.sample_audio(10, False)[0]["payload"], "OFF")

    def test_last_will(self):
        self.pc.client.will_set.assert_called_once_with(
            "pc2mqtt/desktop/availability",
            payload="offline", retain=True, properties=ANY,
        )

    @patch("pc2mqtt.time.sleep", side_effect=[None, None, KeyboardInterrupt])
    def test_polling_checks_audio_every_second(self, sleep):
        with patch.object(self.audio, "poll") as audio, patch.object(self.pc.integrations[2], "poll"):
            with self.assertRaises(KeyboardInterrupt):
                self.pc.state()
        self.assertEqual(audio.call_count, 3)
        self.assertEqual(self.publications(), [])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 1, 1])


class IntegrationRegistrationTests(unittest.TestCase):
    def test_registered_integrations_receive_context_and_lifecycle_calls(self):
        factories = [Mock(), Mock()]
        with patch("pc2mqtt.INTEGRATION_TYPES", factories), patch("pc2mqtt.mqtt.Client"):
            pc = PC2MQTT("broker")
        for factory in factories:
            factory.assert_called_once_with(
                pc.client, pc.device["identifiers"][0], pc.device, pc.availability_topic, pc.logger,
            )
        for _ in range(2):
            pc.on_connect(pc.client, None, None, 0)
        with patch("pc2mqtt.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                pc.state()
        for factory in factories:
            self.assertEqual(factory.return_value.config.call_count, 2)
            factory.return_value.poll.assert_called_once_with()

    def test_empty_registry_only_announces_connection(self):
        with patch("pc2mqtt.INTEGRATION_TYPES", ()), patch("pc2mqtt.mqtt.Client"):
            pc = PC2MQTT("broker")
        pc.on_connect(pc.client, None, None, 0)
        with patch("pc2mqtt.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                pc.state()
        pc.client.publish.assert_called_once_with(
            topic=pc.availability_topic, payload="online", retain=True, properties=ANY,
        )

    def test_command_dispatch_skips_read_only_integrations(self):
        with patch("pc2mqtt.mqtt.Client"):
            pc = PC2MQTT("broker")
        message = SimpleNamespace(topic="unknown", payload=b"OFF", retain=False)
        with patch.object(pc.integrations[1], "on_message", return_value=True) as handler:
            pc.on_message(pc.client, None, message)
        handler.assert_called_once_with(message)


if __name__ == "__main__":
    unittest.main()
