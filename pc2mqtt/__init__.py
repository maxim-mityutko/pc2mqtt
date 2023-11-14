import os
import json
import logging
import platform
import time
from enum import Enum

import paho.mqtt.client as mqtt


class PC2MQTT:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        keepalive: int = 60
    ):
        """
        :param host: MQTT broker host
        :param port: MQTT port
        :param keepalive: Keepalive interval
        """
        self.host = host
        self.port = port
        self.keepalive = keepalive

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self._system = platform.system()
        self._platform = platform.platform(terse=True, aliased=True)
        self._node = platform.node().lower()  # network name

        # logging
        self.logger = self._logger
        self.logger.info(f"System: {self._system} / Node: {self._node}")
        self.logger.info(f"Connecting to '{self.host}:{self.port}'")
        self.client.connect(host=self.host, port=self.port, keepalive=self.keepalive)

    @property
    def _logger(self):
        logging.basicConfig()
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)
        return logger

    class Topics(Enum):
        CONFIG = "homeassistant/switch/{node}/config"
        STATE = "homeassistant/switch/{node}/state"
        COMMAND = "homeassistant/switch/{node}/set"

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code):
        self._logger.info(f"Connected to MQTT broker with the result: {reason_code}")

        topic = self.Topics.COMMAND.value.format(node=self._node)
        client.subscribe(topic=topic)
        self._logger.info(f"Subscribed to command topic: {topic}")

    def on_message(self, client, userdata, message: mqtt.MQTTMessage):
        self._logger.info(f"{message.topic} {message.payload}")

        payload = message.payload.decode()
        if payload == "OFF":
            self.client.publish(topic=self.Topics.STATE.value.format(node=self._node), payload="OFF")
            self._shutdown()

    def config(self):
        device_name = f"Computer {self._node.upper()}"
        message = {
            "name": "Switch",
            # "device_class": "switch",
            "command_topic": self.Topics.COMMAND.value.format(node=self._node),
            "state_topic": self.Topics.STATE.value.format(node=self._node),
            "unique_id": f"{device_name.lower().replace(' ','_')}_switch",
            "device": {
                "identifiers": [self._node],
                "name": device_name,
                "model": self._system,
                "sw_version": self._platform
            }
        }

        self.client.publish(topic=self.Topics.CONFIG.value.format(node=self._node), payload=json.dumps(message))

    def state(self):
        while True:
            self.client.publish(topic=self.Topics.STATE.value.format(node=self._node), payload="ON")
            time.sleep(30)

    def _shutdown(self):
        if self._system.lower() == "windows":
            os.system("shutdown -t 0")
        elif self._system.lower() == "linux":
            os.system("shutdown now")
