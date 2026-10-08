"""Shared test dependencies; native actions and MQTT connections are always mocked."""

from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pc2mqtt import PC2MQTT
from pc2mqtt.integrations import integration_types


@pytest.fixture
def mqtt_client():
    client = Mock()
    client.is_connected.return_value = True
    client.socket.return_value.getsockname.return_value = ('192.0.2.1', 1883)
    return client


@pytest.fixture
def make_integration():
    def make(cls, *, capabilities=None, values=None, client=None, backend=None):
        client = client if client is not None else Mock()
        client.is_connected.return_value = True
        client.socket.return_value.getsockname.return_value = ('192.0.2.1', 1883)
        values = dict(values or {})
        if backend is None:
            backend = Mock(
                spec=[
                    'supported_features',
                    'unsupported_reason',
                    'read',
                    'check',
                    'execute',
                    'start',
                ]
            )
            backend.read.side_effect = lambda key: values.get(key, 0)
            backend.unsupported_reason.return_value = 'missing dependency'
            backend.supported_features.return_value = (
                set(cls.entities) if capabilities is None else set(capabilities)
            )
        clock, logger = Mock(return_value=0), Mock()
        integration = cls(
            client,
            'pc',
            {'name': 'Computer PC', 'model': 'Test'},
            'connection',
            logger,
            backend=backend,
            clock=clock,
        )
        return SimpleNamespace(
            integration=integration,
            backend=backend,
            client=client,
            clock=clock,
            logger=logger,
            values=values,
        )

    return make


@pytest.fixture
def make_app(monkeypatch):
    def make(*, system='Linux', display_name=None):
        client = Mock()
        client.is_connected.return_value = True
        client.socket.return_value.getsockname.return_value = ('192.0.2.1', 1883)
        backends = {}
        clock = Mock(return_value=0)

        def factory(selected_system):
            assert selected_system == system
            constructors = []
            for cls in integration_types(system):
                role = cls.__name__.lower()
                backend = Mock(
                    spec=[
                        'supported_features',
                        'unsupported_reason',
                        'read',
                        'check',
                        'execute',
                        'start',
                    ]
                )
                backend.supported_features.return_value = set(cls.entities)
                backend.read.return_value = 0
                backend.unsupported_reason.return_value = 'missing dependency'
                backends[role] = backend
                constructors.append(partial(cls, backend=backend, clock=clock))
            return constructors

        with monkeypatch.context() as context:
            context.setattr('pc2mqtt.platform.system', lambda: system)
            context.setattr('pc2mqtt.platform.node', lambda: 'desktop')
            app = PC2MQTT(
                'broker', display_name=display_name, client=client, integration_factory=factory
            )
        integrations = {type(item).__name__.lower(): item for item in app.integrations}
        return SimpleNamespace(
            app=app, client=client, backends=backends, clock=clock, integrations=integrations
        )

    return make
