import os
import logging
import platform
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

        self._platform = platform.system().lower()
        self._node = platform.node().lower()  # network name

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        logging.basicConfig()
        self._logger = logging.getLogger(__name__)
        self._logger.setLevel(logging.INFO)

        self._logger.info(f"System info: {self._platform} / {self._node}")
        self.client.connect(host=host, port=port, keepalive=keepalive)
        self.client.loop_forever()

    def on_connect(self, client, userdata, flags, reason_code):
        topic = f"pc/{self._node}"

        self._logger.info(f"Connected to MQTT broker with the result: {reason_code}")
        client.subscribe(topic=topic)
        self._logger.info(f"Subscribed to topic: {topic}")

    def on_message(self, client, userdata, message: mqtt.MQTTMessage):
        self._logger.info(f"{message.topic} {message.payload}")

        payload = message.payload.decode().lower()
        if payload == "reboot":
            self._reboot()
        elif payload == "shutdown":
            self._shutdown()

    def _reboot(self):
        if self._platform == "windows":
            os.system("shutdown -r -t 0")
        elif self._platform == "linux":
            os.system("shutdown -r now")

    def _shutdown(self):
        if self._platform == "windows":
            os.system("shutdown -t 0")
        elif self._platform == "linux":
            os.system("shutdown now")