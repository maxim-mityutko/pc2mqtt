"""Linux shutdown/suspend commands and X11/Sway display power."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from .._entities import Entity
from .._shared import EntityIntegration
from ._session import Session, run


class Backend(Session):
    def __init__(self, *, runner=None):
        super().__init__()
        self.runner = runner if runner is not None else subprocess.Popen

    def start(self, key):
        return self.runner(self.command(key))

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

    def check(self, key):
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
