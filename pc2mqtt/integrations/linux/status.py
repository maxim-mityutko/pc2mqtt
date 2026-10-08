"""Machine heartbeat, broker-facing IP address, and Linux uptime."""
from pathlib import Path

from .._shared import StatusIntegration


class Backend:
    def supported_features(self):
        return {'uptime'} if Path('/proc/uptime').is_file() else set()

    def unsupported_reason(self, key):
        return '/proc/uptime is not available'

    def read(self, key):
        if key != 'uptime':
            raise ValueError(key)
        return int(float(Path('/proc/uptime').read_text().split()[0]))


class Status(StatusIntegration):
    backend_type = Backend
    entities = {
        'uptime': ('sensor', 'Uptime', {
            'device_class': 'duration', 'unit_of_measurement': 'h',
            'value_template': '{{ value | float / 3600 }}',
            'suggested_display_precision': 2, 'state_class': 'measurement',
            'entity_category': 'diagnostic',
        }),
    }
