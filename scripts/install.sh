#!/usr/bin/env bash
# Install the latest stable release for the current desktop user.
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }

[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'Linux x64 is required.'
[[ $(id -u) != 0 ]] || fail 'Run as your desktop user, without sudo; only package installation uses sudo.'
for tool in curl sudo apt-get dpkg systemctl; do
    command -v "$tool" >/dev/null || fail "Missing $tool. This installer requires Debian/Ubuntu with systemd and curl."
done
[[ $(dpkg --print-architecture) == amd64 ]] || fail 'An amd64 Debian/Ubuntu installation is required.'
systemctl --user show-environment >/dev/null || fail 'Run from a logged-in user session with a systemd user manager.'
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
[[ $config_dir == /* ]] || fail 'XDG_CONFIG_HOME must be an absolute path.'

while true; do
    read -r -p 'MQTT host (hostname or IP address): ' mqtt_host
    [[ $mqtt_host =~ ^[a-zA-Z0-9:][a-zA-Z0-9._:-]*$ ]] && break
    printf 'Enter a hostname or IP address without a URL scheme, brackets, or spaces.\n'
done
while true; do
    read -r -p 'MQTT port [1883]: ' mqtt_port
    mqtt_port=${mqtt_port:-1883}
    if [[ $mqtt_port =~ ^[0-9]{1,5}$ ]] && (( 10#$mqtt_port >= 1 && 10#$mqtt_port <= 65535 )); then
        mqtt_port=$((10#$mqtt_port))
        break
    fi
    printf 'Enter a port between 1 and 65535.\n'
done

download_dir=$(mktemp -d)
trap 'rm -rf -- "$download_dir"' EXIT
package="$download_dir/pc2mqtt-linux-amd64.deb"
curl --fail --location --show-error --silent --retry 3 \
    https://github.com/maxim-mityutko/pc2mqtt/releases/latest/download/pc2mqtt-linux-amd64.deb \
    --output "$package"
# Allow apt's unprivileged download user to read the local package.
chmod 755 "$download_dir"
chmod 644 "$package"
sudo apt-get update
sudo apt-get install --yes "$package" pulseaudio-utils

service_dir="$config_dir/systemd/user"
mkdir -p "$service_dir"
cat > "$service_dir/pc2mqtt.service" <<EOF
[Unit]
Description=PC controls and sensors over MQTT
StartLimitIntervalSec=0

[Service]
ExecStart=/usr/bin/pc2mqtt --host $mqtt_host --port $mqtt_port
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable pc2mqtt.service
systemctl --user restart pc2mqtt.service
printf '\nInstalled and started pc2mqtt. It will start automatically at login.\n'
printf 'Check status: systemctl --user status pc2mqtt\n'
printf 'View logs: journalctl --user -u pc2mqtt -f\n'
