"""PulseAudio/PipeWire audio controls and playerctl media playback commands."""

import os
import re
import shutil
import subprocess

from .._entities import Entity
from .._shared import EntityIntegration
from ._session import run


class Backend:
    def supported_features(self):
        features = {'audio_playing', 'volume', 'mute'} if shutil.which('pactl') else set()
        if shutil.which('playerctl'):
            features.add('play_pause')
        return features

    def unsupported_reason(self, key):
        if key == 'play_pause':
            return 'playerctl is not installed or not on PATH'
        return 'pactl is not installed or not on PATH'

    def read(self, key):
        if key == 'audio_playing':
            return self.is_audio_playing()
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

    def check(self, key):
        if key != 'play_pause':
            raise ValueError(key)
        run('playerctl', 'status')

    def execute(self, key, value):
        if key == 'volume':
            run('pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{value:g}%')
        elif key == 'mute':
            run('pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1' if value else '0')
        elif key == 'play_pause':
            run('playerctl', 'play-pause')
        else:
            raise ValueError(key)

    def is_audio_playing(self) -> bool:
        result = subprocess.run(
            ['pactl', 'list', 'short', 'sinks'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            env={**os.environ, 'LC_ALL': 'C'},
        )
        return any(
            line.split()[-1] == 'RUNNING' for line in result.stdout.splitlines() if line.strip()
        )


class Audio(EntityIntegration):
    backend_type = Backend
    entities = {
        'play_pause': Entity('button', 'Play / Pause', {'icon': 'mdi:play-pause'}),
        'audio_playing': Entity(
            'binary_sensor',
            'Audio playing',
            {'icon': 'mdi:volume-high', 'expire_after': 90},
            interval=0,
            behavior='playback',
        ),
        'volume': Entity(
            'number',
            'Volume',
            {
                'min': 0,
                'max': 100,
                'step': 1,
                'mode': 'slider',
                'unit_of_measurement': '%',
                'icon': 'mdi:volume-high',
            },
        ),
        'mute': Entity('switch', 'Mute', {'icon': 'mdi:volume-off'}),
    }
