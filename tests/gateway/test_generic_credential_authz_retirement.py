"""Generic authorization and credential readiness after native adapter retirement."""

from types import SimpleNamespace

from gateway import authz_mixin, config
from gateway.authz_mixin import GatewayAuthorizationMixin, _principal_matches_allowlist
from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource


def test_credential_gate_uses_registry_readiness_and_retains_missing_plugin_retry(monkeypatch):
    from gateway.run import _platform_has_bot_credential
    from gateway.platform_registry import platform_registry

    plugin = Platform._add_pseudo_member("generic_test_plugin")
    entry = SimpleNamespace(name=plugin.value, config_connected_fn=lambda cfg: bool(cfg.extra.get("credential")))
    monkeypatch.setitem(platform_registry._entries, plugin.value, entry)

    def no_discovery(platform):
        raise AssertionError("Credential readiness must not discover or load plugins")

    monkeypatch.setattr(config, "_discovered_registry_entry", no_discovery)
    assert not _platform_has_bot_credential(plugin, PlatformConfig())
    assert _platform_has_bot_credential(plugin, PlatformConfig(extra={"credential": "value"}))
    # A plugin-specific check owns readiness even if a token is present.
    assert not _platform_has_bot_credential(plugin, PlatformConfig(token="token"))
    entry.config_connected_fn = None
    entry.is_connected = lambda cfg: bool(cfg.extra.get("ready"))
    entry.validate_config = None
    assert not _platform_has_bot_credential(plugin, PlatformConfig())
    assert _platform_has_bot_credential(plugin, PlatformConfig(api_key="key"))
    assert _platform_has_bot_credential(plugin, PlatformConfig(extra={"ready": True}))
    assert _platform_has_bot_credential(Platform.MATRIX, PlatformConfig())
    assert _platform_has_bot_credential(Platform.WEBHOOK, PlatformConfig())
    assert not _platform_has_bot_credential(Platform.MSGRAPH_WEBHOOK, PlatformConfig())
    assert _platform_has_bot_credential(Platform.MSGRAPH_WEBHOOK, PlatformConfig(extra={"client_state": "value"}))
    assert not _platform_has_bot_credential(Platform.API_SERVER, PlatformConfig())
    assert _platform_has_bot_credential(Platform.API_SERVER, PlatformConfig(extra={"key": "test-key-1234567890"}))


def test_archived_qualified_identity_is_not_a_live_alias():
    source = SessionSource(platform=Platform.WHATSAPP, user_id="123@s.whatsapp.net", chat_id="123", chat_type="dm")
    assert not _principal_matches_allowlist(source, "123@s.whatsapp.net", {"123"})
    assert _principal_matches_allowlist(source, "123@s.whatsapp.net", {"123@s.whatsapp.net"})


def test_native_group_variable_cannot_grant_but_generic_chat_allowlist_can(monkeypatch):
    platform = Platform.TELEGRAM
    source = SessionSource(platform=platform, user_id="person", chat_id="-123", chat_type="group")
    entry = SimpleNamespace(group_allowed_chats_env="PLUGIN_ALLOWED_CHATS", allowed_users_env="PLUGIN_ALLOWED_USERS", allow_all_env="")
    monkeypatch.setattr(authz_mixin, "_registry_entry", lambda _platform: entry)
    monkeypatch.setattr(authz_mixin, "authz_field", lambda _name, field, ent: getattr(ent, field, ""))
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_USERS", "-123,person")
    runner = GatewayAuthorizationMixin()
    assert not runner._is_user_authorized(source)
    monkeypatch.setenv("PLUGIN_ALLOWED_CHATS", "-123")
    assert runner._is_user_authorized(source)


def test_plugin_normalizer_matches_scoped_allowlist_without_unqualified_aliases(monkeypatch):
    platform = Platform._add_pseudo_member("generic_auth_plugin")
    entry = SimpleNamespace(group_allowed_chats_env="", allowed_users_env="PLUGIN_ALLOWED_USERS", allow_all_env="")
    monkeypatch.setattr(authz_mixin, "_registry_entry", lambda _platform: entry)
    monkeypatch.setattr(authz_mixin, "authz_field", lambda _name, field, ent: getattr(ent, field, ""))
    monkeypatch.setenv("PLUGIN_ALLOWED_USERS", "person:alice")
    runner = GatewayAuthorizationMixin()
    adapter = SimpleNamespace(normalize_user_id=lambda value: value.removeprefix("person:"))
    monkeypatch.setattr(runner, "_delivery_adapter_for", lambda source: adapter)
    source = SessionSource(platform=platform, user_id="alice", chat_id="chat", chat_type="dm")
    assert runner._is_user_authorized(source)
    source.user_id = "alice@foreign.example"
    assert not runner._is_user_authorized(source)
