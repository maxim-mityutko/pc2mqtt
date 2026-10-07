"""Power controls.

`pc2mqtt/power.py` owns three buttons: **Shutdown**, **Sleep**, and **Restart**.
All are discovered on the same Home Assistant device and share the same topic
structure and command payload.

| Action | MQTT command topic | Payload |
| --- | --- | --- |
| Shutdown | `homeassistant/button/<node>/shutdown/set` | `PRESS` |
| Sleep | `homeassistant/button/<node>/sleep/set` | `PRESS` |
| Restart | `homeassistant/button/<node>/restart/set` | `PRESS` |

Discovery uses `homeassistant/button/<node>/<action>/config`. These are stateless
buttons; device availability comes from `pc2mqtt/<node>/availability`.

The old shutdown switch discovery entry is cleared automatically on connection.
Update existing shutdown automations to use the new button or topic with `PRESS`;
`homeassistant/switch/<node>/set` with `OFF` is no longer handled.

Publish commands without retain. Retained commands are ignored on subscription so
reconnecting does not replay a power action. Commands run asynchronously; launch
errors and unsuccessful exits are logged, and commands can be retried after failure
or return from sleep.

Windows uses `shutdown` for shutdown/restart and Windows PowerShell with
[`Application.SetSuspendState`](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.application.setsuspendstate)
for sleep. Linux uses `shutdown now`, `shutdown -r now`, and `systemctl suspend`.
The user running pc2mqtt must have permission to perform these actions; Linux sleep
requires systemd, and sleep must be supported by the computer's power configuration.
"""

import json
from queue import Empty, Full, Queue
import subprocess

from pc2mqtt.publishing import publish


class PowerControls:
    def __init__(self, client, node, device, connection_availability_topic, logger):
        self.client = client
        self.device = device
        self.availability_topic = connection_availability_topic
        self.logger = logger
        self._legacy_config_topic = f"homeassistant/switch/{node}/config"
        self.button_topics = {
            action: f"homeassistant/button/{node}/{action}"
            for action in ("shutdown", "sleep", "restart")
        }
        self._commands = {
            f"{topic}/set": (b"PRESS", action)
            for action, topic in self.button_topics.items()
        }
        self._pending = Queue(maxsize=1)
        self._process = None
        self._action = None
        self._stopping = False

    def config(self):
        identifier = self.device["name"].lower().replace(" ", "_")
        common = {
            "device": self.device,
            "availability_topic": self.availability_topic,
        }
        # Remove the old shutdown switch when migrating to aligned buttons.
        publish(self.client, topic=self._legacy_config_topic, payload="", retain=True)
        for action, topic in self.button_topics.items():
            message = {
                **common,
                "name": action.title(),
                "command_topic": f"{topic}/set",
                "payload_press": "PRESS",
                "unique_id": f"{identifier}_{action}",
                "icon": {"shutdown": "mdi:power", "sleep": "mdi:sleep", "restart": "mdi:restart"}[action],
            }
            if action == "restart":
                message["device_class"] = "restart"
            publish(self.client, topic=f"{topic}/config", payload=json.dumps(message), retain=True)
        for topic in self._commands:
            self.client.subscribe(topic=topic)

    def on_message(self, message):
        command = self._commands.get(message.topic)
        if command is None:
            return False
        payload, action = command
        # Old retained commands must not execute again after a reconnect.
        if message.retain or message.payload != payload:
            return True
        if self._process is not None or self._stopping:
            return True
        try:
            self._pending.put_nowait(action)
        except Full:
            pass
        return True

    def _start(self, action):
        try:
            self._process = subprocess.Popen(_power_command(self.device["model"], action))
        except (OSError, ValueError, NotImplementedError) as exc:
            self.logger.error("Unable to %s: %s", action, exc)
            return
        self._action = action

    def poll(self):
        # Launch queued commands on the polling thread and never wait for exit.
        if self._process is None and not self._stopping:
            try:
                action = self._pending.get_nowait()
            except Empty:
                pass
            else:
                self._start(action)
        if self._process is not None:
            result = self._process.poll()
            if result is None:
                return
            if result != 0:
                self.logger.error("Power command %s failed with exit code %s", self._action, result)
            elif self._action in ("shutdown", "restart"):
                self._stopping = True
            self._process = None
    

def _power_command(system, action):
    if system.lower() == "linux":
        return {
            "shutdown": ["shutdown", "now"],
            "restart": ["shutdown", "-r", "now"],
            "sleep": ["systemctl", "suspend"],
        }[action]
    if system.lower() == "windows":
        return {
            "shutdown": ["shutdown", "/s", "/t", "0"],
            "restart": ["shutdown", "/r", "/t", "0"],
            "sleep": [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "$ErrorActionPreference = 'Stop'; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "if (-not [System.Windows.Forms.Application]::SetSuspendState("
                "[System.Windows.Forms.PowerState]::Suspend, $false, $false)) { exit 1 }",
            ],
        }[action]
    raise NotImplementedError(f"Power controls are not supported on {system}")
