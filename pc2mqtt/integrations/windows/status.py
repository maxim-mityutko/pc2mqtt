"""Machine heartbeat, broker-facing IP address, and 64-bit Windows uptime.

Uptime can span Fast Startup shutdowns.
"""

import ctypes as C

from .._entities import Entity
from .._shared import EntityIntegration
from .._state import broker_ip, last_seen
from ._api import NativeAPI


class Backend(NativeAPI):
    features = ('uptime',)

    def prepare(self, key):
        if key != 'uptime':
            raise ValueError(key)
        self.ticks = self.bind('kernel32', 'GetTickCount64', C.c_uint64)

    def read(self, key):
        self.prepare(key)
        return self.ticks() // 1000


class Status(EntityIntegration):
    backend_type = Backend
    legacy_discovery = ('homeassistant/binary_sensor/{node}/online/config',)
    entities = {
        'status': Entity(
            'binary_sensor',
            'Status',
            {'device_class': 'connectivity'},
            availability='none',
            behavior='connection',
        ),
        'last_seen': Entity(
            'sensor',
            'Last seen',
            {'entity_category': 'diagnostic', 'device_class': 'timestamp'},
            interval=60,
            availability='none',
            reader=last_seen,
        ),
        'ip_address': Entity(
            'sensor',
            'IP address',
            {'entity_category': 'diagnostic', 'icon': 'mdi:ip-network'},
            interval=60,
            availability='connection',
            reader=broker_ip,
        ),
        'uptime': Entity(
            'sensor',
            'Uptime',
            {
                'device_class': 'duration',
                'unit_of_measurement': 'h',
                'value_template': '{{ value | float / 3600 }}',
                'suggested_display_precision': 2,
                'state_class': 'measurement',
                'entity_category': 'diagnostic',
            },
        ),
    }
