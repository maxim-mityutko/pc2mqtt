"""Exercise Linux installer control flow without installing packages or services."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import build


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('bash'), 'Linux Bash installer')
class LinuxInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
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
        stub = f'#!{sys.executable}\n' + '''import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['INSTALL_TEST_LOG'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\\n')
if name == 'uname':
    print({'-s': 'Linux', '-m': 'x86_64', '-n': 'test-desktop'}[args[0]])
elif name == 'id':
    print(os.environ.get('INSTALL_TEST_UID', '1000'))
elif name == 'dpkg':
    print('amd64')
elif name == 'curl':
    if os.environ.get('INSTALL_TEST_FAILURE') == 'download':
        sys.exit(22)
    pathlib.Path(args[args.index('--output') + 1]).write_text('fake package')
elif name == 'systemctl' and 'is-active' in args:
    sys.exit(0 if os.environ.get('INSTALL_TEST_ACTIVE') == '1' else 3)
elif name == 'systemctl' and 'stop' in args and os.environ.get('INSTALL_TEST_FAILURE') == 'stop':
    sys.exit(1)
elif name == 'sudo' and os.environ.get('INSTALL_TEST_FAILURE') == 'install' and 'install' in args:
    sys.exit(100)
elif name == 'sudo' and os.environ.get('INSTALL_TEST_FAILURE') == 'package':
    sys.exit(100)
elif name == 'systemctl' and os.environ.get('INSTALL_TEST_FAILURE') == 'session':
    sys.exit(1)
'''
        for name in ('uname', 'id', 'dpkg', 'curl', 'sudo', 'apt-get', 'systemctl'):
            path = self.bin / name
            path.write_text(stub)
            path.chmod(0o755)

    @property
    def service(self):
        return self.config / 'systemd/user/pc2mqtt.service'

    def run_installer(self, inputs='broker.local\n\n\n\n', **overrides):
        return subprocess.run(
            ['bash', str(self.installer)], input=inputs, text=True,
            capture_output=True, env={**self.env, **overrides}, timeout=10,
        )

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_default_port_startup_and_rerun(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        service = self.service.read_text()
        self.assertIn('ExecStart=/usr/bin/pc2mqtt --host broker.local --port 1883', service)
        self.assertIn('--keepalive 60\n', service)
        self.assertNotIn('--display-name', service)
        self.assertIn('Restart=always', service)
        commands = self.commands()
        download = next(c for c in commands if c[0] == 'curl')
        self.assertIn('https://github.com/maxim-mityutko/pc2mqtt/releases/download/v2.3.4/pc2mqtt-v2.3.4-linux-amd64.deb', download)
        self.assertIn(['systemctl', '--user', 'enable', 'pc2mqtt.service'], commands)
        self.assertEqual(commands[-1], ['systemctl', '--user', 'restart', 'pc2mqtt.service'])
        install = next(c for c in commands if c[:3] == ['sudo', 'apt-get', 'install'])
        self.assertIn('pulseaudio-utils', install)
        self.assertFalse(Path(install[-2]).exists(), 'Temporary package should be cleaned up')
        result = self.run_installer('::1\n2883\n\n\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--host ::1 --port 2883', self.service.read_text())
        self.assertEqual(len(list(self.service.parent.glob('*.service'))), 1)

    def test_invalid_host_and_port_are_reprompted(self):
        result = self.run_installer('\nbroker;touch /tmp/injected\n--help\nbroker\n0\n65536\nno\n01883\n\n\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--host broker --port 1883', self.service.read_text())
        self.assertNotIn('injected', self.service.read_text())

    def test_failures_do_not_register_startup(self):
        for failure in ('download', 'package', 'session'):
            with self.subTest(failure=failure):
                result = self.run_installer(INSTALL_TEST_FAILURE=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.service.exists())
                self.assertNotIn(['systemctl', '--user', 'enable', 'pc2mqtt.service'], self.commands())
        # Download/session failures must not invoke the package manager.
        commands = self.commands()
        self.assertEqual(sum(c[0] == 'sudo' for c in commands), 1)

    def test_root_and_missing_input_stop_before_download(self):
        for inputs, env in [('broker\n\n', {'INSTALL_TEST_UID': '0'}), ('', {})]:
            with self.subTest(inputs=inputs, env=env):
                result = self.run_installer(inputs, **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.service.exists())
        self.assertFalse(any(c[0] == 'curl' for c in self.commands()))

    def test_running_service_stops_before_package_replacement(self):
        result = self.run_installer(INSTALL_TEST_ACTIVE='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        download = next(i for i, command in enumerate(commands) if command[0] == 'curl')
        stop = commands.index(['systemctl', '--user', 'stop', 'pc2mqtt.service'])
        install = next(i for i, command in enumerate(commands) if command[:3] == ['sudo', 'apt-get', 'install'])
        restart = commands.index(['systemctl', '--user', 'restart', 'pc2mqtt.service'])
        self.assertLess(download, stop)
        self.assertLess(stop, install)
        self.assertLess(install, restart)

    def test_failed_install_attempts_to_restore_running_service(self):
        result = self.run_installer(INSTALL_TEST_ACTIVE='1', INSTALL_TEST_FAILURE='install')
        self.assertEqual(result.returncode, 100)
        commands = self.commands()
        self.assertIn(['systemctl', '--user', 'stop', 'pc2mqtt.service'], commands)
        self.assertEqual(commands[-1], ['systemctl', '--user', 'start', 'pc2mqtt.service'])
        self.assertFalse(self.service.exists())

    def test_stop_failure_prevents_package_replacement(self):
        result = self.run_installer(INSTALL_TEST_ACTIVE='1', INSTALL_TEST_FAILURE='stop')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:3] == ['sudo', 'apt-get', 'install'] for c in self.commands()))

    def test_download_failure_leaves_running_service_alone(self):
        result = self.run_installer(INSTALL_TEST_ACTIVE='1', INSTALL_TEST_FAILURE='download')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in c or 'restart' in c for c in self.commands()))

    def test_custom_display_name_and_keepalive(self):
        result = self.run_installer('broker\n\n  Living Room  \n120\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--keepalive 120 "--display-name=Living Room"\n', self.service.read_text())

    def test_keepalive_validation_and_zero(self):
        result = self.run_installer('broker\n\n\n-1\n65536\nabc\n00000\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--keepalive 0\n', self.service.read_text())

    def test_display_name_special_characters_are_literal(self):
        result = self.run_installer('broker\n\nDesk "A" %h ${USER} \\ end\n\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(r'"--display-name=Desk \"A\" %%h $${USER} \\ end"', self.service.read_text())
