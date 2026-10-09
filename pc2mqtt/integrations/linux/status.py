"""Machine heartbeat, broker-facing IP address, and Linux uptime."""

from pathlib import Path

from .._entities import Entity
from .._shared import EntityIntegration
from .._state import broker_ip, last_seen


class Backend:
    def supported_features(self):
        return {'uptime'} if Path('/proc/uptime').is_file() else set()

    def unsupported_reason(self, key):
        return '/proc/uptime is not available'

    def read(self, key):
        if key != 'uptime':
            raise ValueError(key)
        return int(float(Path('/proc/uptime').read_text().split()[0]))


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
