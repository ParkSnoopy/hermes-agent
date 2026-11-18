"""API admission respects both gateway drain sources without scheduler support."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from gateway.platforms.api_server import APIServerAdapter


@pytest.mark.parametrize("local,external,expected", [
    (False, False, False),
    (True, False, True),
    (False, True, True),
])
def test_api_drain_admission(monkeypatch, local, external, expected):
    runner = SimpleNamespace(_draining=local, _external_drain_active=external)
    gateway_run = ModuleType("gateway.run")
    gateway_run._gateway_runner_ref = lambda: runner
    monkeypatch.setitem(sys.modules, "gateway.run", gateway_run)
    assert APIServerAdapter._gateway_is_draining() is expected
    adapter = object.__new__(APIServerAdapter)
    response = adapter._draining_response()
    assert (response is not None) is expected
    if response is not None:
        assert response.status == 503
        assert response.headers["Retry-After"] == "1"
