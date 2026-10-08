"""Desktop sensors and controls, sampled every ten seconds and refreshed every minute.

Commands are validated and queued on the MQTT thread, then executed by poll().
Only supported entities are discovered; temporary backend errors use per-entity
availability without removing discovery. Volume/mute address the default output.
See linux.py and windows.py for platform requirements.
"""

import json
import math
from queue import Empty, Full, Queue
import time

from pc2mqtt.publishing import publish


ENTITIES = {
    'session_locked': ('binary_sensor', 'Session locked', {'icon': 'mdi:lock'}),
    'idle_time': ('sensor', 'User idle time', {'device_class': 'duration', 'unit_of_measurement': 'h', 'value_template': '{{ value | float / 3600 }}', 'suggested_display_precision': 2, 'state_class': 'measurement'}),
    'uptime': ('sensor', 'Uptime', {'device_class': 'duration', 'unit_of_measurement': 'h', 'value_template': '{{ value | float / 3600 }}', 'suggested_display_precision': 2, 'state_class': 'measurement', 'entity_category': 'diagnostic'}),
    'volume': ('number', 'Volume', {'min': 0, 'max': 100, 'step': 1, 'mode': 'slider', 'unit_of_measurement': '%', 'icon': 'mdi:volume-high'}),
    'mute': ('switch', 'Mute', {'icon': 'mdi:volume-off'}),
    'lock_session': ('button', 'Lock session', {'icon': 'mdi:lock'}),
    'displays_off': ('button', 'Turn off displays', {'icon': 'mdi:monitor-off'}),
}


class Desktop:
    def __init__(self, client, node, device, connection_availability_topic, logger):
        self.client, self.device, self.logger = client, device, logger
        self.connection_availability_topic = connection_availability_topic
        self.identifier = f'computer_{node}'.lower().replace(' ', '_')
        self.topics = {key: f'homeassistant/{domain}/{node}/{key}' for key, (domain, _, _) in ENTITIES.items()}
        self.commands = {f'{self.topics[key]}/set': key for key in ('volume', 'mute', 'lock_session', 'displays_off')}
        self.pending = Queue(maxsize=16)
        self.backend = None
        self.supported = set()
        self.next_poll = 0
        self.last = {}
        self.errors = {}
        self._unsupported_reasons = {}

    def config(self):
        platform_reason = None
        try:
            supported = set(self._backend().supported_features()) & ENTITIES.keys()
        except NotImplementedError as exc:
            supported = set()
            platform_reason = str(exc)
        except Exception as exc:
            # Failed detection is not evidence that existing entities are unsupported.
            self.logger.warning('Unable to determine desktop capabilities: %s', exc)
            return
        self.supported = supported
        for key, (domain, name, options) in ENTITIES.items():
            topic = self.topics[key]
            if key not in supported:
                reason = platform_reason or self.backend.unsupported_reason(key)
                if self._unsupported_reasons.get(key) != reason:
                    self.logger.info('%s disabled: %s', name, reason)
                    self._unsupported_reasons[key] = reason
                # Remove retained discovery from older versions or desktop setups.
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
        # Commands from an earlier connection must not execute after reconnect.
        while True:
            try:
                self.pending.get_nowait()
            except Empty:
                break

    def on_message(self, message):
        key = self.commands.get(message.topic)
        if key is None:
            return False
        if key not in self.supported or message.retain:
            return True
        try:
            value = message.payload.decode('ascii')
            if key == 'volume':
                value = float(value)
                if not math.isfinite(value) or not 0 <= value <= 100:
                    return True
            elif key == 'mute':
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
            if self.device['model'] == 'Windows':
                from .windows import WindowsDesktop
                self.backend = WindowsDesktop()
            elif self.device['model'] == 'Linux':
                from .linux import LinuxDesktop
                self.backend = LinuxDesktop()
            else:
                raise NotImplementedError('Desktop features require Windows or Linux')
        return self.backend

    def _failed(self, key, exc):
        error = str(exc)
        if self.errors.get(key) != error:
            self.logger.warning('%s unavailable: %s', ENTITIES[key][1], error)
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
        for key, (domain, _, _) in ENTITIES.items():
            if key not in self.supported or key == failed_command:
                continue
            try:
                value = self._backend().read(key)
                if domain != 'button':
                    value = ('ON' if value else 'OFF') if key in ('session_locked', 'mute') else str(value)
                previous, refreshed = self.last.get(key, (None, float('-inf')))
                if value != previous or now - refreshed >= 60:
                    if domain != 'button':
                        publish(self.client, topic=f'{self.topics[key]}/state', payload=value)
                    publish(self.client, topic=f'{self.topics[key]}/availability', payload='online')
                    self.last[key] = (value, now)
                self.errors.pop(key, None)
            except Exception as exc:
                self._failed(key, exc)
