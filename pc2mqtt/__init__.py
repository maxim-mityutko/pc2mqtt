import logging
import platform
import time

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes

from pc2mqtt.integrations import integration_types
from pc2mqtt.publishing import MESSAGE_EXPIRY_SECONDS, expiry_properties, publish


class PC2MQTT:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        keepalive: int = 60,
        *,
        connect_async: bool = False,
        display_name: str | None = None,
        client=None,
        integration_factory=None,
    ):
        """
        :param host: MQTT broker host
        :param port: MQTT port
        :param keepalive: Keepalive interval
        :param display_name: Display name suffix; defaults to the uppercase hostname
        """
        if display_name is not None:
            display_name = display_name.strip()
            if not display_name:
                raise ValueError('Computer name must not be empty')
        self.host = host
        self.port = port
        self.keepalive = keepalive

        self.client = (
            client
            if client is not None
            else mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                protocol=mqtt.MQTTv5,
            )
        )
        self._next_discovery = float('inf')
        self._next_availability = float('inf')
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        node = platform.node().lower()  # network name

        self.device = {
            'identifiers': [node],
            'name': f'Computer {display_name if display_name is not None else node.upper()}',
            'model': platform.system(),
            'sw_version': platform.platform(terse=True, aliased=True),
        }
        self.availability_topic = f'pc2mqtt/{node}/availability'
        self.client.will_set(
            self.availability_topic,
            payload='offline',
            retain=True,
            properties=expiry_properties(PacketTypes.WILLMESSAGE),
        )

        # logging
        self.logger = self._logger
        factory = integration_factory if integration_factory is not None else integration_types
        self.integrations = [
            integration_type(self.client, node, self.device, self.availability_topic, self.logger)
            for integration_type in factory(self.device['model'])
        ]
        self.logger.info('System: %s / Node: %s', self.device['model'], node)
        self.logger.info(f"Connecting to '{self.host}:{self.port}'")
        connect = self.client.connect_async if connect_async else self.client.connect
        connect(host=self.host, port=self.port, keepalive=self.keepalive)

    @property
    def _logger(self):
        logging.basicConfig()
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)
        return logger

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties=None):
        self._logger.info(f'Connected to MQTT broker with the result: {reason_code}')

        if reason_code != 0:
            return
        self.config()
        publish(client, topic=self.availability_topic, payload='online')
        self._next_availability = time.monotonic() + 60

    def on_message(self, client, userdata, message: mqtt.MQTTMessage):
        for integration in self.integrations:
            handler = getattr(integration, 'on_message', None)
            if handler is not None and handler(message):
                break

    def config(self):
        self._next_discovery = time.monotonic() + MESSAGE_EXPIRY_SECONDS / 2
        for integration in self.integrations:
            integration.config()

    def poll(self):
        now = time.monotonic()
        if self.client.is_connected():
            if now >= self._next_discovery:
                self.config()
            if now >= self._next_availability:
                publish(self.client, topic=self.availability_topic, payload='online')
                self._next_availability = now + 60
        for integration in self.integrations:
            integration.poll()

    def state(self):
        while True:
            self.poll()
            time.sleep(1)

    def close(self):
        try:
            if self.client.is_connected():
                message = publish(self.client, topic=self.availability_topic, payload='offline')
                message.wait_for_publish(timeout=2)
        except (RuntimeError, ValueError):
            self.logger.warning('Unable to publish offline state during shutdown', exc_info=True)
        finally:
            try:
                self.client.disconnect()
            finally:
                self.client.loop_stop()
