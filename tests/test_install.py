"""Exercise Linux installer control flow without installing packages or services."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / 'scripts/install.sh'


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('bash'), 'Linux Bash installer')
class LinuxInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
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
    print('Linux' if args == ['-s'] else 'x86_64')
elif name == 'id':
    print(os.environ.get('INSTALL_TEST_UID', '1000'))
elif name == 'dpkg':
    print('amd64')
elif name == 'curl':
    if os.environ.get('INSTALL_TEST_FAILURE') == 'download':
        sys.exit(22)
    pathlib.Path(args[args.index('--output') + 1]).write_text('fake package')
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

    def run_installer(self, inputs='broker.local\n\n', **overrides):
        return subprocess.run(
            ['bash', str(INSTALLER)], input=inputs, text=True,
            capture_output=True, env={**self.env, **overrides}, timeout=10,
        )

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_default_port_startup_and_rerun(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        service = self.service.read_text()
        self.assertIn('ExecStart=/usr/bin/pc2mqtt --host broker.local --port 1883', service)
        self.assertIn('Restart=always', service)
        commands = self.commands()
        self.assertIn(['systemctl', '--user', 'enable', 'pc2mqtt.service'], commands)
        self.assertEqual(commands[-1], ['systemctl', '--user', 'restart', 'pc2mqtt.service'])
        install = next(c for c in commands if c[:3] == ['sudo', 'apt-get', 'install'])
        self.assertIn('pulseaudio-utils', install)
        self.assertFalse(Path(install[-2]).exists(), 'Temporary package should be cleaned up')
        result = self.run_installer('::1\n2883\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--host ::1 --port 2883', self.service.read_text())
        self.assertEqual(len(list(self.service.parent.glob('*.service'))), 1)

    def test_invalid_host_and_port_are_reprompted(self):
        result = self.run_installer('\nbroker;touch /tmp/injected\n--help\nbroker\n0\n65536\nno\n01883\n')
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
