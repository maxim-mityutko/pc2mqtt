import os
import argparse
from pc2mqtt import PC2MQTT

parser = argparse.ArgumentParser()
parser.add_argument("--host")
parser.add_argument("--port", default=1883, type=int)
parser.add_argument("--keepalive", default=60, type=int, required=False)
args = parser.parse_args()

c = PC2MQTT(
    host=args.host,
    port=args.port,
    keepalive=args.keepalive,
)  # connect to broker and subscribe to command channel

c.client.loop_start()
c.config()  # announce availability through MQTT
c.state()   # announce state
c.client.loop_stop()
