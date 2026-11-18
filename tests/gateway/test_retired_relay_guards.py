"""Retired relay provenance must not revive access or delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.authz_mixin import GatewayAuthorizationMixin
from gateway.delivery import resolve_delivery_transport
from gateway.session import SessionSource
from gateway.session_ingress_context import restore_provenance
from hermes_state_runtime import RuntimeStoreError


@pytest.mark.parametrize("retired", ["telegram", "discord", "whatsapp", "slack", "relay"])
def test_archived_transport_credentials_do_not_advertise_a_live_connection(retired):
    config = GatewayConfig.from_dict({"platforms": {
        retired: {"enabled": True, "token": "archived-test-token"},
        "webhook": {"enabled": True},
    }})
    assert config.get_connected_platforms() == [Platform.WEBHOOK]
    # Reading old settings does not erase the user's saved configuration.
    assert config.to_dict()["platforms"][retired]["token"] == "archived-test-token"


def test_retired_relay_provenance_cannot_bind_live_adapter():
    source = SessionSource(platform=Platform.API_SERVER, chat_id="chat")
    runner = SimpleNamespace(adapters={Platform.API_SERVER: object()}, _profile_adapters={})
    with pytest.raises(RuntimeStoreError, match="permission_denied"):
        restore_provenance(runner, source, {"relay": "old-seal"})


def test_retired_relay_marker_cannot_resolve_a_live_intake_adapter():
    class Runner(GatewayAuthorizationMixin):
        def _transport_owner(self, source):
            return object(), None

    source = SessionSource(platform=Platform.API_SERVER, chat_id="chat")
    source.delivered_via_upstream_relay = True
    assert Runner()._intake_adapter_for(source) is None


@pytest.mark.asyncio
async def test_delivery_uses_only_enabled_matching_adapter():
    adapter = SimpleNamespace(send=AsyncMock(return_value="sent"))
    config = GatewayConfig(platforms={Platform.API_SERVER: PlatformConfig(enabled=True)})
    transport = resolve_delivery_transport(Platform.API_SERVER, config, {Platform.API_SERVER: adapter})
    assert await transport.send(Platform.API_SERVER, "chat", "hello", metadata={}) == "sent"
    adapter.send.assert_awaited_once_with("chat", "hello", metadata={})
    config.platforms[Platform.API_SERVER].enabled = False
    assert resolve_delivery_transport(Platform.API_SERVER, config, {Platform.API_SERVER: adapter}) is None