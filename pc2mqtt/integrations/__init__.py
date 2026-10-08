"""Select built-in integrations once for the current operating system.

Constructors accept (client, node, device, connection_availability_topic, logger).
config() refreshes discovery/capabilities and command subscriptions on connection.
poll() runs about once a second; integrations own timing and error handling.
on_message(message) returns True for a topic owned by the integration.
"""


def integration_types(system):
    # Explicit imports are visible to PyInstaller without dynamic plugin scanning.
    if system.lower() == 'linux':
        from .linux import INTEGRATION_TYPES
    elif system.lower() == 'windows':
        from .windows import INTEGRATION_TYPES
    else:
        raise NotImplementedError(f'Integrations are not supported on {system}')
    return INTEGRATION_TYPES
