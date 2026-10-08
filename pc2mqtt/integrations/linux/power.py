"""Linux shutdown/suspend commands and X11/Sway display power."""
import json
import os
import shutil
from pathlib import Path

from .._shared import PowerIntegration
from ._session import Session, run


class Backend(Session):
    def supported_features(self):
        features = set()
        if shutil.which('shutdown'):
            features.update(('shutdown', 'restart'))
        if shutil.which('systemctl') and Path('/run/systemd/system').is_dir():
            features.add('sleep')
        properties = self.properties()
        session_type = properties.get('Type') or os.environ.get('XDG_SESSION_TYPE')
        x11_display = session_type == 'x11' and os.environ.get('DISPLAY') and shutil.which('xset')
        sway_display = os.environ.get('SWAYSOCK') and shutil.which('swaymsg')
        if x11_display or sway_display:
            features.add('displays_off')
        return features

    def unsupported_reason(self, key):
        if key in ('shutdown', 'restart'):
            return 'shutdown is not installed or not on PATH'
        if key == 'sleep':
            return 'requires systemctl and a running systemd system manager'
        return 'requires an X11 display with xset, or a Sway session with swaymsg'

    def command(self, action):
        return {
            'shutdown': ['shutdown', 'now'],
            'restart': ['shutdown', '-r', 'now'],
            'sleep': ['systemctl', 'suspend'],
        }[action]

    def display_command(self):
        if os.environ.get('SWAYSOCK') and shutil.which('swaymsg'):
            return ('swaymsg', '-r', 'output', '*', 'power', 'off')
        if self.x11() and shutil.which('xset'):
            return ('xset', 'dpms', 'force', 'off')
        raise NotImplementedError('Display-off requires X11 with xset or Sway with swaymsg')

    def read(self, key):
        if key != 'displays_off':
            raise ValueError(key)
        self.display_command()

    def execute(self, key, value):
        if key != 'displays_off':
            raise ValueError(key)
        command = self.display_command()
        output = run(*command)
        if command[0] == 'swaymsg' and not all(item.get('success') for item in json.loads(output)):
            raise RuntimeError('Sway rejected display-off command')


class Power(PowerIntegration):
    backend_type = Backend
    entities = {
        'displays_off': ('button', 'Turn off displays', {'icon': 'mdi:monitor-off'}),
    }
