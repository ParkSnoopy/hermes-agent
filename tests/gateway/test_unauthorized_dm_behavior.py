import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource


@pytest.fixture
def transport(monkeypatch):
    entry = PlatformEntry(
        name="dm_auth_test", label="DM auth test", adapter_factory=lambda _: None,
        check_fn=lambda: True, allowed_users_env="DM_AUTH_TEST_ALLOWED_USERS",
        allow_all_env="DM_AUTH_TEST_ALLOW_ALL_USERS", allowlist_suppresses_pairing=True,
    )
    monkeypatch.setitem(platform_registry._entries, entry.name, entry)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    for key in ("DM_AUTH_TEST_ALLOWED_USERS", "DM_AUTH_TEST_ALLOW_ALL_USERS",
                "GATEWAY_ALLOWED_USERS", "GATEWAY_ALLOW_ALL_USERS"):
        monkeypatch.delenv(key, raising=False)
    return Platform(entry.name)


def _make_event(platform, user_id="person", chat_id="chat", *, is_bot=False):
    return MessageEvent(
        text="hello", message_id="m1",
        source=SessionSource(platform=platform, user_id=user_id, chat_id=chat_id,
                             user_name="tester", is_bot=is_bot, chat_type="dm"),
    )


def _make_runner(platform, extra=None):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={platform: PlatformConfig(enabled=True, extra=extra or {})})
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters = {platform: adapter}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    runner.pairing_store._is_rate_limited.return_value = False
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._update_prompts = {}
    runner.hooks = SimpleNamespace(dispatch=AsyncMock(return_value=None))
    runner._sessions = {}
    return runner, adapter


@pytest.mark.parametrize("allowlist,expected", [("tester", False), ("4", True)])
def test_allowlist_matches_stable_id_not_display_name(monkeypatch, transport, allowlist, expected):
    monkeypatch.setenv("DM_AUTH_TEST_ALLOWED_USERS", allowlist)
    runner, _ = _make_runner(transport)
    source = _make_event(transport, "4", "tester").source
    assert runner._is_user_authorized(source) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "zh"])
async def test_unauthorized_dm_pairs_by_default(monkeypatch, transport, language):
    monkeypatch.setenv("HERMES_LANGUAGE", language)
    runner, adapter = _make_runner(transport)
    runner.pairing_store.generate_code.return_value = "ABC12DEF"
    assert not runner._is_user_authorized(_make_event(transport).source)
    assert await runner._handle_message(_make_event(transport)) is None
    runner.pairing_store.generate_code.assert_called_once_with("dm_auth_test", "person", "tester")
    adapter.send.assert_awaited_once()
    assert "ABC12DEF" in adapter.send.await_args.args[1]


@pytest.mark.parametrize("allowed_users,expected", [
    (["person:alice"], True), (["alice"], True), (["person:bob"], False), ([], False),
])
def test_single_profile_adapter_config_allowlist(monkeypatch, transport, allowed_users, expected):
    runner, adapter = _make_runner(transport)
    adapter.config = PlatformConfig(enabled=True, extra={"allowed_users": allowed_users})
    adapter.normalize_user_id = lambda value: value.removeprefix("person:")
    assert not runner.config.multiplex_profiles
    assert runner._is_user_authorized(_make_event(transport, user_id="alice").source) is expected


@pytest.mark.asyncio
async def test_unauthorized_bot_dm_is_never_offered_a_pairing_code(transport):
    runner, adapter = _make_runner(transport)
    assert await runner._handle_message(_make_event(transport, is_bot=True)) is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_unauthorized_dm_can_be_ignored(transport):
    runner, adapter = _make_runner(transport, {"unauthorized_dm_behavior": "ignore"})
    assert await runner._handle_message(_make_event(transport)) is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("allowlist_env", ["DM_AUTH_TEST_ALLOWED_USERS", "GATEWAY_ALLOWED_USERS"])
async def test_allowlist_suppresses_pairing(monkeypatch, transport, allowlist_env):
    monkeypatch.setenv(allowlist_env, "other")
    runner, adapter = _make_runner(transport)
    assert runner._get_unauthorized_dm_behavior(transport) == "ignore"
    assert await runner._handle_message(_make_event(transport)) is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


def test_no_allowlist_defaults_to_pair(transport):
    runner, _ = _make_runner(transport)
    assert runner._get_unauthorized_dm_behavior(transport) == "pair"


@pytest.mark.asyncio
async def test_unauthorized_dm_decline_sends_once_then_stays_silent(transport):
    runner, adapter = _make_runner(transport, {"unauthorized_dm_behavior": "decline"})
    runner.config.unauthorized_dm_decline_message = "Sorry, this assistant is private."
    runner.pairing_store.has_recent_decline.return_value = False

    async def send_after_stamp(*args):
        runner.pairing_store.record_decline.assert_called_once_with("dm_auth_test", "person")

    adapter.send.side_effect = send_after_stamp
    assert await runner._handle_message(_make_event(transport)) is None
    runner.pairing_store.generate_code.assert_not_called()
    runner.pairing_store.record_decline.assert_called_once_with("dm_auth_test", "person")
    adapter.send.assert_awaited_once_with("chat", "Sorry, this assistant is private.")

    runner.pairing_store.has_recent_decline.return_value = True
    adapter.send.reset_mock()
    runner.pairing_store.record_decline.reset_mock()
    assert await runner._handle_message(_make_event(transport)) is None
    runner.pairing_store.record_decline.assert_not_called()
    adapter.send.assert_not_awaited()


def test_decline_config_and_stamp_roundtrip(monkeypatch, tmp_path, transport):
    import gateway.pairing as pairing_mod
    from gateway.config import load_gateway_config

    other = PlatformEntry(name="dm_auth_other", label="Other", adapter_factory=lambda _: None,
                          check_fn=lambda: True)
    monkeypatch.setitem(platform_registry._entries, other.name, other)
    other_platform = Platform(other.name)
    (tmp_path / "config.yaml").write_text(
        "unauthorized_dm_behavior: DECLINE\n"
        "unauthorized_dm_decline_message: '  custom text  '\n"
        "platforms:\n"
        "  dm_auth_test:\n"
        "    unauthorized_dm_behavior: pair\n"
        "  dm_auth_other:\n"
        "    unauthorized_dm_behavior: decline\n", encoding="utf-8",
    )
    with patch("gateway.config.get_hermes_home", return_value=tmp_path):
        config = load_gateway_config()
    assert config.unauthorized_dm_behavior == "decline"
    assert config.unauthorized_dm_decline_message == "custom text"
    assert config.get_unauthorized_dm_behavior(transport) == "pair"
    assert config.get_unauthorized_dm_behavior(other_platform) == "decline"
    assert config.get_unauthorized_dm_behavior(Platform.LOCAL) == "decline"
    restored = GatewayConfig.from_dict(config.to_dict())
    assert restored.unauthorized_dm_decline_message == "custom text"
    assert restored.get_unauthorized_dm_behavior(transport) == "pair"
    assert restored.get_unauthorized_dm_behavior(other_platform) == "decline"

    with patch("gateway.pairing.PAIRING_DIR", tmp_path):
        store = pairing_mod.PairingStore()
        assert store.has_recent_decline("dm_auth_test", "12345") is False
        store.record_decline("dm_auth_test", "12345")
        store = pairing_mod.PairingStore()
        assert store.has_recent_decline("dm_auth_test", "12345") is True
        assert store.has_recent_decline("dm_auth_test", "67890") is False
        with patch("gateway.pairing.time.time", return_value=time.time() + pairing_mod.DECLINE_DEDUPE_SECONDS + 1):
            assert store.has_recent_decline("dm_auth_test", "12345") is False
