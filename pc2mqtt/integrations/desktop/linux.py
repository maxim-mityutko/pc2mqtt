"""Linux desktop backend.

loginctl LockedHint relies on the desktop reporting its lock state. Idle time uses
GNOME Mutter's idle monitor or xprintidle on X11 (never Xwayland). Display power
uses xset on X11 or swaymsg on Sway; other Wayland compositors are unavailable.
The user service needs the desktop's DISPLAY/XAUTHORITY or SWAYSOCK environment.
Audio requires pactl and PulseAudio/PipeWire's PulseAudio compatibility server.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=3,
                          env={**os.environ, 'LC_ALL': 'C'}).stdout.strip()


class LinuxDesktop:
    def __init__(self):
        self._session_properties = {}

    def supported_features(self):
        features = {'uptime'}
        if shutil.which('pactl'):
            features.update(('volume', 'mute'))
        if shutil.which('loginctl'):
            try:
                _, self._session_properties = self.session()
            except (OSError, subprocess.SubprocessError, RuntimeError):
                # Preserve known capabilities through transient logind failures.
                pass
        else:
            self._session_properties = {}
        properties = self._session_properties
        if properties:
            features.add('lock_session')
            if properties.get('LockedHint') in ('yes', 'no'):
                features.add('session_locked')
        session_type = properties.get('Type') or os.environ.get('XDG_SESSION_TYPE')
        is_x11 = session_type == 'x11' and bool(os.environ.get('DISPLAY'))
        desktop = properties.get('Desktop', '') + ':' + os.environ.get('XDG_CURRENT_DESKTOP', '')
        if (is_x11 and shutil.which('xprintidle')) or ('gnome' in desktop.lower() and shutil.which('gdbus')):
            features.add('idle_time')
        if (is_x11 and shutil.which('xset')) or (os.environ.get('SWAYSOCK') and shutil.which('swaymsg')):
            features.add('displays_off')
        return features

    def session(self):
        session = os.environ.get('XDG_SESSION_ID') or run('loginctl', 'show-user', str(os.getuid()), '-p', 'Display', '--value')
        if not session:
            raise RuntimeError('No graphical login session found')
        properties = dict(line.split('=', 1) for line in run('loginctl', 'show-session', session).splitlines() if '=' in line)
        if properties.get('User') != str(os.getuid()) or properties.get('Type') not in ('x11', 'wayland'):
            raise RuntimeError('No graphical session for the current user')
        return session, properties

    def x11(self):
        return bool(os.environ.get('DISPLAY')) and self.session()[1]['Type'] == 'x11'

    def display_command(self):
        if os.environ.get('SWAYSOCK') and shutil.which('swaymsg'):
            return ('swaymsg', '-r', 'output', '*', 'power', 'off')
        if self.x11() and shutil.which('xset'):
            return ('xset', 'dpms', 'force', 'off')
        raise NotImplementedError('Display-off requires X11 with xset or Sway with swaymsg')

    def read(self, key):
        if key == 'uptime':
            return int(float(Path('/proc/uptime').read_text().split()[0]))
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
        if key == 'volume':
            values = re.findall(r'(\d+)%', run('pactl', 'get-sink-volume', '@DEFAULT_SINK@'))
            if not values:
                raise ValueError('No output volume reported')
            return max(map(int, values))
        if key == 'mute':
            output = run('pactl', 'get-sink-mute', '@DEFAULT_SINK@')
            if output not in ('Mute: yes', 'Mute: no'):
                raise ValueError('No mute state reported')
            return output == 'Mute: yes'
        if key == 'displays_off':
            self.display_command()
            return None
        raise ValueError(key)

    def execute(self, key, value):
        if key == 'volume':
            run('pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{value:g}%')
        elif key == 'mute':
            run('pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1' if value else '0')
        elif key == 'lock_session':
            run('loginctl', 'lock-session', self.session()[0])
        elif key == 'displays_off':
            command = self.display_command()
            output = run(*command)
            if command[0] == 'swaymsg' and not all(item.get('success') for item in json.loads(output)):
                raise RuntimeError('Sway rejected display-off command')
        else:
            raise ValueError(key)
