#!/usr/bin/env bash
# Release installer template; scripts/build.py stamps the release tag.
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }

release_tag='@RELEASE_TAG@'
[[ $release_tag != @* ]] || fail 'Use an installer from a release, or generate one with scripts/build.py.'

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

default_display_name=$(uname -n)
default_display_name=${default_display_name^^}
while true; do
    read -r -p "Display name [$default_display_name]: " display_name
    display_name="${display_name#"${display_name%%[![:space:]]*}"}"
    display_name="${display_name%"${display_name##*[![:space:]]}"}"
    [[ ! $display_name =~ [[:cntrl:]] ]] && break
    printf 'Enter a display name without control characters.\n'
done
while true; do
    read -r -p 'MQTT keepalive in seconds [60]: ' mqtt_keepalive
    mqtt_keepalive=${mqtt_keepalive:-60}
    if [[ $mqtt_keepalive =~ ^[0-9]{1,5}$ ]] && (( 10#$mqtt_keepalive <= 65535 )); then
        mqtt_keepalive=$((10#$mqtt_keepalive))
        break
    fi
    printf 'Enter a keepalive between 0 and 65535 seconds (0 disables keepalive).\n'
done

# Quote a single systemd argument, including literal specifiers and dollar signs.
display_argument=''
if [[ -n $display_name ]]; then
    escaped_name=${display_name//\\/\\\\}
    escaped_name=${escaped_name//\"/\\\"}
    escaped_name=${escaped_name//%/%%}
    escaped_name=${escaped_name//\$/\$\$}
    display_argument=" \"--display-name=$escaped_name\""
fi

download_dir=$(mktemp -d)
restart_on_failure=false
cleanup() {
    result=$?
    if (( result != 0 )) && [[ $restart_on_failure == true ]]; then
        systemctl --user start pc2mqtt.service || printf 'Could not restart pc2mqtt after the failed update. Check the service status.\n' >&2
    fi
    rm -rf -- "$download_dir"
    exit "$result"
}
trap cleanup EXIT
package="$download_dir/pc2mqtt-${release_tag}-linux-amd64.deb"
curl --fail --location --show-error --silent --retry 3 \
    "https://github.com/maxim-mityutko/pc2mqtt/releases/download/${release_tag}/pc2mqtt-${release_tag}-linux-amd64.deb" \
    --output "$package"
# Allow apt's unprivileged download user to read the local package.
chmod 755 "$download_dir"
chmod 644 "$package"
sudo apt-get update
service_dir="$config_dir/systemd/user"
if systemctl --user is-active --quiet pc2mqtt.service; then
    restart_on_failure=true
fi
# Stop the entire service process group, including PyInstaller's child process.
# Explicitly stopping the unit also prevents Restart=always from relaunching it.
if [[ -f $service_dir/pc2mqtt.service || $restart_on_failure == true ]]; then
    systemctl --user stop pc2mqtt.service
fi
sudo apt-get install --yes "$package" pulseaudio-utils

mkdir -p "$service_dir"
cat > "$service_dir/pc2mqtt.service" <<EOF
[Unit]
Description=PC controls and sensors over MQTT
StartLimitIntervalSec=0

[Service]
ExecStart=/usr/bin/pc2mqtt --host $mqtt_host --port $mqtt_port --keepalive $mqtt_keepalive$display_argument
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
