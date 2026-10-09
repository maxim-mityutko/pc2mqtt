"""Exercise Linux installer control flow without installing packages or services."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import build


@pytest.mark.skipif(
    not (sys.platform.startswith('linux') and shutil.which('bash')), reason='Linux Bash installer'
)
class TestLinuxInstaller:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        self.root = tmp_path
        (self.root / 'scripts').mkdir()
        shutil.copy2(build.ROOT / 'scripts/install.sh', self.root / 'scripts/install.sh')
        with patch.object(build, 'ROOT', self.root):
            self.installer = build.render_installer('deb', 'v2.3.4')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.config = self.root / 'config with spaces'
        self.log = self.root / 'commands.jsonl'
        self.env = {
            **os.environ,
            'PATH': f'{self.bin}:{os.environ["PATH"]}',
            'XDG_CONFIG_HOME': str(self.config),
            'INSTALL_TEST_LOG': str(self.log),
        }
        stub = (
            f'#!{sys.executable}\n'
            + "import json, os, pathlib, sys\nname = pathlib.Path(sys.argv[0]).name\nargs = sys.argv[1:]\nwith open(os.environ['INSTALL_TEST_LOG'], 'a') as log:\n    log.write(json.dumps([name, *args]) + '\\n')\nif name == 'uname':\n    print({'-s': 'Linux', '-m': 'x86_64', '-n': 'test-desktop'}[args[0]])\nelif name == 'id':\n    print(os.environ.get('INSTALL_TEST_UID', '1000'))\nelif name == 'dpkg':\n    print('amd64')\nelif name == 'curl':\n    if os.environ.get('INSTALL_TEST_FAILURE') == 'download':\n        sys.exit(22)\n    pathlib.Path(args[args.index('--output') + 1]).write_text('fake package')\nelif name == 'systemctl' and 'is-active' in args:\n    sys.exit(0 if os.environ.get('INSTALL_TEST_ACTIVE') == '1' else 3)\nelif name == 'systemctl' and 'stop' in args and os.environ.get('INSTALL_TEST_FAILURE') == 'stop':\n    sys.exit(1)\nelif name == 'sudo' and os.environ.get('INSTALL_TEST_FAILURE') == 'install' and 'install' in args:\n    sys.exit(100)\nelif name == 'sudo' and os.environ.get('INSTALL_TEST_FAILURE') == 'package':\n    sys.exit(100)\nelif name == 'systemctl' and os.environ.get('INSTALL_TEST_FAILURE') == 'session':\n    sys.exit(1)\n"
        )
        for name in ('uname', 'id', 'dpkg', 'curl', 'sudo', 'apt-get', 'systemctl'):
            path = self.bin / name
            path.write_text(stub)
            path.chmod(0o755)

    @property
    def service(self):
        return self.config / 'systemd/user/pc2mqtt.service'

    def run_installer(self, inputs='broker.local\n\n\n\n', **overrides):
        return subprocess.run(
            ['bash', str(self.installer)],
            input=inputs,
            text=True,
            capture_output=True,
            env={**self.env, **overrides},
            timeout=10,
        )

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_default_port_startup_and_rerun(self):
        result = self.run_installer()
        assert result.returncode == 0, result.stderr
        service = self.service.read_text()
        assert 'ExecStart=/usr/bin/pc2mqtt --host broker.local --port 1883' in service
        assert '--keepalive 60\n' in service
        assert '--display-name' not in service
        assert 'Restart=always' in service
        commands = self.commands()
        download = next((c for c in commands if c[0] == 'curl'))
        assert (
            'https://github.com/maxim-mityutko/pc2mqtt/releases/download/v2.3.4/pc2mqtt-v2.3.4-linux-amd64.deb'
            in download
        )
        assert ['systemctl', '--user', 'enable', 'pc2mqtt.service'] in commands
        assert commands[-1] == ['systemctl', '--user', 'restart', 'pc2mqtt.service']
        install = next((c for c in commands if c[:3] == ['sudo', 'apt-get', 'install']))
        assert 'pulseaudio-utils' in install
        assert 'playerctl' in install
        assert not Path(next((arg for arg in install if arg.endswith('.deb')))).exists(), (
            'Temporary package should be cleaned up'
        )
        result = self.run_installer('::1\n2883\n\n\n')
        assert result.returncode == 0, result.stderr
        assert '--host ::1 --port 2883' in self.service.read_text()
        assert len(list(self.service.parent.glob('*.service'))) == 1

    def test_invalid_host_and_port_are_reprompted(self):
        result = self.run_installer(
            '\nbroker;touch /tmp/injected\n--help\nbroker\n0\n65536\nno\n01883\n\n\n'
        )
        assert result.returncode == 0, result.stderr
        assert '--host broker --port 1883' in self.service.read_text()
        assert 'injected' not in self.service.read_text()

    def test_failures_do_not_register_startup(self):
        for failure in ('download', 'package', 'session'):
            result = self.run_installer(INSTALL_TEST_FAILURE=failure)
            assert result.returncode != 0
            assert not self.service.exists()
            assert ['systemctl', '--user', 'enable', 'pc2mqtt.service'] not in self.commands()
        commands = self.commands()
        assert sum((c[0] == 'sudo' for c in commands)) == 1

    def test_root_and_missing_input_stop_before_download(self):
        for inputs, env in [('broker\n\n', {'INSTALL_TEST_UID': '0'}), ('', {})]:
            result = self.run_installer(inputs, **env)
            assert result.returncode != 0
            assert not self.service.exists()
        assert not any((c[0] == 'curl' for c in self.commands()))

    def test_running_service_stops_before_package_replacement(self):
        result = self.run_installer(INSTALL_TEST_ACTIVE='1')
        assert result.returncode == 0, result.stderr
        commands = self.commands()
        download = next((i for i, command in enumerate(commands) if command[0] == 'curl'))
        stop = commands.index(['systemctl', '--user', 'stop', 'pc2mqtt.service'])
        install = next(
            (
                i
                for i, command in enumerate(commands)
                if command[:3] == ['sudo', 'apt-get', 'install']
            )
        )
        restart = commands.index(['systemctl', '--user', 'restart', 'pc2mqtt.service'])
        assert download < stop
        assert stop < install
        assert install < restart

    def test_failed_install_attempts_to_restore_running_service(self):
        result = self.run_installer(INSTALL_TEST_ACTIVE='1', INSTALL_TEST_FAILURE='install')
        assert result.returncode == 100
        commands = self.commands()
        assert ['systemctl', '--user', 'stop', 'pc2mqtt.service'] in commands
        assert commands[-1] == ['systemctl', '--user', 'start', 'pc2mqtt.service']
        assert not self.service.exists()

    def test_stop_failure_prevents_package_replacement(self):
        result = self.run_installer(INSTALL_TEST_ACTIVE='1', INSTALL_TEST_FAILURE='stop')
        assert result.returncode != 0
        assert not any((c[:3] == ['sudo', 'apt-get', 'install'] for c in self.commands()))

    def test_download_failure_leaves_running_service_alone(self):
        result = self.run_installer(INSTALL_TEST_ACTIVE='1', INSTALL_TEST_FAILURE='download')
        assert result.returncode != 0
        assert not any(('stop' in c or 'restart' in c for c in self.commands()))

    def test_custom_display_name_and_keepalive(self):
        result = self.run_installer('broker\n\n  Living Room  \n120\n')
        assert result.returncode == 0, result.stderr
        assert '--keepalive 120 "--display-name=Living Room"\n' in self.service.read_text()

    def test_keepalive_validation_and_zero(self):
        result = self.run_installer('broker\n\n\n-1\n65536\nabc\n00000\n')
        assert result.returncode == 0, result.stderr
        assert '--keepalive 0\n' in self.service.read_text()

    def test_display_name_special_characters_are_literal(self):
        result = self.run_installer('broker\n\nDesk "A" %h ${USER} \\ end\n\n')
        assert result.returncode == 0, result.stderr
        assert '"--display-name=Desk \\"A\\" %%h $${USER} \\\\ end"' in self.service.read_text()
