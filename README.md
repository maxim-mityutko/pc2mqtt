# pc2mqtt

Windows and Linux controls and sensors for Home Assistant. Requires **MQTT 5**.

## Command line

```shell
pc2mqtt --host broker.local --display-name "Living Room"
```

| Option | Description | Default |
| --- | --- | --- |
| `--host` | MQTT broker hostname or IP | Required |
| `--display-name` | Computer display name suffix | Hostname |
| `--port` | MQTT broker port | `1883` |
| `--keepalive` | Keepalive in seconds; `0` disables it | `60` |
| `--tray` | Windows tray mode | Enabled in the Windows executable |

- Display name: **Computer Living Room**; topics and entity IDs use the real hostname.
- Installers prompt for settings. Rerun to update them, or edit the Windows startup shortcut / Linux service's `ExecStart`.
- After editing the Linux service: `systemctl --user daemon-reload && systemctl --user restart pc2mqtt`.

## Actions and sensors

`<node>` is the lowercase hostname. Send commands **without retain**.

| Entity | Type | Description / payload | MQTT topic |
| --- | --- | --- | --- |
| Shutdown | Action | Power off; send `PRESS` | `homeassistant/button/<node>/shutdown/set` |
| Restart | Action | Reboot; send `PRESS` | `homeassistant/button/<node>/restart/set` |
| Sleep | Action | Suspend; send `PRESS` | `homeassistant/button/<node>/sleep/set` |
| Status | Binary sensor | pc2mqtt connected: `online` / `offline` | `pc2mqtt/<node>/availability` |
| Play / Pause | Action | Toggle media playback; send `PRESS` | `homeassistant/button/<node>/play_pause/set` |
| Audio playing | Sensor | Active playback: `ON` / `OFF` | `homeassistant/binary_sensor/<node>/audio_playing/state` |
| IP address | Sensor | Local IPv4/IPv6 used to reach the broker | `homeassistant/sensor/<node>/ip_address/state` |
| Last seen | Sensor | UTC heartbeat, updated every minute | `homeassistant/sensor/<node>/last_seen/state` |
| Session locked | Sensor | Session lock state: `ON` / `OFF` | `homeassistant/binary_sensor/<node>/session_locked/state` |
| User idle time | Sensor | Idle duration; seconds on MQTT, hours in HA | `homeassistant/sensor/<node>/user_idle_time/state` |
| Uptime | Sensor | OS uptime; seconds on MQTT, hours in HA | `homeassistant/sensor/<node>/uptime/state` |
| Volume | Control | Default output volume; send `0`–`100` | `homeassistant/number/<node>/volume/set` |
| Mute | Control | Mute default output; send `ON` / `OFF` | `homeassistant/switch/<node>/mute/set` |
| Lock session | Action | Lock desktop; send `PRESS` | `homeassistant/button/<node>/lock_session/set` |
| Turn off displays | Action | Switch off screens; send `PRESS` | `homeassistant/button/<node>/turn_off_displays/set` |

| Behavior | Details |
| --- | --- |
| Connection availability | `pc2mqtt/<node>/availability`: `online` / `offline` |
| Retention | Discovery, state, and availability: 12 hours; playback state expires in HA after 90 seconds |
| Sensor updates | Session, idle, uptime, volume/mute: checked every 10 seconds; changes published, refreshed every minute |
| Heartbeat | IP and last seen: every minute; last seen remains readable offline |
| Playback | Checked every second; `ON` after 2 seconds of activity, `OFF` at the next minute update; muted/silent streams count |
| Controls | Volume/mute target the default output; feedback uses `/state` |
| Capabilities | Unsupported entities are removed; rechecked on reconnect/discovery refresh; temporary failures retain discovery |
| Commands | Retained commands ignored; queued commands cleared on reconnect; power commands run asynchronously |

### Platform requirements

| Feature | Linux | Windows |
| --- | --- | --- |
| Audio | `pactl` + PulseAudio or PipeWire's PulseAudio compatibility; no direct ALSA | Core Audio; playback checked across all active outputs |
| Play / Pause | `playerctl` + an MPRIS-compatible player on the user session bus | Media play/pause key in the logged-in desktop session |
| Power | `shutdown`; sleep needs `systemctl` and running systemd | `shutdown`; Windows PowerShell for sleep |
| Session lock | `loginctl`; lock state requires desktop `LockedHint` reporting | Logged-in user session |
| Idle time | GNOME + `gdbus`, or X11 + `xprintidle` | Last-input API |
| Display off | X11 + `xset`, or Sway + `swaymsg` | Native display-power API |

Commands require user permissions and OS/hardware support. Linux services need the
session environment (`DISPLAY`/`XAUTHORITY` or `SWAYSOCK`). Windows uptime may span Fast Startup shutdowns.

## Installation

- Run as your **desktop user**. Installers start the app and enable startup at login.
- Rerun to update; the installed instance is stopped and restarted automatically.
- Broker: hostname/IP without `mqtt://`. Enter accepts defaults; names with spaces need no quotes in prompts.
- One-liners use the latest completed stable release. For a specific release/prerelease, use its `install-<tag>.ps1` or `install-<tag>.sh` asset.

### Windows x64

Run in **PowerShell**, without administrator privileges:

```powershell
& { $ErrorActionPreference = 'Stop'; $release = Invoke-RestMethod 'https://api.github.com/repos/maxim-mityutko/pc2mqtt/releases/latest'; $tag = $release.tag_name; $installer = Join-Path $env:TEMP ('pc2mqtt-' + [guid]::NewGuid() + '.ps1'); try { Invoke-WebRequest -UseBasicParsing "https://github.com/maxim-mityutko/pc2mqtt/releases/download/$tag/install-$tag.ps1" -OutFile $installer; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer } finally { Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue } }
```

| Item | Location / action |
| --- | --- |
| Installation | `%LOCALAPPDATA%\pc2mqtt` |
| Startup | `pc2mqtt.lnk` in `shell:startup` (open via `Win + R`) |
| Logs | Tray → **Open log**, or `%LOCALAPPDATA%\pc2mqtt\pc2mqtt.log` |
| Tray | Connection status, **Open log**, **Quit**; source runs use `--tray` |
| Uninstall | **Quit**, remove startup shortcut, delete installation folder |

Remove older startup entries to avoid duplicate instances.

### Linux x64 (Debian/Ubuntu)

Requires Bash, `curl`, a systemd user session, and glibc 2.35+ (Ubuntu 22.04+).
Run **without sudo**; the installer uses it for packages and audio/desktop helpers.

```bash
bash -c 'release=$(curl -fsSL -o /dev/null -w "%{url_effective}" https://github.com/maxim-mityutko/pc2mqtt/releases/latest) && tag=${release##*/} && installer=$(mktemp) && curl -fsSL --retry 3 "https://github.com/maxim-mityutko/pc2mqtt/releases/download/$tag/install-$tag.sh" -o "$installer" && bash "$installer"; result=$?; rm -f -- "${installer:-}"; exit "$result"'
```

```bash
systemctl --user status pc2mqtt         # service status; auto-restarts after 10s
journalctl --user -u pc2mqtt -f          # logs
systemctl --user disable --now pc2mqtt  # stop and disable startup
```

Uninstall after disabling the service:

```bash
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/pc2mqtt.service"
systemctl --user daemon-reload
sudo apt remove pc2mqtt
```

Installer sources: [Windows](scripts/install.ps1) · [Linux](scripts/install.sh).

## Development

| Requirement | Details |
| --- | --- |
| Tools | Python 3.11 or 3.12, Poetry (CI: 2.4.1), GNU Make (`choco install make` on Windows) |
| Linux builds | `binutils`, `dpkg-dev`; resulting packages need the build host's glibc or newer |
| Build platform | Build on the target OS; cross-compilation unsupported |

```shell
make setup                                  # create .venv; install locked dependencies
make test                                   # pytest suite
poetry run pytest tests/test_audio.py -v     # one test module
poetry run python -m pc2mqtt.app --host broker.local
make build-exe                              # Windows executable + installer in dist/
make build-deb                              # Debian package + installer in dist/
```

- Run setup outside an active virtual environment; choose Python with `make setup PYTHON=python3.12`.
- Builds use `pyproject.toml`'s version; override with `RELEASE_TAG=v0.5.0` or `poetry run python scripts/build.py deb --tag v0.5.0`.
- Tests use pytest classes, fixtures, parametrization, and `unittest.mock` for mocks.

### Integrations

Matching [Linux](pc2mqtt/integrations/linux/) and [Windows](pc2mqtt/integrations/windows/) packages:

| Module | Entities |
| --- | --- |
| `power` | Shutdown, restart, sleep, turn off displays |
| `audio` | Play / Pause, audio playing, volume, mute |
| `status` | Status, IP address, last seen, uptime |
| `user` | Session locked, user idle time, lock session |

| Component | Responsibility |
| --- | --- |
| Platform/domain modules | Complete entity catalogs, native operations, capability detection |
| [`_entities.py`](pc2mqtt/integrations/_entities.py) | Entity metadata, discovery payloads, command validation |
| [`_shared.py`](pc2mqtt/integrations/_shared.py) | `config()`, `poll()`, `on_message()`; one capability snapshot per refresh |
| [`_state.py`](pc2mqtt/integrations/_state.py) | Playback timing and asynchronous process tracking |
| [`tests/conftest.py`](tests/conftest.py) | Injectable MQTT clients, backends, clocks, and app factories |

Register integrations in each platform's `__init__.py`. Preserve MQTT topics/IDs;
cover missing dependencies, unsupported environments, and recovery in tests.

## Releases

Publishing a release/prerelease triggers [the release workflow](.github/workflows/release.yml).

| Asset example | Contents |
| --- | --- |
| `install-v0.5.0.ps1`, `install-v0.5.0.sh` | Installers pinned to the release |
| `pc2mqtt-v0.5.0-windows-x64.exe` | Tray app, no console |
| `pc2mqtt-v0.5.0-linux-amd64.deb` | Bundled Python; glibc 2.35+; manual install does not enable startup |

- CI runs tests, executable `--help` checks, and Debian install checks.
- Publish drafts to trigger builds. Reruns replace assets; release immutability must be disabled.
- Uploads use `GITHUB_TOKEN` with `contents: write`.
- Tags: `[v]MAJOR.MINOR.PATCH`, optionally `-rc.1` / `+build.1`; override the project version. Windows numeric components must be ≤65535; Debian maps prereleases to `~rc.1`.

```shell
sudo apt install ./pc2mqtt-v0.5.0-linux-amd64.deb
pc2mqtt --host broker.local
```
