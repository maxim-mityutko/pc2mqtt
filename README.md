# pc2mqtt

Expose actions and sensors from Windows or Linux computer via MQTT.

## Command Line Arguments

- `--host` - ip or hostname of MQTT broker
- `--display-name` - computer display name suffix (defaults to the hostname)
- `--port` - port of the MQTT broker (default: 1883)
- `--keepalive` - interval to send keepalive messages (default: 60)
- `--tray` - Windows tray mode (default in the packaged Windows executable)

For example, `pc2mqtt --host broker.local --display-name foo` displays **Computer foo**.
Names preserve your capitalization; quote names containing spaces, such as
`--display-name "Living Room"`. Without `--display-name`, the existing **Computer HOSTNAME** name
is used. MQTT topics, device identifiers, and entity IDs remain tied to the real
hostname, so changing the display name does not create new entities.
For automatic startup, add `--display-name foo` to the Windows shortcut arguments or the
Linux user service's `ExecStart` line; after editing the Linux service, run
`systemctl --user daemon-reload` and `systemctl --user restart pc2mqtt`.

## Actions and Sensors

- Shutdown
- Restart
- Sleep
- Audio playing
- IP address
- Last seen

## MQTT retention and status sensors

An MQTT 5 broker is required. All published discovery, state, and availability
messages are retained with a 12-hour (43,200-second) message expiry, including the
last will. Each publication resets that topic's expiry. Discovery is refreshed
every six hours and connection availability every minute while connected.
The broker removes expired retained messages; this does not delete Home Assistant
history. Audio still has its separate 90-second Home Assistant state expiry.

Status sensors update on connection and every minute:

- **IP address** reports the local IPv4 or IPv6 address used to connect to
  the MQTT broker. With a broker on the same machine, this may be loopback.
- **Last seen** reports the UTC timestamp of the latest heartbeat. It remains
  readable when the computer goes offline, until the retained message expires.

Topics are `homeassistant/sensor/<node>/ip_address/state` and
`homeassistant/sensor/<node>/last_seen/state`. Both sensors are discovered on the
existing computer device. See [status sensors](pc2mqtt/integrations/status.py).

## Installation

Run one of these commands as your normal desktop user. The command selects the
latest stable release and downloads its versioned installer. The installer asks
for your MQTT hostname or IP address and port (press Enter for **1883**), launches the app, and enables startup at login for your user.
An MQTT 5 broker is required. Enter just the hostname or IP, without `mqtt://`.

### Windows x64

Paste into **PowerShell** (no administrator privileges required):

```powershell
& { $ErrorActionPreference = 'Stop'; $release = Invoke-RestMethod 'https://api.github.com/repos/maxim-mityutko/pc2mqtt/releases/latest'; $tag = $release.tag_name; $installer = Join-Path $env:TEMP ('pc2mqtt-' + [guid]::NewGuid() + '.ps1'); try { Invoke-WebRequest -UseBasicParsing "https://github.com/maxim-mityutko/pc2mqtt/releases/download/$tag/install-$tag.ps1" -OutFile $installer; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer } finally { Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue } }
```

Installs to `%LOCALAPPDATA%\pc2mqtt` and creates a `pc2mqtt.lnk` startup shortcut
in your Startup folder (`Win + R`, then `shell:startup`). Rerun the one-liner to update the app
or change the broker settings; after downloading, the installer stops its running
copy, waits for it to exit, replaces the executable, and starts the new version. Remove
that shortcut to disable automatic startup. Choose **Quit** in the tray menu and delete its install
folder to uninstall. Remove any older manually created startup entry first to
avoid running two copies. See [the Windows installer](scripts/install.ps1).

The app runs in your desktop session with a system tray icon and no console
window. Right-click the icon for MQTT connection status, **Open log**, and **Quit**.
If Windows hides the icon, look in the tray's overflow menu. The app retries the
connection when the broker is unavailable, including at login. Logs are kept in
`%LOCALAPPDATA%\pc2mqtt\pc2mqtt.log` with two rotated backups (1 MiB each).
Quit publishes an offline state before disconnecting when the broker is reachable.

Windows tray mode keeps audio detection in the logged-in user's session. A
conventional Windows service runs in a separate session, even under a local user
account; see Microsoft's [service session documentation](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services).
When running from source, add `--tray` to enable the icon. Linux continues to use
the systemd user service below.

### Linux x64 (Debian/Ubuntu)

Requires Bash, `curl`, a systemd user session, and glibc 2.35 or newer (for example,
Ubuntu 22.04+). Run from a terminal in your desktop session, **without sudo**:

```bash
bash -c 'release=$(curl -fsSL -o /dev/null -w "%{url_effective}" https://github.com/maxim-mityutko/pc2mqtt/releases/latest) && tag=${release##*/} && installer=$(mktemp) && curl -fsSL --retry 3 "https://github.com/maxim-mityutko/pc2mqtt/releases/download/$tag/install-$tag.sh" -o "$installer" && bash "$installer"; result=$?; rm -f -- "${installer:-}"; exit "$result"'
```

The installer uses `sudo` for package installation, including `pulseaudio-utils`
for audio detection. It creates a `pc2mqtt.service` systemd **user** service,
starts it immediately, and enables it at login. The service retries after ten
seconds if the app exits, including when the broker is initially unreachable.
Rerun the one-liner to update or change broker settings. After downloading, the
installer stops the existing user service before replacing the package, then
starts it again. If installation fails after stopping a running service, it
attempts to restart that service. See [the Linux installer](scripts/install.sh).

```bash
systemctl --user status pc2mqtt         # check status
journalctl --user -u pc2mqtt -f          # view logs
systemctl --user disable --now pc2mqtt  # stop and disable automatic startup
```

To uninstall, disable the service above, delete
`${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/pc2mqtt.service`, run
`systemctl --user daemon-reload`, and then `sudo apt remove pc2mqtt`.

The one-liners become available once a stable release containing these installers
has finished building. They resolve GitHub's
[latest release](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases)
once, then download the matching `install-<tag>.ps1` or `install-<tag>.sh`.
Each downloaded installer stays pinned to its release, even if a newer release
is published later. To install a specific version (including a prerelease), download
and run its installer from that release's Assets list. The files in `scripts/`
are build templates; use the generated installers in release assets or `dist/`.

## Development and builds

Install Python 3.11 or 3.12, Poetry (CI uses 2.4.1), and GNU Make first.
On Windows, GNU Make is available through Chocolatey (`choco install make`).
Linux package builds also require `binutils` and `dpkg-dev`.

```shell
make setup                   # create .venv and install poetry.lock dependencies
make test                    # run the automated tests
make build-exe               # versioned Windows executable and installer in dist/
make build-deb               # versioned Debian package and installer in dist/
```

Local builds default to `v` plus the version in `pyproject.toml`. To build for a
specific tag, use `make build-deb RELEASE_TAG=v0.5.0` (or `build-exe`), or pass
`--tag v0.5.0` directly to `scripts/build.py`. Release CI always supplies the
published tag; it takes precedence over the project version.

Run `make setup` before tests or builds. Select a specific interpreter with
`make setup PYTHON=python3.12`; `POETRY` can also be overridden. Run setup outside
an already activated virtual environment so Poetry creates the project's `.venv`.
Build each package on its target OS; PyInstaller does not cross-compile. A local
Debian package requires the build host's glibc version or newer. Release builds use
Ubuntu 22.04 for a glibc 2.35 baseline. Both build targets check the executable with
`--help`. Start the app from source with `poetry run python -m pc2mqtt.app --host <broker>`.

Integration behavior, MQTT topics, and platform requirements are documented in the
module docstrings: [audio playback](pc2mqtt/integrations/audio.py) and [power controls](pc2mqtt/integrations/power.py).

## Adding integrations

Each integration lives in its own module: `AudioSensor` in `pc2mqtt/integrations/audio.py` and
`PowerControls` in `pc2mqtt/integrations/power.py`, with `StatusSensors` in `pc2mqtt/integrations/status.py`. Add a class to `INTEGRATION_TYPES` in
`pc2mqtt/integrations/__init__.py` to enable it. This replaces the earlier sensor-only registry.
The constructor receives the MQTT client, node name, shared device metadata,
connection availability topic, and logger.

- `config()` publishes discovery and subscribes to any command topics. It is
  called on every successful MQTT connection, including reconnects.
- `poll()` checks the integration and publishes when needed. It is called about
  once per second; each integration owns its schedule and handles backend errors.
- Optional `on_message(message)` handles commands and returns `True` for an owned
  topic. Validate the topic and payload here, and keep execution nonblocking.

Keep integration-specific topics, discovery payloads, state, and platform logic in
that integration's module. Use the shared connection availability topic in discovery;
integrations with a separate backend availability topic should require both to be online.
The application owns the MQTT connection and last will, message dispatch, and
polling loop. Registration is an explicit tuple of classes.

## Release builds

Publishing a GitHub release (including a prerelease) runs
`.github/workflows/release.yml` against its tag. For example, `v0.5.0` attaches:

- `install-v0.5.0.ps1` and `install-v0.5.0.sh` — installers pinned to this release.
- `pc2mqtt-v0.5.0-windows-x64.exe` — standalone Windows tray executable (no console window).
- `pc2mqtt-v0.5.0-linux-amd64.deb` — Debian/Ubuntu package for x64 Linux with glibc 2.35
  or newer (for example, Ubuntu 22.04 or newer).

The workflow installs locked dependencies, runs tests, builds with PyInstaller,
and checks each executable with `--help` before uploading. The Linux build also
installs and checks the Debian package. Re-running the workflow replaces assets
with the same names. It uses the built-in `GITHUB_TOKEN` with `contents: write`;
no additional upload secret is needed. Draft releases do not trigger a build;
publish the release to start it. Release asset uploads require mutable releases
(repository release immutability must be disabled for this flow). See GitHub's
[release event documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release).

Install and start the Linux package with:

```shell
sudo apt install ./pc2mqtt-v0.5.0-linux-amd64.deb
pc2mqtt --host <broker>
```

The package installs `/usr/bin/pc2mqtt` and bundles Python and the Python dependencies.
It recommends `pulseaudio-utils` for audio detection and `systemd-sysv` for power
commands. Run the app as your desktop user so it can access the audio server; the
package alone does not configure automatic startup; the installer above does.

The release tag is the source of truth for asset names and embedded versions.
Tags use `[v]MAJOR.MINOR.PATCH`, optionally with a dot-separated prerelease suffix
such as `-rc.1` and build metadata such as `+build.1`. Filenames preserve the exact
tag. The Windows File/Product version strings omit the optional `v` prefix;
numeric Windows version fields use `MAJOR.MINOR.PATCH.0` (each component must be
at most 65535). Debian versions also omit `v` and replace the prerelease separator
with `~`, so `v0.5.0-rc.1` becomes `0.5.0~rc.1` and sorts before `0.5.0`, following
[Debian version ordering](https://www.debian.org/doc/debian-policy/ch-controlfields.html#version).
`pyproject.toml` remains the version for source installs and local builds; it does
not override the tag in release CI.
