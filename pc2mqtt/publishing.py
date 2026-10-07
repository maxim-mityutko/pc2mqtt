"""Shared retained publishing policy (MQTT 5)."""

from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

MESSAGE_EXPIRY_SECONDS = 12 * 60 * 60


def expiry_properties(packet_type=PacketTypes.PUBLISH):
    properties = Properties(packet_type)
    properties.MessageExpiryInterval = MESSAGE_EXPIRY_SECONDS
    return properties


def publish(client, *, topic, payload, retain=True):
    return client.publish(
        topic=topic, payload=payload, retain=retain, properties=expiry_properties(),
    )
