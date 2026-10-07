"""Built-in integrations, explicitly registered without dynamic plugin loading.

Constructors accept (client, node, device, connection_availability_topic, logger).
config() announces discovery and subscribes to commands on each MQTT connection.
poll() runs about once a second; integrations own their timing and error handling.
Optional on_message(message) handles commands, returning True for an owned topic.
"""

from pc2mqtt.audio import AudioSensor
from pc2mqtt.power import PowerControls
from pc2mqtt.machine import MachineSensors


INTEGRATION_TYPES = (AudioSensor, PowerControls, MachineSensors)
