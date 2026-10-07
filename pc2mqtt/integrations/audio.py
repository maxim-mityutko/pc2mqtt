"""Audio playback sensor.

Home Assistant also discovers an **Audio playing** binary sensor on the computer's
existing device. It checks playback every second and publishes status every minute,
with an initial update at startup. Playback must remain active for at least two
seconds before it counts as `ON`; this triggers an early update if the last published
state was `OFF`. Continued playback does not trigger repeated early updates. When
playback stops, `OFF` is published at the next scheduled minute update. Short bursts
are ignored, although interruptions between polls may be missed.
This detects active streams, including muted playback or streams containing silence;
it does not measure audible volume or microphone activity.

- **Windows:** uses Core Audio sessions across all active output devices. The
  Windows-only `pycaw` dependency is installed by `poetry install`; rebuild packaged
  executables to include it.
- **Linux:** requires `pactl` (typically provided by `pulseaudio-utils`) and a running
  PulseAudio server or PipeWire with PulseAudio compatibility. Run pc2mqtt as the
  desktop user with access to that audio server. Direct ALSA playback is not covered.

The state topic is `homeassistant/binary_sensor/<node>/audio_playing/state`, where
`<node>` is the lowercase hostname. Discovery is retained and republished on MQTT
reconnection. Detection errors mark the sensor unavailable and are retried without
interrupting the power controls. Shared connection availability at
`pc2mqtt/<node>/availability` uses an MQTT last will; audio also has its own
availability topic for detection failures. Both must be online for the sensor to
be available. The last will and a 90-second state expiry prevent
stale playback states when the client stops reporting.
"""

import json
import os
import subprocess
import time

from pc2mqtt.publishing import publish


class AudioSensor:
    """Own audio discovery, availability, playback detection, and publishing cadence."""

    def __init__(self, client, node, device, connection_availability_topic, logger):
        self.client = client
        self.device = device
        # Preserve legacy entity IDs independently of the editable display name.
        self.identifier = f"computer_{node}".lower().replace(" ", "_")
        self.connection_availability_topic = connection_availability_topic
        self.logger = logger
        topic = f"homeassistant/binary_sensor/{node}/audio_playing"
        self.config_topic = f"{topic}/config"
        self.state_topic = f"{topic}/state"
        self.availability_topic = f"{topic}/availability"
        self._audio_error = None
        self._audio_active_since = None
        self._audio_last_state = None
        self._next_audio_update = 0

    def config(self):
        message = {
            "name": "Audio playing",
            "state_topic": self.state_topic,
            "availability": [
                {"topic": self.connection_availability_topic},
                {"topic": self.availability_topic},
            ],
            "availability_mode": "all",
            "payload_on": "ON",
            "payload_off": "OFF",
            "unique_id": f"{self.identifier}_audio_playing",
            "icon": "mdi:volume-high",
            "expire_after": 90,
            "device": self.device,
        }
        publish(
            self.client,
            topic=self.config_topic,
            payload=json.dumps(message), retain=True,
        )

        self._next_audio_update = 0

    def poll(self):
        availability = self.availability_topic
        try:
            playing = is_audio_playing(self.device["model"])
        except Exception as exc:
            # Detection failures interrupt the continuous-playback window.
            now = time.monotonic()
            self._audio_active_since = None
            error = str(exc)
            if error != self._audio_error:
                self.logger.warning("Unable to detect audio playback: %s", exc)
            if self._audio_error is None or now >= self._next_audio_update:
                publish(self.client, topic=availability, payload="offline", retain=True)
                self._next_audio_update = now + 60
            self._audio_error = error
            return

        now = time.monotonic()
        if self._audio_error is not None:
            self._next_audio_update = 0
        self._audio_error = None
        if playing:
            if self._audio_active_since is None:
                self._audio_active_since = now
        else:
            self._audio_active_since = None

        active = (
            self._audio_active_since is not None
            and now - self._audio_active_since >= 2
        )
        heartbeat_due = now >= self._next_audio_update
        became_active = active and self._audio_last_state != "ON"
        if not heartbeat_due and not became_active:
            return

        payload = "ON" if active else "OFF"
        publish(
            self.client,
            topic=self.state_topic,
            payload=payload,
        )
        publish(self.client, topic=availability, payload="online", retain=True)
        self._audio_last_state = payload
        # Early ON updates leave the regular minute heartbeat on schedule.
        if heartbeat_due:
            self._next_audio_update = now + 60


def is_audio_playing(system: str) -> bool:
    if system.lower() == "windows":
        return _windows_audio_sate()
    if system.lower() == "linux":
        return _linux_audio_state()
    raise NotImplementedError(f"Audio detection is not supported on {system}")


def _linux_audio_state() -> bool:
    result = subprocess.run(
        ["pactl", "list", "short", "sinks"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
        env={**os.environ, "LC_ALL": "C"},
    )
    return any(
        line.split()[-1] == "RUNNING"
        for line in result.stdout.splitlines() if line.strip()
    )


def _windows_audio_sate() -> bool:
    # Lazy imports keep the Windows-only COM dependencies off Linux.
    import comtypes
    from pycaw.api.audiopolicy import IAudioSessionManager2
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        enumerator = AudioUtilities.GetDeviceEnumerator()
        devices = enumerator.EnumAudioEndpoints(
            EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value
        )
        # Inspect every output, including playback routed away from the default.
        for index in range(devices.GetCount()):
            device = devices.Item(index)
            interface = device.Activate(
                IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None
            )
            manager = interface.QueryInterface(IAudioSessionManager2)
            sessions = manager.GetSessionEnumerator()
            for session_index in range(sessions.GetCount()):
                # AudioSessionStateActive = 1; inactive/expired sessions are idle.
                if sessions.GetSession(session_index).GetState() == 1:
                    return True
        return False
    finally:
        comtypes.CoUninitialize()
