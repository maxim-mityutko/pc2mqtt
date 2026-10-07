"""Machine IP and UTC last-seen sensors, updated every minute while connected.

The IP is the local address of the MQTT socket (IPv4 or IPv6), so machines with
multiple interfaces report the interface used to reach the broker. A local broker
may yield a loopback address. Last seen remains readable when the PC is offline.
"""

from datetime import datetime, timezone
import json
import time

from pc2mqtt.publishing import publish


class MachineSensors:
    def __init__(self, client, node, device, connection_availability_topic, logger):
        self.client = client
        self.device = device
        self.availability_topic = connection_availability_topic
        self.logger = logger
        self.topics = {
            key: f"homeassistant/sensor/{node}/{key}"
            for key in ("ip_address", "last_seen")
        }
        self._next_update = 0

    def config(self):
        identifier = self.device["name"].lower().replace(" ", "_")
        for key, topic in self.topics.items():
            message = {
                "name": "Machine IP address" if key == "ip_address" else "Last seen",
                "state_topic": f"{topic}/state",
                "unique_id": f"{identifier}_{key}",
                "device": self.device,
                "entity_category": "diagnostic",
            }
            if key == "last_seen":
                message["device_class"] = "timestamp"
            else:
                message["icon"] = "mdi:ip-network"
                message["availability_topic"] = self.availability_topic
            publish(self.client, topic=f"{topic}/config", payload=json.dumps(message))
        self._next_update = 0

    def poll(self):
        now = time.monotonic()
        if now < self._next_update or not self.client.is_connected():
            return
        self._next_update = now + 60
        publish(
            self.client, topic=f"{self.topics['last_seen']}/state",
            payload=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        try:
            connection = self.client.socket()
            if connection is None:
                return
            address = connection.getsockname()[0]
        except OSError as exc:
            self.logger.warning("Unable to read machine IP address: %s", exc)
            return
        publish(self.client, topic=f"{self.topics['ip_address']}/state", payload=address)
