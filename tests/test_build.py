"""Release tags determine package metadata, asset names, and installer downloads."""

import ast
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import build


class TestReleaseBuild:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        self.root = tmp_path
        (self.root / 'scripts').mkdir()
        for extension in ('sh', 'ps1'):
            shutil.copy2(
                build.ROOT / f'scripts/install.{extension}',
                self.root / f'scripts/install.{extension}',
            )
        (self.root / 'pyproject.toml').write_text('[tool.poetry]\nversion = "0.1.1"\n')
        monkeypatch.setattr(build, 'ROOT', self.root)

    def fake_run(self, command, **kwargs):
        if 'PyInstaller' in command:
            (self.root / 'dist').mkdir(exist_ok=True)
            for name in ('pc2mqtt', 'pc2mqtt.exe'):
                (self.root / f'dist/{name}').write_bytes(b'fake executable')
        return subprocess.CompletedProcess(command, 0)

    def test_tag_versions_and_prerelease_ordering(self):
        assert build.release_versions('v1.2.3') == ('1.2.3', '1.2.3', (1, 2, 3, 0))
        assert build.release_versions('1.2.3-rc.1+build.2') == (
            '1.2.3-rc.1+build.2',
            '1.2.3~rc.1+build.2',
            (1, 2, 3, 0),
        )
        if shutil.which('dpkg'):
            subprocess.run(['dpkg', '--compare-versions', '1.2.3~rc.1', 'lt', '1.2.3'], check=True)

    def test_invalid_tags_fail_before_building(self):
        for tag in (
            '',
            'latest',
            'v1.2',
            'v01.2.3',
            '../v1.2.3',
            'v1.2.3;echo bad',
            'v1.2.3\n',
            'v65536.0.0',
        ):
            with patch.object(build.subprocess, 'run') as run:
                with pytest.raises(ValueError):
                    build.build('exe', tag)
                run.assert_not_called()

    def test_windows_metadata_and_installer_use_tag(self):
        with (
            patch.object(build.platform, 'system', return_value='Windows'),
            patch.object(build.platform, 'machine', return_value='AMD64'),
            patch.object(build.os, 'chdir'),
            patch.object(build.subprocess, 'run', side_effect=self.fake_run) as run,
        ):
            asset = build.build('exe', 'v2.3.4-rc.1')
        assert asset.name == 'pc2mqtt-v2.3.4-rc.1-windows-x64.exe'
        assert asset.exists()
        command = run.call_args_list[0].args[0]
        assert '--windowed' in command
        assert command[command.index('--exclude-module') + 1] == 'pc2mqtt.integrations.linux'
        assert 'pystray._win32' in command
        metadata = Path(command[command.index('--version-file') + 1]).read_text()
        ast.parse(metadata)
        assert "StringStruct('ProductVersion', '2.3.4-rc.1')" in metadata
        assert 'filevers=(2, 3, 4, 0)' in metadata
        installer = (self.root / 'dist/install-v2.3.4-rc.1.ps1').read_text()
        assert "$releaseTag = 'v2.3.4-rc.1'" in installer
        assert '/latest/' not in installer
        assert '@RELEASE_TAG@' not in installer

    def test_debian_metadata_uses_tag_instead_of_project_version(self):
        with (
            patch.object(build.platform, 'system', return_value='Linux'),
            patch.object(build.platform, 'machine', return_value='x86_64'),
            patch.object(build.shutil, 'which', return_value='/usr/bin/tool'),
            patch.object(build.os, 'confstr', return_value='glibc 2.35', create=True),
            patch.object(build.os, 'chdir'),
            patch.object(build.subprocess, 'run', side_effect=self.fake_run) as run,
        ):
            asset = build.build('deb', 'v2.3.4-rc.1')
        command = run.call_args_list[0].args[0]
        assert command[command.index('--exclude-module') + 1] == 'pc2mqtt.integrations.windows'
        assert asset.name == 'pc2mqtt-v2.3.4-rc.1-linux-amd64.deb'
        control = (self.root / 'build/deb-root/DEBIAN/control').read_text()
        assert 'Version: 2.3.4~rc.1\n' in control
        installer = (self.root / 'dist/install-v2.3.4-rc.1.sh').read_text()
        assert "release_tag='v2.3.4-rc.1'" in installer
        assert '/latest/' not in installer

    def test_local_build_defaults_to_project_version(self):
        with (
            patch.object(build.platform, 'system', return_value='Windows'),
            patch.object(build.platform, 'machine', return_value='AMD64'),
            patch.object(build.os, 'chdir'),
            patch.object(build.subprocess, 'run', side_effect=self.fake_run),
        ):
            asset = build.build('exe')
        assert asset.name == 'pc2mqtt-v0.1.1-windows-x64.exe'

    @pytest.mark.skipif(
        not (sys.platform.startswith('linux') and shutil.which('dpkg-deb')),
        reason='Debian packaging tools',
    )
    def test_real_debian_package_metadata(self):
        real_run = subprocess.run

        def compile_stub_or_run(command, **kwargs):
            if 'PyInstaller' in command:
                (self.root / 'dist').mkdir()
                executable = self.root / 'dist/pc2mqtt'
                executable.write_text('#!/bin/sh\nexit 0\n')
                executable.chmod(0o755)
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, capture_output=True, **kwargs)

        with (
            patch.object(build.platform, 'machine', return_value='x86_64'),
            patch.object(build.shutil, 'which', return_value='/usr/bin/tool'),
            patch.object(build.os, 'chdir'),
            patch.object(build.subprocess, 'run', side_effect=compile_stub_or_run),
        ):
            asset = build.build('deb', 'v2.3.4-rc.1')
        result = real_run(
            ['dpkg-deb', '--field', str(asset), 'Version'],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == '2.3.4~rc.1'
        assert asset.is_file()
