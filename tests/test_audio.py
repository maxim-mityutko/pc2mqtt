import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from pc2mqtt.integrations import integration_types
from pc2mqtt.integrations.linux.audio import Audio
from pc2mqtt.integrations.linux.audio import Backend as LinuxAudio
from pc2mqtt.integrations.windows.audio import Backend as WindowsAudio


class TestAudioDetection:
    @patch('pc2mqtt.integrations.linux.audio.subprocess.run')
    def test_linux_output_states(self, run):
        for output, expected in [
            ('', False),
            ('0\tspeaker\tdriver\ts16le 2ch 48000Hz\tIDLE\n', False),
            ('0\tspeaker\tdriver\ts16le 2ch 48000Hz\tSUSPENDED\n', False),
            (
                '0\tspeaker\tdriver\ts16le 2ch 48000Hz\tIDLE\n1\theadphones\tdriver\ts16le 2ch 48000Hz\tRUNNING\n',
                True,
            ),
        ]:
            run.return_value.stdout = output
            assert LinuxAudio().is_audio_playing() == expected
        assert run.call_args.args[0] == ['pactl', 'list', 'short', 'sinks']
        assert run.call_args.kwargs['check']
        assert run.call_args.kwargs['timeout'] == 5
        assert run.call_args.kwargs['env']['LC_ALL'] == 'C'

    @patch('pc2mqtt.integrations.linux.audio.subprocess.run')
    def test_linux_errors_propagate(self, run):
        for error in [
            FileNotFoundError(),
            subprocess.TimeoutExpired('pactl', 5),
            subprocess.CalledProcessError(1, 'pactl'),
        ]:
            run.side_effect = error
            with pytest.raises(type(error)):
                LinuxAudio().is_audio_playing()

    def test_unsupported_platform(self):
        with pytest.raises(NotImplementedError):
            integration_types('Darwin')

    def test_windows_outputs_and_com_cleanup(self):
        com = Mock()
        utilities = Mock()
        devices = utilities.GetDeviceEnumerator.return_value.EnumAudioEndpoints.return_value
        modules = {
            'comtypes': com,
            'pycaw': Mock(),
            'pycaw.api': Mock(),
            'pycaw.api.audiopolicy': Mock(),
            'pycaw.constants': SimpleNamespace(
                DEVICE_STATE=SimpleNamespace(ACTIVE=SimpleNamespace(value=1)),
                EDataFlow=SimpleNamespace(eRender=SimpleNamespace(value=0)),
            ),
            'pycaw.pycaw': SimpleNamespace(AudioUtilities=utilities),
        }
        with patch.dict(sys.modules, modules):
            for states, expected in [([], False), ([0, 2], False), ([0, 1], True)]:
                outputs = []
                for state in states:
                    output = Mock()
                    sessions = output.Activate.return_value.QueryInterface.return_value.GetSessionEnumerator.return_value
                    sessions.GetCount.return_value = 1
                    sessions.GetSession.return_value.GetState.return_value = state
                    outputs.append(output)
                devices.GetCount.return_value = len(outputs)
                devices.Item.side_effect = outputs
                assert WindowsAudio().is_audio_playing() == expected
            devices.GetCount.side_effect = RuntimeError('audio service unavailable')
            with pytest.raises(RuntimeError):
                WindowsAudio().is_audio_playing()
        assert com.CoInitialize.call_count == 4
        assert com.CoUninitialize.call_count == 4
        utilities.GetDeviceEnumerator.return_value.EnumAudioEndpoints.assert_called_with(0, 1)


class TestPlayback:
    @pytest.fixture(autouse=True)
    def setup(self, make_integration):
        self.h = make_integration(Audio, capabilities={'audio_playing'})
        self.audio = self.h.integration
        self.audio.config()
        self.h.client.reset_mock()

    def sample(self, now, playing):
        self.h.client.reset_mock()
        self.h.clock.return_value = now
        if isinstance(playing, Exception):
            self.h.backend.read.side_effect = playing
        else:
            self.h.backend.read.side_effect = None
            self.h.backend.read.return_value = playing
        self.audio.poll()
        return [call.kwargs['payload'] for call in self.h.client.publish.call_args_list]

    @pytest.mark.parametrize(
        'samples',
        [
            [
                (0, False, ['OFF', 'online']),
                (1, False, []),
                (59, False, []),
                (60, False, ['OFF', 'online']),
                (119, False, []),
                (120, False, ['OFF', 'online']),
            ],
            [
                (0, False, ['OFF', 'online']),
                (10, True, []),
                (11.99, True, []),
                (12, True, ['ON', 'online']),
                (14, True, []),
                (59, True, []),
                (60, True, ['ON', 'online']),
                (61, False, []),
                (119, False, []),
                (120, False, ['OFF', 'online']),
                (121, True, []),
                (123, True, ['ON', 'online']),
            ],
            [
                (0, False, ['OFF', 'online']),
                (10, True, []),
                (11, False, []),
                (12, True, []),
                (13, False, []),
                (59, True, []),
                (60, True, ['OFF', 'online']),
                (61, True, ['ON', 'online']),
            ],
            [(0, True, ['OFF', 'online']), (1, True, []), (2, True, ['ON', 'online'])],
        ],
        ids=['idle-heartbeat', 'sustained-playback', 'short-bursts', 'active-at-startup'],
    )
    def test_cadence(self, samples):
        for now, playing, expected in samples:
            assert self.sample(now, playing) == expected

    def test_failure_resets_debounce_and_recovers(self):
        self.sample(0, False)
        self.sample(10, True)
        assert self.sample(11, RuntimeError('backend unavailable')) == ['offline']
        assert self.sample(12, RuntimeError('backend unavailable')) == []
        assert self.sample(13, True) == ['OFF', 'online']
        assert self.sample(14, True) == []
        assert self.sample(15, True) == ['ON', 'online']
        self.h.logger.warning.assert_called_once()

    def test_reconnect_forces_fresh_state(self):
        self.sample(0, False)
        self.audio.config()
        assert self.sample(10, False) == ['OFF', 'online']

    def test_missing_dependency_clears_whole_domain_and_stops_polling(self):
        self.h.backend.supported_features.return_value = set()
        self.sample(0, FileNotFoundError('pactl'))
        self.h.backend.read.reset_mock()
        assert self.sample(1, False) == []
        self.h.backend.read.assert_not_called()
        assert not self.audio.supported

    def test_temporary_capability_error_during_read_failure_is_contained(self):
        self.h.backend.supported_features.side_effect = OSError('probe failed')
        messages = self.sample(0, FileNotFoundError('temporary failure'))
        assert messages[-1] == 'offline'
        assert '' not in messages
        assert 'audio_playing' in self.audio.supported


class TestAudioCapabilities:
    def test_missing_tools_removes_all_audio_discovery(self, make_integration, monkeypatch):
        monkeypatch.setattr('pc2mqtt.integrations.linux.audio.shutil.which', lambda name: None)
        h = make_integration(Audio, backend=LinuxAudio())
        h.integration.config()
        messages = [call.kwargs for call in h.client.publish.call_args_list]
        assert len(messages) == 12
        assert all(message['payload'] == '' and message['retain'] for message in messages)
        h.client.reset_mock()
        h.integration.poll()
        h.client.publish.assert_not_called()

    def test_installing_dependency_restores_entities(self, make_integration, monkeypatch):
        monkeypatch.setattr('pc2mqtt.integrations.linux.audio.shutil.which', lambda name: None)
        h = make_integration(Audio, backend=LinuxAudio())
        h.integration.config()
        monkeypatch.setattr(
            'pc2mqtt.integrations.linux.audio.shutil.which',
            lambda name: '/bin/pactl' if name == 'pactl' else None
        )
        h.client.reset_mock()
        h.integration.config()
        configs = [
            json.loads(call.kwargs['payload'])
            for call in h.client.publish.call_args_list
            if call.kwargs['topic'].endswith('/config') and call.kwargs['payload']
        ]
        assert {config['name'] for config in configs} == {'Audio playing', 'Volume', 'Mute'}
        assert h.client.subscribe.call_count == 2


class TestPlayPause:
    @pytest.mark.parametrize('system', ['Linux', 'Windows'])
    def test_discovery_and_queued_button_press(self, make_integration, system):
        cls = next(cls for cls in integration_types(system) if cls.__name__ == 'Audio')
        h = make_integration(cls, capabilities={'play_pause'})
        h.integration.config()
        topic = 'homeassistant/button/pc/play_pause'
        config = next(
            json.loads(call.kwargs['payload'])
            for call in h.client.publish.call_args_list
            if call.kwargs['topic'] == f'{topic}/config'
        )
        assert config['name'] == 'Play / Pause'
        assert config['unique_id'] == 'computer_pc_play_pause'
        assert config['command_topic'] == f'{topic}/set'
        assert config['payload_press'] == 'PRESS'
        assert 'state_topic' not in config
        for payload, retain in [(b'PRESS', True), (b'ON', False), (b'\xff', False)]:
            h.integration.on_message(SimpleNamespace(
                topic=f'{topic}/set', payload=payload, retain=retain,
            ))
        h.integration.poll()
        h.backend.execute.assert_not_called()
        h.integration.on_message(SimpleNamespace(
            topic=f'{topic}/set', payload=b'PRESS', retain=False,
        ))
        h.backend.execute.assert_not_called()
        h.integration.poll()
        h.integration.poll()
        h.backend.execute.assert_called_once_with('play_pause', 'PRESS')

    @pytest.mark.parametrize('tools,expected', [
        (set(), set()),
        ({'playerctl'}, {'play_pause'}),
        ({'pactl'}, {'audio_playing', 'volume', 'mute'}),
        ({'playerctl', 'pactl'}, {'play_pause', 'audio_playing', 'volume', 'mute'}),
    ])
    def test_linux_capabilities_are_independent(self, tools, expected):
        with patch('shutil.which', side_effect=lambda name: name if name in tools else None):
            assert LinuxAudio().supported_features() == expected

    def test_linux_commands_and_missing_player_recovery(self, make_integration):
        with (
            patch('shutil.which', side_effect=lambda name: name if name == 'playerctl' else None),
            patch('pc2mqtt.integrations.linux.audio.run') as command,
        ):
            h = make_integration(Audio, backend=LinuxAudio())
            h.integration.config()
            h.client.reset_mock()
            command.side_effect = subprocess.CalledProcessError(1, 'playerctl')
            h.integration.poll()
            command.assert_called_once_with('playerctl', 'status')
            assert h.client.publish.call_args.kwargs['payload'] == 'offline'
            assert 'play_pause' in h.integration.supported
            assert not any(c.kwargs['topic'].endswith('/config') for c in h.client.publish.call_args_list)
            command.side_effect = None
            command.return_value = 'Paused'
            h.clock.return_value = 10
            h.integration.poll()
            assert h.client.publish.call_args.kwargs['payload'] == 'online'
            h.backend.execute('play_pause', 'PRESS')
            command.assert_called_with('playerctl', 'play-pause')
            command.side_effect = subprocess.CalledProcessError(1, 'playerctl')
            h.integration.on_message(SimpleNamespace(
                topic='homeassistant/button/pc/play_pause/set', payload=b'PRESS', retain=False,
            ))
            h.integration.poll()
            assert h.client.publish.call_args.kwargs['payload'] == 'offline'
