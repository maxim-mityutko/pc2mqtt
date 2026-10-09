"""One MQTT lifecycle for each platform/domain integration.

Entity catalogs supply discovery metadata, sampling policy, and local readers.
Backends supply native capabilities, reads, checks, and command execution.
"""

import json
import time
from queue import Empty, Full, Queue

from pc2mqtt.publishing import publish

from ._state import PlaybackState, ProcessCommands


class EntityIntegration:
    entities = {}
    backend_type = None
    legacy_discovery = ()

    def __init__(
        self,
        client,
        node,
        device,
        connection_availability_topic,
        logger,
        *,
        backend=None,
        clock=None,
    ):
        self.client, self.device, self.logger = client, device, logger
        self.connection_availability_topic = connection_availability_topic
        self.identifier = f'computer_{node}'.lower().replace(' ', '_')
        self.backend = backend if backend is not None else self.backend_type()
        self.clock = clock if clock is not None else time.monotonic
        self.topics = {
            key: f'homeassistant/{entity.domain}/{node}/{key}'
            for key, entity in self.entities.items()
        }
        self.commands = {
            f'{self.topics[key]}/set': key
            for key, entity in self.entities.items()
            if entity.controllable
        }
        self.pending = (
            Queue(maxsize=16)
            if any(
                entity.controllable and entity.behavior != 'process'
                for entity in self.entities.values()
            )
            else None
        )
        self.processes = (
            ProcessCommands(self.backend.start, logger)
            if any(entity.behavior == 'process' for entity in self.entities.values())
            else None
        )
        self.playback = {
            key: PlaybackState()
            for key, entity in self.entities.items()
            if entity.behavior == 'playback'
        }
        self.supported = set()
        self.next_poll = {}
        self.last = {}
        self.errors = {}
        self.unsupported_reasons = {}
        self.legacy_topics = [topic.format(node=node) for topic in self.legacy_discovery]

    def _clear_commands(self):
        if self.pending is not None:
            while True:
                try:
                    self.pending.get_nowait()
                except Empty:
                    break
        if self.processes is not None:
            self.processes.clear()

    def _remove(self, key, reason):
        entity, topic = self.entities[key], self.topics[key]
        if self.unsupported_reasons.get(key) != reason:
            self.logger.info('%s disabled: %s', entity.name, reason)
            self.unsupported_reasons[key] = reason
        for suffix in ('config', 'state', 'availability'):
            publish(self.client, topic=f'{topic}/{suffix}', payload='')
        if entity.controllable:
            self.client.unsubscribe(f'{topic}/set')
        self.supported.discard(key)
        self.last.pop(key, None)
        self.errors.pop(key, None)
        if key in self.playback:
            self.playback[key] = PlaybackState()

    def config(self):
        self._clear_commands()
        # One snapshot is shared by every entity, including playback and power actions.
        try:
            capabilities = set(self.backend.supported_features())
        except Exception as exc:
            self.logger.warning('Unable to determine integration capabilities: %s', exc)
            capabilities = None
        known = self.supported if capabilities is None else capabilities
        self.supported = {
            key
            for key, entity in self.entities.items()
            if key in known or entity.reader is not None or entity.behavior == 'connection'
        }
        for topic in self.legacy_topics:
            publish(self.client, topic=topic, payload='')
        for key, entity in self.entities.items():
            topic = self.topics[key]
            if key not in self.supported:
                if capabilities is not None:
                    self._remove(key, self.backend.unsupported_reason(key))
                continue
            self.unsupported_reasons.pop(key, None)
            payload = entity.discovery(
                topic, f'{self.identifier}_{key}', self.device, self.connection_availability_topic
            )
            if entity.controllable:
                self.client.subscribe(topic=f'{topic}/set')
            publish(self.client, topic=f'{topic}/config', payload=json.dumps(payload))
            if entity.availability == 'entity' and entity.behavior != 'playback':
                publish(self.client, topic=f'{topic}/availability', payload='offline')
        self.last.clear()
        self.next_poll.clear()
        for state in self.playback.values():
            state.refresh()

    def on_message(self, message):
        key = self.commands.get(message.topic)
        if key is None:
            return False
        if key not in self.supported or message.retain:
            return True
        try:
            value = self.entities[key].decode(message.payload)
            if self.entities[key].behavior == 'process':
                self.processes.enqueue(key)
            else:
                self.pending.put_nowait((key, value))
        except (UnicodeDecodeError, ValueError, Full):
            pass
        return True

    def _failed(self, key, exc, now):
        error = str(exc)
        first_failure = key not in self.errors
        if self.errors.get(key) != error:
            self.logger.warning('%s unavailable: %s', self.entities[key].name, error)
        self.errors[key] = error
        self.last.pop(key, None)
        announce = True
        if key in self.playback:
            announce = self.playback[key].failed(now, first_failure)
        if announce and self.entities[key].availability == 'entity':
            publish(self.client, topic=f'{self.topics[key]}/availability', payload='offline')

    def _execute_pending(self, now):
        if self.pending is None:
            return None
        try:
            key, value = self.pending.get_nowait()
        except Empty:
            return None
        failed = None
        if key in self.supported:
            try:
                self.backend.execute(key, value)
            except Exception as exc:
                self._failed(key, exc, now)
                failed = key
        # Read controls back immediately, without changing playback/heartbeat cadence.
        for name, entity in self.entities.items():
            if entity.behavior == 'sample' and entity.reader is None:
                self.next_poll[name] = 0
        return failed

    def _sample(self, key, entity, now):
        if entity.domain == 'button':
            self.backend.check(key)
            value = None
        else:
            value = entity.reader(self.client) if entity.reader else self.backend.read(key)
            if value is None:
                return
        if key in self.playback:
            state = self.playback[key]
            if key in self.errors:
                state.refresh()
            payload = state.sample(value, now)
            if payload is None:
                return
        else:
            payload = entity.encode(value) if entity.domain != 'button' else None
            previous, refreshed = self.last.get(key, (None, float('-inf')))
            if payload == previous and now - refreshed < 60:
                return
        if entity.domain != 'button':
            publish(self.client, topic=f'{self.topics[key]}/state', payload=payload)
        if entity.availability == 'entity':
            publish(self.client, topic=f'{self.topics[key]}/availability', payload='online')
        self.last[key] = (payload, now)
        self.errors.pop(key, None)

    def poll(self):
        if not self.client.is_connected():
            return
        now = self.clock()
        failed_command = self._execute_pending(now)
        if self.processes is not None:
            self.processes.poll(self.supported, self.entities)
        for key, entity in self.entities.items():
            if (
                key not in self.supported
                or key == failed_command
                or entity.behavior in ('process', 'connection')
            ):
                continue
            if now < self.next_poll.get(key, 0):
                continue
            self.next_poll[key] = now + entity.interval
            try:
                self._sample(key, entity, now)
            except Exception as exc:
                # Missing executables are known support changes; temporary failures
                # retain discovery. Refresh the whole domain from one new snapshot.
                if isinstance(exc, FileNotFoundError):
                    self.config()
                    if key not in self.supported:
                        continue
                self._failed(key, exc, now)
