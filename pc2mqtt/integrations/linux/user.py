"""Session lock and idle state; loginctl, GNOME Mutter, or native X11."""
import os
import re
import shutil
import subprocess

from .._shared import EntityIntegration
from ._session import Session, run


class Backend(Session):
    def supported_features(self):
        features = set()
        properties = self.properties()
        if properties:
            features.add('lock_session')
            if properties.get('LockedHint') in ('yes', 'no'):
                features.add('session_locked')
        session_type = properties.get('Type') or os.environ.get('XDG_SESSION_TYPE')
        is_x11 = session_type == 'x11' and bool(os.environ.get('DISPLAY'))
        desktop = properties.get('Desktop', '') + ':' + os.environ.get('XDG_CURRENT_DESKTOP', '')
        if (is_x11 and shutil.which('xprintidle')) or ('gnome' in desktop.lower() and shutil.which('gdbus')):
            features.add('idle_time')
        return features

    def unsupported_reason(self, key):
        if key == 'idle_time':
            return 'requires GNOME with gdbus, or an X11 display with xprintidle'
        if not shutil.which('loginctl'):
            return 'loginctl is not installed or not on PATH'
        if not self._session_properties:
            return 'no graphical session could be detected for the current user'
        return 'the desktop does not report a valid LockedHint'

    def read(self, key):
        if key in ('session_locked', 'lock_session'):
            _, properties = self.session()
            if key == 'lock_session':
                return None
            hint = properties.get('LockedHint')
            if hint not in ('yes', 'no'):
                raise RuntimeError('Desktop does not report LockedHint')
            return hint == 'yes'
        if key == 'idle_time':
            try:
                output = run('gdbus', 'call', '--session', '--dest', 'org.gnome.Mutter.IdleMonitor',
                             '--object-path', '/org/gnome/Mutter/IdleMonitor/Core',
                             '--method', 'org.gnome.Mutter.IdleMonitor.GetIdletime')
                match = re.fullmatch(r'\(uint64 (\d+),\)', output)
                if match:
                    return int(match[1]) // 1000
            except (OSError, subprocess.SubprocessError):
                pass
            if self.x11():
                return int(run('xprintidle')) // 1000
            raise NotImplementedError('Idle time requires GNOME Mutter or X11 with xprintidle')
        raise ValueError(key)

    def execute(self, key, value):
        if key != 'lock_session':
            raise ValueError(key)
        run('loginctl', 'lock-session', self.session()[0])


class User(EntityIntegration):
    backend_type = Backend
    entities = {
        'session_locked': ('binary_sensor', 'Session locked', {'icon': 'mdi:lock'}),
        'idle_time': ('sensor', 'User idle time', {
            'device_class': 'duration', 'unit_of_measurement': 'h',
            'value_template': '{{ value | float / 3600 }}',
            'suggested_display_precision': 2, 'state_class': 'measurement',
        }),
        'lock_session': ('button', 'Lock session', {'icon': 'mdi:lock'}),
    }
