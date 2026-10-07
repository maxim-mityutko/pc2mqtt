# pc2mqtt

Expose actions and sensors from Windows or Linux computer via MQTT.

## Command Line Arguments

- `--host` - ip or hostname of MQTT broker
- `--port` - port of the MQTT broker (default: 1883)
- `--keepalive` - interval to send keepalive messages (default: 60)

## Actions and Sensors

- Shutdown
- Restart
- Sleep
- Audio playing
- Machine IP address
- Last seen

## MQTT retention and machine sensors

An MQTT 5 broker is required. All published discovery, state, and availability
messages are retained with a 12-hour (43,200-second) message expiry, including the
last will. Each publication resets that topic's expiry. Discovery is refreshed
every six hours and connection availability every minute while connected.
The broker removes expired retained messages; this does not delete Home Assistant
history. Audio still has its separate 90-second Home Assistant state expiry.

Machine sensors update on connection and every minute:

- **Machine IP address** reports the local IPv4 or IPv6 address used to connect to
  the MQTT broker. With a broker on the same machine, this may be loopback.
- **Last seen** reports the UTC timestamp of the latest heartbeat. It remains
  readable when the computer goes offline, until the retained message expires.

Topics are `homeassistant/sensor/<node>/ip_address/state` and
`homeassistant/sensor/<node>/last_seen/state`. Both sensors are discovered on the
existing computer device. See [machine sensors](pc2mqtt/machine.py).

## Installation

- Windows
  - `Win + R` and open the Autostart location for all users: `shell:common startup`
  - Place the `pc2mqtt.exe` executable in `c:\Program Files (x86)`
  - Create a shortcut for the executable with argument `--host <my_mqtt>` 
  - Select to run application minimized
  - Move the shortcut to the `Autostart` location

## Development and builds

Install Python 3.11 or 3.12, Poetry (CI uses 1.7.0), and GNU Make first.
On Windows, GNU Make is available through Chocolatey (`choco install make`).
Linux package builds also require `binutils` and `dpkg-dev`.

```shell
make setup                   # create .venv and install poetry.lock dependencies
make test                    # run the automated tests
make build-exe               # Windows x64: dist/pc2mqtt-windows-x64.exe
make build-deb               # Linux x64: dist/pc2mqtt-linux-amd64.deb
```

Run `make setup` before tests or builds. Select a specific interpreter with
`make setup PYTHON=python3.12`; `POETRY` can also be overridden. Run setup outside
an already activated virtual environment so Poetry creates the project's `.venv`.
Build each package on its target OS; PyInstaller does not cross-compile. A local
Debian package requires the build host's glibc version or newer. Release builds use
Ubuntu 22.04 for a glibc 2.35 baseline. Both build targets check the executable with
`--help`. Start the app from source with `poetry run python -m pc2mqtt.app --host <broker>`.

Integration behavior, MQTT topics, and platform requirements are documented in the
module docstrings: [audio playback](pc2mqtt/audio.py) and [power controls](pc2mqtt/power.py).

## Adding integrations

Each integration lives in its own module: `AudioSensor` in `pc2mqtt/audio.py` and
`PowerControls` in `pc2mqtt/power.py`, with `MachineSensors` in `pc2mqtt/machine.py`. Add a class to `INTEGRATION_TYPES` in
`pc2mqtt/integrations.py` to enable it. This replaces the earlier sensor-only registry.
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
`.github/workflows/release.yml` against its tag and attaches:

- `pc2mqtt-windows-x64.exe` — standalone Windows executable.
- `pc2mqtt-linux-amd64.deb` — Debian/Ubuntu package for x64 Linux with glibc 2.35
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
sudo apt install ./pc2mqtt-linux-amd64.deb
pc2mqtt --host <broker>
```

The package installs `/usr/bin/pc2mqtt` and bundles Python and the Python dependencies.
It recommends `pulseaudio-utils` for audio detection and `systemd-sysv` for power
commands. Run the app as your desktop user so it can access the audio server; the
package does not configure automatic startup. Package versions come from
`pyproject.toml`, so update the project version before tagging a release.
