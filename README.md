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
after 90 seconds. Integrations live in matching [Linux](pc2mqtt/integrations/linux/)
and [Windows](pc2mqtt/integrations/windows/) packages:

| Module | Entities |
| --- | --- |
| `power` | Shutdown, restart, sleep, turn off displays |
| `audio` | Audio playing, volume, mute |
| `status` | IP address, last seen, uptime |
| `user` | Session locked, user idle time, lock session |

Only supported capabilities are discovered and subscribed to. Checks apply per
entity, including power commands, audio dependencies, session/display environment,
and native APIs. Unsupported entities have their retained discovery, state, and
availability cleared and command subscriptions removed. Capabilities are checked
again on reconnect and discovery refresh; newly available features then appear.
Temporary backend failures keep supported entities discovered but unavailable
where per-entity availability is provided. Power command failures are logged.
Disabled features log an INFO message with the reason; unchanged reasons are not
repeated. Commands require the running user's permissions; support detection does
not execute power or lock actions to test them.

Session sensors, uptime, and volume/mute are checked every ten seconds; changes
publish after each check, with a one-minute refresh. Volume/mute feedback uses the
corresponding `/state` topic and controls the default output. IP address and last
seen update every minute; last seen remains readable while the computer is offline.

Audio playback is checked every second. Two seconds of sustained playback trigger
an early `ON`; `OFF` is sent at the next minute update. Muted or silent active
streams count as playback. Linux requires `pactl` and PulseAudio or PipeWire's
PulseAudio compatibility server; direct ALSA playback is not covered. Windows
checks Core Audio sessions across all active outputs. Detection failures mark
playback unavailable and are retried.

Power commands run asynchronously. Retained commands are ignored, and queued
commands are discarded on reconnect. Linux shutdown/restart require `shutdown`;
sleep requires `systemctl` and a running systemd system manager. Windows uses
`shutdown` and Windows PowerShell for sleep. Sleep also requires hardware/OS
support. Windows uptime may span Fast Startup shutdowns.
Linux lock state depends on the desktop updating loginctl's `LockedHint`; idle time
supports GNOME or X11, and display-off supports X11 or Sway. Other Wayland desktops
omit unsupported idle/display controls. The user service needs the desktop's display/session
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
make test                    # run the pytest suite
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

Both `pc2mqtt/integrations/linux/` and `pc2mqtt/integrations/windows/` contain
`power.py`, `audio.py`, `status.py`, and `user.py`. Put native operations and
capability detection in the appropriate platform/domain module. Reusable MQTT
lifecycle code lives in [`_shared.py`](pc2mqtt/integrations/_shared.py); Linux session
detection and Windows API binding use private helpers inside their platform packages.

Each platform's `__init__.py` explicitly registers its integration classes.
[`integration_types(system)`](pc2mqtt/integrations/__init__.py) selects only the
current OS package. Each domain class inherits the same `EntityIntegration` and
has a complete `entities` catalog, including playback, power actions, and portable
status sensors. [`Entity`](pc2mqtt/integrations/_entities.py) defines MQTT metadata,
command validation, polling intervals, availability, and sampling behavior.

Constructors receive `(client, node, device, connection_availability_topic, logger)`.
Optional `backend=` and `clock=` arguments allow tests to supply dependencies.
Power backends also accept `runner=` for launching asynchronous processes.

- `config()`: take one capability snapshot, publish discovery, update subscriptions,
  and discard queued commands. Transient detection errors preserve known support.
- `poll()`: sample due entities and execute queued commands. Playback is checked on
  every tick (`interval=0`); its debounce and heartbeat remain specialized.
- `on_message(message)`: validate and queue commands, returning `True` for owned topics.

Backends implement `supported_features()` and `unsupported_reason(key)`, plus only
the operations their catalog needs: `read(key)` for sensors, `check(key)` for button
availability, `execute(key, value)` for controls, and `start(key)` for asynchronous
actions. Portable sensors use catalog readers. New process actions specify their
metadata and terminal behavior in the catalog, without changing shared lifecycle code.
[`_state.py`](pc2mqtt/integrations/_state.py) handles playback timing and process
tracking independently of discovery. Read-only integrations allocate no command queues.

Keep MQTT topics and unique IDs stable when moving code. Tests use pytest classes,
plain assertions, fixtures in `tests/conftest.py`, and parametrized cases. Standard
library `unittest.mock` is retained for mocks only. Run an individual area with
`poetry run pytest tests/test_audio.py -v` or select cases with `poetry run pytest -k capability`.
Add coverage for missing dependencies, unsupported environments, recovery, and
native operations. App tests can inject `client=` and `integration_factory=`;
select integrations by role rather than registry position.

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
