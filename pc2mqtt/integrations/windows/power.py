"""Windows shutdown/suspend commands and asynchronous display power control."""
import ctypes as C
import shutil

from .._shared import PowerIntegration
from ._api import NativeAPI


class Backend(NativeAPI):
    features = ('displays_off',)

    def prepare(self, key):
        self.display_off = self.bind('user32', 'SendNotifyMessageW', C.c_int,
                                    (C.c_void_p, C.c_uint32, C.c_size_t, C.c_ssize_t))

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
                'powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                "$ErrorActionPreference = 'Stop'; "
                'Add-Type -AssemblyName System.Windows.Forms; '
                'if (-not [System.Windows.Forms.Application]::SetSuspendState('
                '[System.Windows.Forms.PowerState]::Suspend, $false, $false)) { exit 1 }',
            ],
        }[action]

    def read(self, key):
        if key != 'displays_off':
            raise ValueError(key)
        self.prepare(key)

    def execute(self, key, value):
        self.read(key)
        # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER; do not wait on other apps.
        if not self.display_off(0xffff, 0x112, 0xf170, 2):
            raise RuntimeError('Display-off request failed')


class Power(PowerIntegration):
    backend_type = Backend
    entities = {
        'displays_off': ('button', 'Turn off displays', {'icon': 'mdi:monitor-off'}),
    }
