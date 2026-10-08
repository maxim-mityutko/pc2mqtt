"""PulseAudio/PipeWire playback and default-output controls; requires pactl."""
import os
import re
import shutil
import subprocess

from .._shared import AudioIntegration
from ._session import run


class Backend:
    def supported_features(self):
        return {'audio_playing', 'volume', 'mute'} if shutil.which('pactl') else set()

    def unsupported_reason(self, key):
        return 'pactl is not installed or not on PATH'

    def read(self, key):
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
        raise ValueError(key)

    def execute(self, key, value):
        if key == 'volume':
            run('pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{value:g}%')
        elif key == 'mute':
            run('pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1' if value else '0')
        else:
            raise ValueError(key)

    def is_audio_playing(self) -> bool:
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        return any(
            line.split()[-1] == "RUNNING"
            for line in result.stdout.splitlines() if line.strip()
        )


class Audio(AudioIntegration):
    backend_type = Backend
    entities = {
        'volume': ('number', 'Volume', {
            'min': 0, 'max': 100, 'step': 1, 'mode': 'slider',
            'unit_of_measurement': '%', 'icon': 'mdi:volume-high',
        }),
        'mute': ('switch', 'Mute', {'icon': 'mdi:volume-off'}),
    }
