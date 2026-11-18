"""Multiplex callback authorization and profile ownership regressions."""

from pathlib import Path

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.pairing import PairingStore
from gateway.profile_routing import ProfileRoute


@pytest.fixture
def mux_home(tmp_path, monkeypatch):
    from agent import secret_scope
    from gateway.platform_registry import PlatformEntry, platform_registry

    home = tmp_path / "hh"
    (home / "profiles" / "secondary").mkdir(parents=True)
    (home / ".env").write_text("", encoding="utf-8")
    (home / "profiles" / "secondary" / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("TEST_AUTH_ALLOWED_USERS", raising=False)
    platform_registry.register(PlatformEntry(name="test_auth", label="Test", adapter_factory=lambda _: None,
                                             check_fn=lambda: True, allowed_users_env="TEST_AUTH_ALLOWED_USERS"))
    prev = secret_scope.is_multiplex_active()
    secret_scope.set_multiplex_active(True)
    yield home
    secret_scope.set_multiplex_active(prev)
    platform_registry.unregister("test_auth")


def _runner(home):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    platform = Platform("test_auth")
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner.config.profile_routes = [ProfileRoute(name="r", platform="test_auth", chat_id="routed", profile="secondary")]
    runner.config.platforms = {platform: PlatformConfig(enabled=True, extra={})}
    runner.pairing_store = PairingStore(profile="default")
    runner.pairing_stores = {"default": runner.pairing_store, "secondary": PairingStore(profile="secondary")}
    runner._primary_profile_name = "default"
    runner._profile_adapters = {"secondary": {}}
    runner.adapters = {}
    return runner


def test_routed_callback_uses_routed_pairing_store_and_transport_allowlist(mux_home):
    runner = _runner(mux_home)
    runner.pairing_stores["secondary"]._save_json(
        runner.pairing_stores["secondary"]._approved_path("test_auth"), {"777": {}})
    (mux_home / ".env").write_text("TEST_AUTH_ALLOWED_USERS=999\n", encoding="utf-8")
    check = runner._make_adapter_auth_check(Platform("test_auth"))
    assert check("777", chat_type="group", chat_id="routed") is True
    assert check("777", chat_type="group", chat_id="other") is False
    assert check("999", chat_type="group", chat_id="routed") is True
    assert check("888", chat_type="group", chat_id="routed") is False


def test_secondary_callback_uses_fresh_own_profile_allowlist(mux_home, monkeypatch):
    from hermes_cli import env_loader

    hydrated = []
    monkeypatch.setattr(env_loader, "hydrate_profile_secret_sources", hydrated.append)
    runner = _runner(mux_home)
    (mux_home / ".env").write_text("TEST_AUTH_ALLOWED_USERS=999\n", encoding="utf-8")
    monkeypatch.setenv("TEST_AUTH_ALLOWED_USERS", "999")
    env_path = mux_home / "profiles" / "secondary" / ".env"
    env_path.write_text("TEST_AUTH_ALLOWED_USERS=555\n", encoding="utf-8")
    check = runner._make_adapter_auth_check(Platform("test_auth"), profile_name="secondary")
    assert check("555", chat_type="dm", chat_id="chat") is True
    assert check("999", chat_type="dm", chat_id="chat") is False
    assert check("777", chat_type="dm", chat_id="chat") is False
    env_path.write_text("TEST_AUTH_ALLOWED_USERS=555,777\n", encoding="utf-8")
    assert check("777", chat_type="dm", chat_id="chat") is True
    assert hydrated == []


def test_authorization_adapter_ignores_per_turn_active_profile(mux_home):
    from gateway.run import _profile_runtime_scope

    runner = _runner(mux_home)
    platform = Platform("test_auth")
    default_bot = object()
    runner.adapters = {platform: default_bot}
    runner._profile_bot_platforms = {"secondary": {platform}}
    with _profile_runtime_scope(mux_home / "profiles" / "secondary"):
        assert runner._authorization_adapter(platform, profile="secondary") is None
    assert runner._authorization_adapter(platform, profile="default") is default_bot


def test_channel_directory_path_follows_current_home(mux_home):
    import gateway.channel_directory as cd
    from gateway.run import _profile_runtime_scope

    assert cd.DIRECTORY_PATH is None
    with _profile_runtime_scope(mux_home / "profiles" / "secondary"):
        assert cd._directory_path() == Path(mux_home / "profiles" / "secondary" / "channel_directory.json")
    assert cd._directory_path() == Path(mux_home / "channel_directory.json")
