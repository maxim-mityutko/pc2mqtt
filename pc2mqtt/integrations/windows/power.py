"""Windows shutdown/suspend commands and asynchronous display power control."""

import ctypes as C
import shutil
import subprocess

from .._entities import Entity
from .._shared import EntityIntegration
from ._api import NativeAPI


class Backend(NativeAPI):
    def __init__(self, *, runner=None):
        super().__init__()
        self.runner = runner if runner is not None else subprocess.Popen

    def start(self, key):
        return self.runner(self.command(key))

    features = ('displays_off',)

    def prepare(self, key):
        self.display_off = self.bind(
            'user32',
            'SendNotifyMessageW',
            C.c_int,
            (C.c_void_p, C.c_uint32, C.c_size_t, C.c_ssize_t),
        )

    def supported_features(self):
        features = super().supported_features()
        if shutil.which('shutdown'):
            features.update(('shutdown', 'restart'))
        if shutil.which('powershell.exe'):
            features.add('sleep')
        return features

    def unsupported_reason(self, key):
        if key in ('shutdown', 'restart'):
            return 'shutdown is not installed or not on PATH'
        if key == 'sleep':
            return 'Windows PowerShell is not installed or not on PATH'
        return super().unsupported_reason(key)

    def command(self, action):
        return {
            'shutdown': ['shutdown', '/s', '/t', '0'],
            'restart': ['shutdown', '/r', '/t', '0'],
            'sleep': [
                'powershell.exe',
                '-NoProfile',
                '-NonInteractive',
                '-Command',
                "$ErrorActionPreference = 'Stop'; "
                'Add-Type -AssemblyName System.Windows.Forms; '
                'if (-not [System.Windows.Forms.Application]::SetSuspendState('
                '[System.Windows.Forms.PowerState]::Suspend, $false, $false)) { exit 1 }',
            ],
        }[action]

    def check(self, key):
        if key != 'displays_off':
            raise ValueError(key)
        self.prepare(key)

    def execute(self, key, value):
        self.check(key)
        # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER; do not wait on other apps.
        if not self.display_off(0xFFFF, 0x112, 0xF170, 2):
            raise RuntimeError('Display-off request failed')


class Power(EntityIntegration):
    backend_type = Backend
    legacy_discovery = ('homeassistant/switch/{node}/config',)
    entities = {
        'shutdown': Entity(
            'button',
            'Shutdown',
            {'icon': 'mdi:power'},
            availability='connection',
            behavior='process',
            stop_after=True,
        ),
        'sleep': Entity(
            'button',
            'Sleep',
            {'icon': 'mdi:sleep'},
            availability='connection',
            behavior='process',
            stop_after=False,
        ),
        'restart': Entity(
            'button',
            'Restart',
            {'icon': 'mdi:restart', 'device_class': 'restart'},
            availability='connection',
            behavior='process',
            stop_after=True,
        ),
        'displays_off': Entity('button', 'Turn off displays', {'icon': 'mdi:monitor-off'}),
    }
