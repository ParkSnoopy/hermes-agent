"""Account-owned connector RPCs work without reporting the client's surface."""

import pytest

from tools.connectors import live
from tools.managed_gateway_identity import user_agent
from tui_gateway import server


@pytest.fixture
def remote_backend(monkeypatch):
    monkeypatch.delenv("HERMES_DESKTOP", raising=False)
    monkeypatch.setattr("tools.connectors.connectors_available", lambda: True)
    monkeypatch.setattr("tools.connectors.ensure_guest_identity", lambda: None)
    monkeypatch.setattr("tools.connectors.managed.ensure_guest_identity", lambda: None)
    live.reset_for_tests()
    yield
    live.reset_for_tests()


def _dispatch(method, params):
    return server.handle_request({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def test_account_reads_and_connects_keep_a_surface_free_user_agent(remote_backend, monkeypatch):
    from tools.connectors.gateway.errors import GatewayUnavailable

    agents = []

    def read_tools(slug, *, client, refresh):
        agents.append(user_agent())
        raise GatewayUnavailable("gone", code="connector_not_found")

    class Client:
        def __init__(self, **_):
            pass

        def connections(self, names, **_):
            agents.append(user_agent())
            return {"results": [{"connector": n, "status": "active"} for n in names]}

    monkeypatch.setattr("tools.connectors.portal.client.PortalConnectorClient.require_authentication", lambda self: None)
    monkeypatch.setattr("tools.connectors.portal.tools_cache.read_tools", read_tools)
    monkeypatch.setattr("tools.connectors.gateway.client.ConnectorClient", Client)

    assert _dispatch("connectors.tools", {"slug": "gmail", "refresh": False})["error"]["code"] == 4041
    connected = _dispatch("connectors.connect", {"owner": {"type": "account"}, "connectors": ["gmail"]})
    assert "result" in connected, connected
    assert agents == [user_agent(), user_agent()]
    assert all(agent.startswith("hermes-agent/") and " (" not in agent for agent in agents)
