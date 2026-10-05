"""Native release builds shared by Make and GitHub Actions."""

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def build(kind):
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
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
        "--onefile", "--name", "pc2mqtt", "--paths", ".", "pc2mqtt/app.py",
    ], check=True)
    executable = ROOT / "dist" / ("pc2mqtt.exe" if kind == "exe" else "pc2mqtt")
    subprocess.run([str(executable), "--help"], check=True)
    if kind == "exe":
        asset = ROOT / "dist/pc2mqtt-windows-x64.exe"
        executable.replace(asset)
        return asset

    package_root = ROOT / "build/deb-root"
    if package_root.exists():
        shutil.rmtree(package_root)
    (package_root / "DEBIAN").mkdir(parents=True)
    bin_dir = package_root / "usr/bin"
    bin_dir.mkdir(parents=True)
    shutil.copy2(executable, bin_dir / "pc2mqtt")
    (bin_dir / "pc2mqtt").chmod(0o755)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["poetry"]["version"]
    # A locally built executable requires at least the build host's glibc.
    libc_version = os.confstr("CS_GNU_LIBC_VERSION").split()[1]
    control = (
        "Package: pc2mqtt\n"
        f"Version: {version}\n"
        "Architecture: amd64\n"
        "Maintainer: pc2mqtt contributors\n"
        "Section: utils\n"
        "Priority: optional\n"
        f"Depends: libc6 (>= {libc_version}), zlib1g\n"
        "Recommends: pulseaudio-utils, systemd-sysv\n"
        "Description: Expose computer controls and audio playback through MQTT\n"
    )
    (package_root / "DEBIAN/control").write_text(control)
    asset = ROOT / "dist/pc2mqtt-linux-amd64.deb"
    subprocess.run([
        "dpkg-deb", "--build", "--root-owner-group", str(package_root), str(asset),
    ], check=True)
    subprocess.run(["dpkg-deb", "--info", str(asset)], check=True)
    return asset


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("exe", "deb"))
    args = parser.parse_args()
    print(f"Built {build(args.kind)}")
