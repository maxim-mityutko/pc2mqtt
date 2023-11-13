import os
from pc2mqtt import PC2MQTT

mqtt_host = os.environ.get("MQTT_HOST", "casa.brhd.io")
mqtt_port = int(os.environ.get("MQTT_PORT", "31883"))

c = PC2MQTT(
    host=mqtt_host,
    port=mqtt_port
)
