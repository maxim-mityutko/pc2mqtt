import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pc2mqtt.power import PowerControls, _power_command


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.logger = Mock()
        self.device = {"name": "Computer DESKTOP", "model": "Linux", "identifiers": ["desktop"]}
        self.power = PowerControls(self.client, "desktop", self.device, "pc2mqtt/desktop/availability", self.logger)
        self.process = Mock()
        self.process.poll.return_value = None
        patcher = patch("pc2mqtt.power.subprocess.Popen", return_value=self.process)
        self.launch = patcher.start()
        self.addCleanup(patcher.stop)

    def message(self, action, payload=None, retain=False):
        topic = f"{self.power.button_topics[action]}/set"
        if payload is None:
            payload = b"PRESS"
        return SimpleNamespace(topic=topic, payload=payload, retain=retain)

    def test_discovery_and_resubscription(self):
        for _ in range(2):
            self.client.reset_mock()
            self.power.config()
            publications = self.client.publish.call_args_list
            self.assertEqual(len(publications), 4)
            self.assertEqual(publications[0].kwargs, {
                "topic": "homeassistant/switch/desktop/config", "payload": "", "retain": True,
            })
            publications = publications[1:]
            messages = [json.loads(call.kwargs["payload"]) for call in publications]
            self.assertEqual(messages[0]["unique_id"], "computer_desktop_shutdown")
            self.assertEqual(messages[0]["command_topic"], "homeassistant/button/desktop/shutdown/set")
            self.assertEqual([m["name"] for m in messages], ["Shutdown", "Sleep", "Restart"])
            self.assertEqual(len({m["unique_id"] for m in messages}), 3)
            for action, call, message in zip(("shutdown", "sleep", "restart"), publications, messages):
                self.assertEqual(call.kwargs["topic"], f"homeassistant/button/desktop/{action}/config")
                self.assertEqual(message["command_topic"], f"homeassistant/button/desktop/{action}/set")
                self.assertNotIn("state_topic", message)
                self.assertTrue(call.kwargs["retain"])
                self.assertEqual(message["device"], self.device)
                self.assertEqual(message["availability_topic"], "pc2mqtt/desktop/availability")
                self.client.subscribe.assert_any_call(topic=message["command_topic"])
            for message in messages:
                self.assertEqual(message["payload_press"], "PRESS")
            self.assertEqual(self.client.subscribe.call_count, 3)

    def test_commands_are_queued_and_launched_once(self):
        for action in ("shutdown", "sleep", "restart"):
            with self.subTest(action=action):
                self.power = PowerControls(
                    self.client, "desktop", self.device, "pc2mqtt/desktop/availability", self.logger,
                )
                self.launch.reset_mock()
                self.client.reset_mock()
                self.assertTrue(self.power.on_message(self.message(action)))
                self.launch.assert_not_called()
                self.power.poll()
                self.launch.assert_called_once_with(_power_command("Linux", action))
                self.client.publish.assert_not_called()
                self.power.on_message(self.message(action))
                self.power.poll()
                self.launch.assert_called_once()

    def test_wrong_topics_payloads_and_retained_commands_do_not_execute(self):
        self.assertFalse(self.power.on_message(SimpleNamespace(topic="other", payload=b"OFF", retain=False)))
        self.assertFalse(self.power.on_message(SimpleNamespace(
            topic="homeassistant/switch/desktop/set", payload=b"OFF", retain=False,
        )))
        for action in ("shutdown", "sleep", "restart"):
            for message in (self.message(action, retain=True), self.message(action, payload=b"invalid"), self.message(action, payload=b"OFF"),
                            self.message(action, payload=b"\xff")):
                self.assertTrue(self.power.on_message(message))
        self.power.poll()
        self.launch.assert_not_called()

    def test_pending_commands_are_not_duplicated(self):
        for _ in range(3):
            self.power.on_message(self.message("restart"))
        self.power.poll()
        self.launch.assert_called_once()
        self.process.poll.return_value = 0
        self.power.poll()
        self.power.poll()
        self.launch.assert_called_once()
        self.client.publish.assert_not_called()

    def test_sleep_completion_allows_another_command(self):
        self.power.on_message(self.message("sleep"))
        self.power.poll()
        self.process.poll.return_value = 0
        self.power.poll()
        self.power.on_message(self.message("restart"))
        self.power.poll()
        self.assertEqual(self.launch.call_count, 2)

    def test_nonzero_exit_and_launch_failure_are_logged(self):
        self.power.on_message(self.message("restart"))
        self.power.poll()
        self.process.poll.return_value = 1
        self.power.poll()
        self.logger.error.assert_called_once()
        self.launch.side_effect = FileNotFoundError("command missing")
        self.power.on_message(self.message("sleep"))
        self.power.poll()
        self.assertEqual(self.logger.error.call_count, 2)

    def test_platform_commands(self):
        for action, linux, windows in [
            ("shutdown", ["shutdown", "now"], ["shutdown", "/s", "/t", "0"]),
            ("restart", ["shutdown", "-r", "now"], ["shutdown", "/r", "/t", "0"]),
            ("sleep", ["systemctl", "suspend"], None),
        ]:
            self.assertEqual(_power_command("Linux", action), linux)
            if windows:
                self.assertEqual(_power_command("Windows", action), windows)
        sleep = _power_command("Windows", "sleep")
        self.assertEqual(sleep[:4], ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"])
        self.assertIn("PowerState]::Suspend, $false, $false", sleep[-1])
        self.assertIn("exit 1", sleep[-1])
        with self.assertRaises(NotImplementedError):
            _power_command("Darwin", "sleep")


if __name__ == "__main__":
    unittest.main()
