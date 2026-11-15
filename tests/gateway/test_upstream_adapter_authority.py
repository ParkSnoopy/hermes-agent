"""Adapter-declared upstream authorization remains explicit and fail-closed."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from gateway.config import Platform
from gateway.session import SessionSource


def _runner(platform, upstream):
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.adapters = {platform: SimpleNamespace(send=AsyncMock(), authorization_is_upstream=upstream,
                                                  enforces_own_access_policy=False)}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    runner.pairing_store._is_rate_limited.return_value = False
    return runner


def test_base_adapter_defaults_to_not_upstream_authorized():
    from gateway.platforms.base import BasePlatformAdapter
    assert BasePlatformAdapter.authorization_is_upstream.fget(object()) is False


def test_non_upstream_adapter_still_default_denies(monkeypatch):
    for key in ("GATEWAY_ALLOWED_USERS", "GATEWAY_ALLOW_ALL_USERS"):
        monkeypatch.delenv(key, raising=False)
    from tests.gateway.test_platform_reconnect import register_test_platform
    platform = register_test_platform(monkeypatch)
    source = SessionSource(platform=platform, user_id="123", chat_id="456", chat_type="dm")
    assert _runner(platform, False)._is_user_authorized(source) is False


def test_registered_adapter_can_declare_upstream_authorization(monkeypatch):
    from tests.gateway.test_platform_reconnect import register_test_platform
    platform = register_test_platform(monkeypatch)
    source = SessionSource(platform=platform, user_id="123", chat_id="456", chat_type="dm")
    assert _runner(platform, True)._is_user_authorized(source) is True
