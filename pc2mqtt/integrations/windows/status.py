"""Machine heartbeat, broker-facing IP address, and 64-bit Windows uptime.

Uptime can span Fast Startup shutdowns.
"""
import ctypes as C

from .._shared import StatusIntegration
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
