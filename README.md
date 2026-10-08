# pc2mqtt

Expose Windows and Linux computer controls and sensors to Home Assistant via MQTT.
Requires an **MQTT 5** broker.

## Command Line Arguments

| Option | Description | Default |
| --- | --- | --- |
| `--host` | MQTT broker hostname or IP | Required |
| `--display-name` | Computer display name suffix | Hostname |
| `--port` | MQTT broker port | `1883` |
| `--keepalive` | Keepalive in seconds; `0` disables it | `60` |
| `--tray` | Windows tray mode | Enabled in the Windows executable |

`pc2mqtt --host broker.local --display-name foo` displays **Computer foo**.
Quote spaces on the command line: `--display-name "Living Room"`. Display names
preserve capitalization; MQTT topics and entity IDs still use the real hostname.

Installers prompt for these settings. To change startup settings manually, add
`--display-name foo` to the Windows shortcut or Linux service's `ExecStart`.
On Linux, apply edits with `systemctl --user daemon-reload` and
`systemctl --user restart pc2mqtt`.

## Actions and Sensors

`<node>` is the lowercase hostname. Send action and control payloads **without retain**.

| Entity | Type | Description / payload | MQTT topic |
| --- | --- | --- | --- |
| Shutdown | Action | Power off; send `PRESS` | `homeassistant/button/<node>/shutdown/set` |
| Restart | Action | Reboot; send `PRESS` | `homeassistant/button/<node>/restart/set` |
| Sleep | Action | Suspend; send `PRESS` | `homeassistant/button/<node>/sleep/set` |
| Audio playing | Sensor | Active playback: `ON` / `OFF` | `homeassistant/binary_sensor/<node>/audio_playing/state` |
| IP address | Sensor | Local IPv4/IPv6 used to reach the broker | `homeassistant/sensor/<node>/ip_address/state` |
| Last seen | Sensor | UTC heartbeat, updated every minute | `homeassistant/sensor/<node>/last_seen/state` |
| Session locked | Sensor | Session lock state: `ON` / `OFF` | `homeassistant/binary_sensor/<node>/session_locked/state` |
| User idle time | Sensor | Idle duration; seconds on MQTT, hours in HA | `homeassistant/sensor/<node>/idle_time/state` |
| Uptime | Sensor | OS uptime; seconds on MQTT, hours in HA | `homeassistant/sensor/<node>/uptime/state` |
| Volume | Control | Default output volume; send `0`–`100` | `homeassistant/number/<node>/volume/set` |
| Mute | Control | Mute default output; send `ON` / `OFF` | `homeassistant/switch/<node>/mute/set` |
| Lock session | Action | Lock desktop; send `PRESS` | `homeassistant/button/<node>/lock_session/set` |
| Turn off displays | Action | Switch off screens; send `PRESS` | `homeassistant/button/<node>/displays_off/set` |

Connection availability: `pc2mqtt/<node>/availability` (`online` / `offline`).
Discovery, state, and availability are retained for 12 hours; audio state expires
after 90 seconds. Details: [audio](pc2mqtt/integrations/audio.py),
[power](pc2mqtt/integrations/power.py), [status](pc2mqtt/integrations/status.py),
[desktop](pc2mqtt/integrations/desktop/).

Desktop sensors and volume/mute are checked every ten seconds; changes publish
immediately after a check, with a one-minute refresh. Volume/mute feedback uses
the corresponding `/state` topic. Unsupported features report unavailable individually.
Windows supports all desktop features; uptime may span Fast Startup shutdowns.
Linux lock state depends on the desktop updating loginctl's `LockedHint`; idle time
supports GNOME or X11, and display-off supports X11 or Sway. Other Wayland desktops
may lack idle/display control. The user service needs the desktop's display/session
environment (`DISPLAY`/`XAUTHORITY` for X11, `SWAYSOCK` for Sway).

## Installation

Run as your desktop user. Installers download the latest stable release, start
the app, and enable startup at login. Rerun to update or change settings; the
running installed copy is stopped before replacement and restarted afterward.

Enter the broker hostname/IP without `mqtt://`. Press Enter for port **1883**,
display name **hostname**, and keepalive **60 seconds**. In installer prompts,
names with spaces need no quotes.

### Windows x64

Run in **PowerShell**, without administrator privileges:

```powershell
& { $ErrorActionPreference = 'Stop'; $release = Invoke-RestMethod 'https://api.github.com/repos/maxim-mityutko/pc2mqtt/releases/latest'; $tag = $release.tag_name; $installer = Join-Path $env:TEMP ('pc2mqtt-' + [guid]::NewGuid() + '.ps1'); try { Invoke-WebRequest -UseBasicParsing "https://github.com/maxim-mityutko/pc2mqtt/releases/download/$tag/install-$tag.ps1" -OutFile $installer; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer } finally { Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue } }
```

Installs to `%LOCALAPPDATA%\pc2mqtt` and adds `pc2mqtt.lnk` to `shell:startup`
(open with `Win + R`). The tray menu provides connection status, **Open log**,
and **Quit**; logs are in `%LOCALAPPDATA%\pc2mqtt\pc2mqtt.log`.
Tray mode runs in your desktop session for audio detection and retries unavailable
broker connections. Source runs can enable it with `--tray`.

To uninstall, choose **Quit**, remove the Startup shortcut, and delete the install
folder. Remove older manual startup entries to avoid duplicate instances.

### Linux x64 (Debian/Ubuntu)

Requires Bash, `curl`, a systemd user session, and glibc 2.35+ (Ubuntu 22.04+).
Run from your desktop terminal **without sudo**; the installer uses `sudo` only
for packages, including audio and desktop helpers (`pulseaudio-utils`,
`x11-xserver-utils`, `xprintidle`, and `libglib2.0-bin`).

```bash
bash -c 'release=$(curl -fsSL -o /dev/null -w "%{url_effective}" https://github.com/maxim-mityutko/pc2mqtt/releases/latest) && tag=${release##*/} && installer=$(mktemp) && curl -fsSL --retry 3 "https://github.com/maxim-mityutko/pc2mqtt/releases/download/$tag/install-$tag.sh" -o "$installer" && bash "$installer"; result=$?; rm -f -- "${installer:-}"; exit "$result"'
```

Runs as a systemd user service, restarting after ten seconds if the app exits.

```bash
systemctl --user status pc2mqtt         # check status
journalctl --user -u pc2mqtt -f          # view logs
systemctl --user disable --now pc2mqtt  # stop and disable automatic startup
```

To uninstall, disable the service above, delete
`${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/pc2mqtt.service`, run
`systemctl --user daemon-reload`, then `sudo apt remove pc2mqtt`.

One-liners require a completed stable release with installer assets. For a specific
version or prerelease, run its `install-<tag>.ps1` or `install-<tag>.sh` asset.
Downloaded installers stay pinned to their release; files in `scripts/` are build
templates. See [Windows](scripts/install.ps1) and [Linux](scripts/install.sh) installers.

## Development and builds

Requires Python 3.11 or 3.12, Poetry (CI: 2.4.1), and GNU Make
(`choco install make` on Windows). Linux builds also need `binutils` and `dpkg-dev`.

```shell
make setup                   # create .venv and install poetry.lock dependencies
make test                    # run the automated tests
make build-exe               # versioned Windows executable and installer in dist/
make build-deb               # versioned Debian package and installer in dist/
```

Run setup outside an active virtual environment; use `make setup PYTHON=python3.12`
to choose Python, or override `POETRY`. Local builds use the `pyproject.toml` version;
override with `make build-deb RELEASE_TAG=v0.5.0` (or `build-exe`), or pass
`--tag v0.5.0` to `scripts/build.py`.

Build on the target OS; cross-compilation is unsupported. Local Debian packages
require the build host's glibc or newer; releases use Ubuntu 22.04 / glibc 2.35.
Run from source: `poetry run python -m pc2mqtt.app --host <broker>`.

## Adding integrations

Add a module under `pc2mqtt/integrations/` and register its class in
[`INTEGRATION_TYPES`](pc2mqtt/integrations/__init__.py). Constructors receive
`(client, node, device, connection_availability_topic, logger)`.

- `config()`: publish discovery and subscribe on each MQTT connection.
- `poll()`: called about once a second; manage timing and backend errors here.
- Optional `on_message(message)`: handle commands and return `True` for owned topics.

Keep topics and platform logic in the integration; the app owns MQTT and polling.

## Release builds

Publishing a release or prerelease runs [the release workflow](.github/workflows/release.yml).
For tag `v0.5.0`, it attaches:

- `install-v0.5.0.ps1` and `install-v0.5.0.sh`
- `pc2mqtt-v0.5.0-windows-x64.exe` — tray app, no console
- `pc2mqtt-v0.5.0-linux-amd64.deb` — Debian/Ubuntu, glibc 2.35+

CI runs tests, checks executables with `--help`, and installs/checks the Debian
package. Reruns replace matching assets. Publish drafts to trigger builds;
repository release immutability must be disabled. The built-in `GITHUB_TOKEN`
with `contents: write` handles uploads.

Manual Linux installation (bundles Python; does not enable startup):

```shell
sudo apt install ./pc2mqtt-v0.5.0-linux-amd64.deb
pc2mqtt --host <broker>
```

Release tags override `pyproject.toml` and use `[v]MAJOR.MINOR.PATCH`, optionally
with `-rc.1` and `+build.1`. Filenames preserve the tag; embedded versions omit `v`.
Windows numeric versions use `MAJOR.MINOR.PATCH.0` (components ≤65535); Debian maps
prereleases such as `v0.5.0-rc.1` to `0.5.0~rc.1` so they sort before stable releases.
