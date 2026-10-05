import logging
import platform
import time

import paho.mqtt.client as mqtt

from pc2mqtt.integrations import INTEGRATION_TYPES


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

        self.device = {
            "identifiers": [self._node],
            "name": f"Computer {self._node.upper()}",
            "model": self._system,
            "sw_version": self._platform,
        }
        self.availability_topic = f"pc2mqtt/{self._node}/availability"
        self.client.will_set(self.availability_topic, payload="offline", retain=True)

        # logging
        self.logger = self._logger
        self.integrations = [
            integration_type(self.client, self._node, self.device, self.availability_topic, self.logger)
            for integration_type in INTEGRATION_TYPES
        ]
        self.logger.info(f"System: {self._system} / Node: {self._node}")
        self.logger.info(f"Connecting to '{self.host}:{self.port}'")
        self.client.connect(host=self.host, port=self.port, keepalive=self.keepalive)

    @property
    def _logger(self):
        logging.basicConfig()
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)
        return logger

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code):
        self._logger.info(f"Connected to MQTT broker with the result: {reason_code}")

        if reason_code != 0:
            return
        self.config()
        client.publish(topic=self.availability_topic, payload="online", retain=True)

    def on_message(self, client, userdata, message: mqtt.MQTTMessage):
        for integration in self.integrations:
            handler = getattr(integration, "on_message", None)
            if handler is not None and handler(message):
                break

    def config(self):
        for integration in self.integrations:
            integration.config()

    def state(self):
        while True:
            for integration in self.integrations:
                integration.poll()
            time.sleep(1)
