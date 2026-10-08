"""Platform-independent MQTT lifecycle, scheduling, and command handling.

Platform modules own entity catalogs, capability detection, and native operations.
"""

from datetime import datetime, timezone
import json
import math
from queue import Empty, Full, Queue
import subprocess
import time

from pc2mqtt.publishing import publish


class EntityIntegration:
    entities = {}
    backend_type = None

    def __init__(self, client, node, device, connection_availability_topic, logger):
        self.client, self.device, self.logger = client, device, logger
        self.connection_availability_topic = connection_availability_topic
        self.identifier = f'computer_{node}'.lower().replace(' ', '_')
        self.topics = {key: f'homeassistant/{domain}/{node}/{key}' for key, (domain, _, _) in self.entities.items()}
        self.commands = {f'{self.topics[key]}/set': key for key, (domain, _, _) in self.entities.items()
                         if domain in ('number', 'switch', 'button')}
        self.pending = Queue(maxsize=16)
        self.backend = None
        self.supported = set()
        self.next_poll = 0
        self.last = {}
        self.errors = {}
        self._unsupported_reasons = {}

    def config(self):
        # Commands from an earlier connection must not execute after reconnect.
        while True:
            try:
                self.pending.get_nowait()
            except Empty:
                break
        platform_reason = None
        try:
            supported = set(self._backend().supported_features()) & self.entities.keys()
        except NotImplementedError as exc:
            supported = set()
            platform_reason = str(exc)
        except Exception as exc:
            # Failed detection is not evidence that existing entities are unsupported.
            self.logger.warning('Unable to determine integration capabilities: %s', exc)
            return
        self.supported = supported
        for key, (domain, name, options) in self.entities.items():
            topic = self.topics[key]
            if key not in supported:
                reason = platform_reason or self.backend.unsupported_reason(key)
                if self._unsupported_reasons.get(key) != reason:
                    self.logger.info('%s disabled: %s', name, reason)
                    self._unsupported_reasons[key] = reason
                # Remove retained discovery from older versions or platform setups.
                for suffix in ('config', 'state', 'availability'):
                    publish(self.client, topic=f'{topic}/{suffix}', payload='')
                if f'{topic}/set' in self.commands:
                    self.client.unsubscribe(f'{topic}/set')
                continue
            self._unsupported_reasons.pop(key, None)
            payload = {
                'name': name, 'unique_id': f'{self.identifier}_{key}', 'device': self.device,
                'availability_mode': 'all',
                'availability': [{'topic': self.connection_availability_topic}, {'topic': f'{topic}/availability'}],
                **options,
            }
            if domain != 'button':
                payload['state_topic'] = f'{topic}/state'
            if domain in ('number', 'switch', 'button'):
                payload['command_topic'] = f'{topic}/set'
                self.client.subscribe(f'{topic}/set')
            if domain in ('binary_sensor', 'switch'):
                payload.update(payload_on='ON', payload_off='OFF')
            if domain == 'button':
                payload['payload_press'] = 'PRESS'
            publish(self.client, topic=f'{topic}/config', payload=json.dumps(payload))
            publish(self.client, topic=f'{topic}/availability', payload='offline')
        self.last.clear()
        self.next_poll = 0

    def on_message(self, message):
        key = self.commands.get(message.topic)
        if key is None:
            return False
        if key not in self.supported or message.retain:
            return True
        try:
            value = message.payload.decode('ascii')
            domain, _, options = self.entities[key]
            if domain == 'number':
                value = float(value)
                if not math.isfinite(value) or not options['min'] <= value <= options['max']:
                    return True
            elif domain == 'switch':
                if value not in ('ON', 'OFF'):
                    return True
                value = value == 'ON'
            elif value != 'PRESS':
                return True
            self.pending.put_nowait((key, value))
        except (UnicodeDecodeError, ValueError, Full):
            pass
        return True

    def _backend(self):
        if self.backend is None:
            self.backend = self.backend_type()
        return self.backend

    def _failed(self, key, exc):
        error = str(exc)
        if self.errors.get(key) != error:
            self.logger.warning('%s unavailable: %s', self.entities[key][1], error)
        self.errors[key] = error
        self.last.pop(key, None)
        publish(self.client, topic=f'{self.topics[key]}/availability', payload='offline')

    def poll(self):
        if not self.client.is_connected():
            return
        failed_command = None
        # Limit command work per tick so a flood cannot starve sensor polling.
        try:
            key, value = self.pending.get_nowait()
        except Empty:
            pass
        else:
            try:
                if key in self.supported:
                    self._backend().execute(key, value)
            except Exception as exc:
                self._failed(key, exc)
                failed_command = key
            self.next_poll = 0
        now = time.monotonic()
        if now < self.next_poll:
            return
        self.next_poll = now + 10
        for key, (domain, _, _) in self.entities.items():
            if key not in self.supported or key == failed_command:
                continue
            try:
                value = self._backend().read(key)
                if domain != 'button':
                    value = ('ON' if value else 'OFF') if domain in ('binary_sensor', 'switch') else str(value)
                previous, refreshed = self.last.get(key, (None, float('-inf')))
                if value != previous or now - refreshed >= 60:
                    if domain != 'button':
                        publish(self.client, topic=f'{self.topics[key]}/state', payload=value)
                    publish(self.client, topic=f'{self.topics[key]}/availability', payload='online')
                    self.last[key] = (value, now)
                self.errors.pop(key, None)
            except Exception as exc:
                self._failed(key, exc)


class PlaybackSensor:
    """Own audio discovery, availability, playback detection, and publishing cadence."""

    def __init__(self, client, node, device, connection_availability_topic, logger, backend):
        self.backend = backend
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
        self.supported = False
        self._unsupported_reason = None
        self._audio_error = None
        self._audio_active_since = None
        self._audio_last_state = None
        self._next_audio_update = 0

    def _remove_discovery(self):
        reason = self.backend.unsupported_reason('audio_playing')
        if reason != self._unsupported_reason:
            self.logger.info('Audio playing disabled: %s', reason)
            self._unsupported_reason = reason
        self.supported = False
        self._audio_active_since = None
        self._audio_last_state = None
        self._audio_error = None
        for topic in (self.config_topic, self.state_topic, self.availability_topic):
            publish(self.client, topic=topic, payload='')

    def config(self):
        try:
            supported = 'audio_playing' in self.backend.supported_features()
        except Exception as exc:
            self.logger.warning('Unable to determine audio capabilities: %s', exc)
            return
        self.supported = supported
        if not self.supported:
            self._remove_discovery()
            return
        self._unsupported_reason = None
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
        if not self.supported:
            return
        availability = self.availability_topic
        try:
            playing = self.backend.is_audio_playing()
        except Exception as exc:
            if isinstance(exc, FileNotFoundError) and 'audio_playing' not in self.backend.supported_features():
                self._remove_discovery()
                return
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


class PowerControls:
    def __init__(self, client, node, device, connection_availability_topic, logger, backend):
        self.backend = backend
        self.supported = set()
        self._unsupported_reasons = {}
        self.client = client
        self.device = device
        # Preserve legacy entity IDs independently of the editable display name.
        self.identifier = f"computer_{node}".lower().replace(" ", "_")
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
        while True:
            try:
                self._pending.get_nowait()
            except Empty:
                break
        try:
            self.supported = set(self.backend.supported_features()) & self.button_topics.keys()
        except Exception as exc:
            self.logger.warning('Unable to determine power capabilities: %s', exc)
            return
        common = {
            "device": self.device,
            "availability_topic": self.availability_topic,
        }
        # Remove the old shutdown switch when migrating to aligned buttons.
        publish(self.client, topic=self._legacy_config_topic, payload="", retain=True)
        for action, topic in self.button_topics.items():
            if action not in self.supported:
                reason = self.backend.unsupported_reason(action)
                if self._unsupported_reasons.get(action) != reason:
                    self.logger.info('%s disabled: %s', action.title(), reason)
                    self._unsupported_reasons[action] = reason
                for suffix in ('config', 'state', 'availability'):
                    publish(self.client, topic=f'{topic}/{suffix}', payload='')
                self.client.unsubscribe(f'{topic}/set')
                continue
            self._unsupported_reasons.pop(action, None)
            message = {
                **common,
                "name": action.title(),
                "command_topic": f"{topic}/set",
                "payload_press": "PRESS",
                "unique_id": f"{self.identifier}_{action}",
                "icon": {"shutdown": "mdi:power", "sleep": "mdi:sleep", "restart": "mdi:restart"}[action],
            }
            if action == "restart":
                message["device_class"] = "restart"
            publish(self.client, topic=f"{topic}/config", payload=json.dumps(message), retain=True)
        for topic, (_, action) in self._commands.items():
            if action in self.supported:
                self.client.subscribe(topic=topic)

    def on_message(self, message):
        command = self._commands.get(message.topic)
        if command is None:
            return False
        payload, action = command
        # Old retained commands must not execute again after a reconnect.
        if action not in self.supported or message.retain or message.payload != payload:
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
            self._process = subprocess.Popen(self.backend.command(action))
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
                if action in self.supported:
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


class HeartbeatSensors:
    def __init__(self, client, node, device, connection_availability_topic, logger):
        self.client = client
        self.device = device
        # Preserve legacy entity IDs independently of the editable display name.
        self.identifier = f"computer_{node}".lower().replace(" ", "_")
        self.availability_topic = connection_availability_topic
        self.logger = logger
        self.topics = {
            key: f"homeassistant/sensor/{node}/{key}"
            for key in ("ip_address", "last_seen")
        }
        self._next_update = 0

    def config(self):
        for key, topic in self.topics.items():
            message = {
                "name": "IP address" if key == "ip_address" else "Last seen",
                "state_topic": f"{topic}/state",
                "unique_id": f"{self.identifier}_{key}",
                "device": self.device,
                "entity_category": "diagnostic",
            }
            if key == "last_seen":
                message["device_class"] = "timestamp"
            else:
                message["icon"] = "mdi:ip-network"
                message["availability_topic"] = self.availability_topic
            publish(self.client, topic=f"{topic}/config", payload=json.dumps(message))
        self._next_update = 0

    def poll(self):
        now = time.monotonic()
        if now < self._next_update or not self.client.is_connected():
            return
        self._next_update = now + 60
        publish(
            self.client, topic=f"{self.topics['last_seen']}/state",
            payload=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        try:
            connection = self.client.socket()
            if connection is None:
                return
            address = connection.getsockname()[0]
        except OSError as exc:
            self.logger.warning("Unable to read machine IP address: %s", exc)
            return
        publish(self.client, topic=f"{self.topics['ip_address']}/state", payload=address)


class AudioIntegration(EntityIntegration):
    def __init__(self, *args):
        super().__init__(*args)
        self.playback = PlaybackSensor(*args, backend=self._backend())

    def config(self):
        super().config()
        self.playback.backend = self._backend()
        self.playback.config()

    def poll(self):
        super().poll()
        if self.client.is_connected():
            self.playback.poll()


class PowerIntegration(EntityIntegration):
    def __init__(self, *args):
        super().__init__(*args)
        self.controls = PowerControls(*args, backend=self._backend())

    def config(self):
        super().config()
        self.controls.backend = self._backend()
        self.controls.config()

    def on_message(self, message):
        return self.controls.on_message(message) or super().on_message(message)

    def poll(self):
        if self.client.is_connected():
            super().poll()
            self.controls.poll()


class StatusIntegration(EntityIntegration):
    def __init__(self, *args):
        super().__init__(*args)
        self.heartbeat = HeartbeatSensors(*args)

    def config(self):
        super().config()
        self.heartbeat.config()

    def poll(self):
        super().poll()
        self.heartbeat.poll()
