# pc2mqtt
Autodiscover Windows or Linux computer via the MQTT broker in Home Assistant.
Computer will expose the switch that will initiate remote shutdown when toggled 
to 'OFF' state.

## Command Line Arguments
- `--host` - ip or hostname of MQTT broker
- `--port` - port of the MQTT broker (default: 1883)
- `--keepalive` - interval to send keepalive messages (default: 60)

## Installation
- Windows
  - `Win + R` and open the Autostart location for all users: `shell:common startup`
  - Place the `pc2mqtt.exe` executable in `c:\Program Files (x86)`
  - Create a shortcut for the executable with argument `--host <my_mqtt>` 
  - Select to run application minimized
  - Move the shortcut to the `Autostart` location

## Build
- Linux / MacOS
    ```shell
    pyinstaller -F -n pc2mqtt-0.1.0 pc2mqtt/app.py
    ```
- Windows
    ```shell
    pyinstaller -F -n pc2mqtt-0.1.0 .\pc2mqtt\app.py
    ```