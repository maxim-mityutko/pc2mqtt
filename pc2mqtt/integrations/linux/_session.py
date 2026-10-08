"""Linux graphical-session detection shared by user and display controls."""

import os
import shutil
import subprocess


def run(*args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=True,
        timeout=3,
        env={**os.environ, 'LC_ALL': 'C'},
    ).stdout.strip()


class Session:
    def __init__(self):
        self._session_properties = {}

    def properties(self):
        if not shutil.which('loginctl'):
            self._session_properties = {}
        else:
            try:
                _, self._session_properties = self.session()
            except RuntimeError:
                # A missing/non-graphical session is a known unsupported environment.
                self._session_properties = {}
            except (OSError, subprocess.SubprocessError):
                # Keep known capabilities through temporary logind failures.
                pass
        return self._session_properties

    def session(self):
        session = os.environ.get('XDG_SESSION_ID') or run(
            'loginctl', 'show-user', str(os.getuid()), '-p', 'Display', '--value'
        )
        if not session:
            raise RuntimeError('No graphical login session found')
        properties = dict(
            line.split('=', 1)
            for line in run('loginctl', 'show-session', session).splitlines()
            if '=' in line
        )
        if properties.get('User') != str(os.getuid()) or properties.get('Type') not in (
            'x11',
            'wayland',
        ):
            raise RuntimeError('No graphical session for the current user')
        return session, properties

    def x11(self):
        properties = self.properties()
        session_type = properties.get('Type') or os.environ.get('XDG_SESSION_TYPE')
        return bool(os.environ.get('DISPLAY')) and session_type == 'x11'
