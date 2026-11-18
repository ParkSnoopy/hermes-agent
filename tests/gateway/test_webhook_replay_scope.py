"""Webhook replay/credential invariants that the durable ledger does not cover (PR #106742 security lane)."""

from gateway.config import PlatformConfig
from gateway.platforms.webhook import WebhookAdapter


def _adapter(routes):
    return WebhookAdapter(PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0, "routes": routes}))




def test_adding_a_dynamic_route_keeps_the_adapter_credential():
    """An unrelated hot-reloaded subscription must not re-key the connector of admitted deliveries."""
    from gateway.run import GatewayRunner
    a = _adapter({"victim": {"secret": "v", "prompt": "x"}})
    before = GatewayRunner._adapter_credential_fingerprint(a)
    a._dynamic_routes = {"agent-made": {"secret": "n", "prompt": "y"}}
    a._routes = {**a._dynamic_routes, **a._static_routes}
    assert GatewayRunner._adapter_credential_fingerprint(a) == before
