# pc2mqtt
Reboot or shutdown Windows or Linux computer via MQTT

## Build
- Linux / MacOS
```shell
pyinstaller -F -n pc2mqtt-0.1.0 pc2mqtt/app.py
```
- Windows
```shell
pyinstaller -F -n pc2mqtt-0.1.0 .\pc2mqtt\app.py
```