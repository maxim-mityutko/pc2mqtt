import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pc2mqtt.integrations.linux.power import Backend as LinuxPower
from pc2mqtt.integrations.linux.power import Power
from pc2mqtt.integrations.windows.power import Backend as WindowsPower


class TestPower:
    @pytest.fixture(autouse=True)
    def setup(self, make_integration, monkeypatch):
        self.process = Mock()
        self.process.poll.return_value = None
        self.launch = Mock(return_value=self.process)
        self.backend = LinuxPower(runner=self.launch)
        monkeypatch.setattr(
            self.backend, 'supported_features', lambda: {'shutdown', 'sleep', 'restart'}
        )
        self.h = make_integration(Power, backend=self.backend)
        self.power = self.h.integration
        self.power.config()
        self.h.client.reset_mock()

    def message(self, action, payload=b'PRESS', retain=False):
        return SimpleNamespace(
            topic=f'{self.power.topics[action]}/set', payload=payload, retain=retain
        )

    def test_discovery_and_resubscription(self):
        for _ in range(2):
            self.h.client.reset_mock()
            self.power.config()
            messages = {
                call.kwargs['topic']: call.kwargs['payload']
                for call in self.h.client.publish.call_args_list
            }
            assert messages['homeassistant/switch/pc/config'] == ''
            configs = [
                json.loads(value)
                for topic, value in messages.items()
                if topic.endswith('/config') and value
            ]
            assert {config['name'] for config in configs} == {'Shutdown', 'Sleep', 'Restart'}
            assert len({config['unique_id'] for config in configs}) == 3
            for config in configs:
                assert config['availability_topic'] == 'connection'
                assert config['payload_press'] == 'PRESS'
                assert 'state_topic' not in config
                self.h.client.subscribe.assert_any_call(topic=config['command_topic'])
            assert self.h.client.subscribe.call_count == 3

    @pytest.mark.parametrize('action', ['shutdown', 'sleep', 'restart'])
    def test_commands_are_queued_and_launched_once(self, action):
        assert self.power.on_message(self.message(action))
        self.launch.assert_not_called()
        self.power.poll()
        self.launch.assert_called_once_with(self.backend.command(action))
        self.power.on_message(self.message(action))
        self.power.poll()
        self.launch.assert_called_once()

    @pytest.mark.parametrize(
        'payload,retain', [(b'PRESS', True), (b'OFF', False), (b'invalid', False), (b'\xff', False)]
    )
    def test_invalid_or_retained_commands_do_not_execute(self, payload, retain):
        for action in ('shutdown', 'sleep', 'restart'):
            assert self.power.on_message(self.message(action, payload, retain))
        self.power.poll()
        self.launch.assert_not_called()

    def test_unknown_and_legacy_topics_are_not_handled(self):
        for topic in ('unknown', 'homeassistant/switch/pc/set'):
            assert not self.power.on_message(
                SimpleNamespace(topic=topic, payload=b'OFF', retain=False)
            )

    def test_pending_commands_and_terminal_actions_are_not_duplicated(self):
        for _ in range(3):
            self.power.on_message(self.message('restart'))
        self.power.poll()
        self.process.poll.return_value = 0
        self.power.poll()
        self.power.on_message(self.message('restart'))
        self.power.poll()
        self.launch.assert_called_once()

    def test_sleep_completion_allows_another_command(self):
        self.power.on_message(self.message('sleep'))
        self.power.poll()
        self.process.poll.return_value = 0
        self.power.poll()
        self.power.on_message(self.message('restart'))
        self.power.poll()
        assert self.launch.call_count == 2

    def test_nonzero_exit_and_launch_failure_allow_retry(self):
        self.power.on_message(self.message('restart'))
        self.power.poll()
        self.process.poll.return_value = 1
        self.power.poll()
        self.launch.side_effect = FileNotFoundError('command missing')
        self.power.on_message(self.message('sleep'))
        self.power.poll()
        assert self.h.logger.error.call_count == 2
        self.launch.side_effect = None
        self.power.on_message(self.message('sleep'))
        self.power.poll()
        assert self.launch.call_count == 3

    @pytest.mark.parametrize(
        'action,linux,windows',
        [
            ('shutdown', ['shutdown', 'now'], ['shutdown', '/s', '/t', '0']),
            ('restart', ['shutdown', '-r', 'now'], ['shutdown', '/r', '/t', '0']),
            ('sleep', ['systemctl', 'suspend'], None),
        ],
    )
    def test_platform_commands(self, action, linux, windows):
        assert LinuxPower().command(action) == linux
        if windows:
            assert WindowsPower().command(action) == windows
        else:
            sleep = WindowsPower().command(action)
            assert sleep[:4] == ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command']
            assert 'PowerState]::Suspend, $false, $false' in sleep[-1]
            assert 'exit 1' in sleep[-1]
