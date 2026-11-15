"""Allowlist matching must not grant a qualified identity by its localpart."""

import pytest

from gateway.config import Platform
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.session import SessionSource


def _source(platform, user_id):
    return SessionSource(platform=platform, user_id=user_id, chat_id=user_id,
                         user_name="tester", chat_type="dm")


@pytest.mark.parametrize("env_var", ["QUALIFIED_TEST_ALLOWED_USERS", "GATEWAY_ALLOWED_USERS"])
def test_bare_localpart_entry_admits_no_foreign_domain(monkeypatch, env_var):
    from gateway.run import GatewayRunner

    entry = PlatformEntry(name="qualified_test", label="Qualified test",
                          adapter_factory=lambda _: None, check_fn=lambda: True,
                          allowed_users_env="QUALIFIED_TEST_ALLOWED_USERS",
                          allow_all_env="QUALIFIED_TEST_ALLOW_ALL_USERS")
    monkeypatch.setitem(platform_registry._entries, entry.name, entry)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    for key in ("QUALIFIED_TEST_ALLOWED_USERS", "QUALIFIED_TEST_ALLOW_ALL_USERS",
                "GATEWAY_ALLOWED_USERS", "GATEWAY_ALLOW_ALL_USERS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(env_var, "alice,bob@corp.example")
    platform = Platform(entry.name)
    runner = object.__new__(GatewayRunner)
    runner.pairing_store = None

    assert runner._is_user_authorized(_source(platform, "alice@evil.example")) is False
    assert runner._is_user_authorized(_source(platform, "bob@evil.example")) is False
    assert runner._is_user_authorized(_source(platform, "bob@corp.example")) is True
