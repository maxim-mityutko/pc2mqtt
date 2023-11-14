import os
from pc2mqtt import PC2MQTT

mqtt_host = os.environ.get("MQTT_HOST", "casa.brhd.io")
mqtt_port = int(os.environ.get("MQTT_PORT", "31883"))
mqtt_keepalive = int(os.environ.get("MQTT_KEEPALIVE", "60"))

c = PC2MQTT(
    host=mqtt_host,
    port=mqtt_port,
    keepalive=mqtt_keepalive,
)  # connect to broker and subscribe to command channel

c.client.loop_start()
c.config()  # announce availability through MQTT
c.state()   # announce state
c.client.loop_stop()
