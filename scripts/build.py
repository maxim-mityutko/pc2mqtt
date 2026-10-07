"""Native release builds shared by Make and GitHub Actions."""

import argparse
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def release_versions(tag):
    """Validate a tag before using it in filenames, URLs, or installer source."""
    match = re.fullmatch(
        r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:-([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?"
        r"(?:\+([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?", tag,
    )
    if match is None:
        raise ValueError("Release tag must be [v]MAJOR.MINOR.PATCH, optionally with -rc.1 and +build.1 suffixes.")
    numeric = tuple(int(match[i]) for i in (1, 2, 3)) + (0,)
    if any(part > 65535 for part in numeric):
        raise ValueError("Version components must fit Windows version fields (0–65535).")
    version = tag.removeprefix("v")
    deb_version = version.replace("-", "~", 1)
    return version, deb_version, numeric


def render_installer(kind, tag):
    release_versions(tag)
    extension = "ps1" if kind == "exe" else "sh"
    source = (ROOT / f"scripts/install.{extension}").read_text()
    installer = ROOT / f"dist/install-{tag}.{extension}"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_text(source.replace("@RELEASE_TAG@", tag), encoding="utf-8", newline="\n")
    return installer


def windows_version_file(version, numeric):
    path = ROOT / "build/windows-version.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""VSVersionInfo(
    ffi=FixedFileInfo(filevers={numeric!r}, prodvers={numeric!r},
                     mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('FileDescription', 'PC controls and sensors over MQTT'),
        StringStruct('FileVersion', {version!r}),
        StringStruct('ProductName', 'pc2mqtt'),
        StringStruct('ProductVersion', {version!r}),
        StringStruct('OriginalFilename', 'pc2mqtt.exe')
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""", encoding="utf-8")
    return path


def build(kind, tag=None):
    if tag is None:
        tag = "v" + tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["poetry"]["version"]
    version, deb_version, numeric = release_versions(tag)
    required_system = "Windows" if kind == "exe" else "Linux"
    if platform.system() != required_system:
        raise SystemExit(f"Build {kind} on {required_system}; cross-compilation is not supported.")
    if platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("These release packages require an x64 build host.")
    if kind == "deb":
        for tool in ("objdump", "objcopy", "dpkg-deb"):
            if shutil.which(tool) is None:
                raise SystemExit(f"Missing {tool}: install binutils and dpkg-dev before building.")

    os.chdir(ROOT)
    version_options = []
    if kind == "exe":
        version_options = [
            "--windowed", "--hidden-import", "pystray._win32",
            "--version-file", str(windows_version_file(version, numeric)),
        ]
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
        "--onefile", "--name", "pc2mqtt", "--paths", ".", *version_options, "pc2mqtt/app.py",
    ], check=True)
    executable = ROOT / "dist" / ("pc2mqtt.exe" if kind == "exe" else "pc2mqtt")
    subprocess.run([str(executable), "--help"], check=True)
    if kind == "exe":
        asset = ROOT / f"dist/pc2mqtt-{tag}-windows-x64.exe"
        executable.replace(asset)
        render_installer(kind, tag)
        return asset

    package_root = ROOT / "build/deb-root"
    if package_root.exists():
        shutil.rmtree(package_root)
    (package_root / "DEBIAN").mkdir(parents=True)
    bin_dir = package_root / "usr/bin"
    bin_dir.mkdir(parents=True)
    shutil.copy2(executable, bin_dir / "pc2mqtt")
    (bin_dir / "pc2mqtt").chmod(0o755)
    # A locally built executable requires at least the build host's glibc.
    libc_version = os.confstr("CS_GNU_LIBC_VERSION").split()[1]
    control = (
        "Package: pc2mqtt\n"
        f"Version: {deb_version}\n"
        "Architecture: amd64\n"
        "Maintainer: pc2mqtt contributors\n"
        "Section: utils\n"
        "Priority: optional\n"
        f"Depends: libc6 (>= {libc_version}), zlib1g\n"
        "Recommends: pulseaudio-utils, systemd-sysv\n"
        "Description: Expose computer controls and audio playback through MQTT\n"
    )
    (package_root / "DEBIAN/control").write_text(control)
    asset = ROOT / f"dist/pc2mqtt-{tag}-linux-amd64.deb"
    subprocess.run([
        "dpkg-deb", "--build", "--root-owner-group", str(package_root), str(asset),
    ], check=True)
    subprocess.run(["dpkg-deb", "--info", str(asset)], check=True)
    render_installer(kind, tag)
    return asset


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("exe", "deb"))
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG") or None,
                        help="Release tag (defaults to RELEASE_TAG, then the project version)")
    args = parser.parse_args()
    print(f"Built {build(args.kind, args.tag)}")
