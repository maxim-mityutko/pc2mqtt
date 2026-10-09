"""Entity metadata and MQTT discovery/payload rules."""

import math
from dataclasses import dataclass, field
from typing import Callable, Literal


@dataclass(frozen=True)
class Entity:
    domain: str
    name: str
    options: dict = field(default_factory=dict)
    interval: float = 10
    availability: Literal['entity', 'connection', 'none'] = 'entity'
    behavior: Literal['sample', 'playback', 'process', 'connection'] = 'sample'
    reader: Callable | None = None
    stop_after: bool = False

    @property
    def controllable(self):
        return self.domain in ('button', 'number', 'switch')

    def decode(self, payload):
        value = payload.decode('ascii')
        if self.domain == 'number':
            value = float(value)
            if not math.isfinite(value) or not self.options['min'] <= value <= self.options['max']:
                raise ValueError('Value outside control range')
            return value
        if self.domain == 'switch' and value in ('ON', 'OFF'):
            return value == 'ON'
        if self.domain == 'button' and value == 'PRESS':
            return value
        raise ValueError('Invalid command payload')

    def encode(self, value):
        if self.domain in ('binary_sensor', 'switch'):
            return 'ON' if value else 'OFF'
        return str(value)

    def discovery(self, topic, identifier, device, connection_topic):
        message = {'name': self.name, 'unique_id': identifier, 'device': device, **self.options}
        if self.availability == 'entity':
            message.update(
                availability_mode='all',
                availability=[
                    {'topic': connection_topic},
                    {'topic': f'{topic}/availability'},
                ],
            )
        elif self.availability == 'connection':
            message['availability_topic'] = connection_topic
        if self.domain != 'button':
            message['state_topic'] = f'{topic}/state'
        if self.controllable:
            message['command_topic'] = f'{topic}/set'
        if self.domain in ('binary_sensor', 'switch'):
            message.update(payload_on='ON', payload_off='OFF')
        if self.domain == 'button':
            message['payload_press'] = 'PRESS'
        if self.behavior == 'connection':
            # The app's birth/shutdown messages and broker will supply the state.
            message.update(state_topic=connection_topic, payload_on='online', payload_off='offline')
        return message
