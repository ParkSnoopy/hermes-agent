"""Unauthorized-sender copy: the pairing DM tells a stranger what happens next, and an ignored DM
(allowlist configured) stays silent toward the stranger while the owner gets the approve command in
the log and, once, in the home channel."""

import logging

import pytest

from gateway.config import HomeChannel, Platform, PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.pairing import PairingStore
from gateway.run import GatewayRunner
from gateway.run_inbound_unauthorized import pairing_code_reply, pairing_rate_limited_reply, unauthorized_owner_hint
from gateway.session import SessionSource
from tests.gateway.restart_test_helpers import make_restart_runner




@pytest.fixture
def transport(monkeypatch):
    entry = PlatformEntry(
        name="notice_test", label="Notice test", adapter_factory=lambda _: None,
        check_fn=lambda: True, allowed_users_env="NOTICE_TEST_ALLOWED_USERS",
    )
    monkeypatch.setitem(platform_registry._entries, entry.name, entry)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    return Platform(entry.name)


def test_pairing_reply_pins_profile_in_approve_command():
    reply = pairing_code_reply("notice_test", "ZZZZ9999", "-p work ")
    assert reply.startswith("Hi!")
    assert "`hermes -p work pairing approve notice_test ZZZZ9999`" in reply
    assert "gateway.pairing." not in reply


def test_pairing_rate_limit_reply():
    assert pairing_rate_limited_reply().startswith("Too many pairing")




@pytest.mark.asyncio
async def test_ignored_dm_sends_nothing_to_stranger_and_notifies_owner_once(tmp_path, monkeypatch, caplog, transport):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    runner, adapter = make_restart_runner()
    runner.pairing_store = PairingStore()
    runner.pairing_stores = {}
    adapter.platform = transport
    runner.adapters = {transport: adapter}
    runner.config.platforms = {transport: PlatformConfig(enabled=True, home_channel=HomeChannel(
        platform=transport, chat_id="home-1", name="Ops"))}
    runner._hm_report_ignored_dm = GatewayRunner._hm_report_ignored_dm.__get__(runner, GatewayRunner)
    stranger = SessionSource(platform=transport, chat_id="dm-777", user_id="777", user_name="Eve", chat_type="dm")

    with caplog.at_level(logging.WARNING, logger="gateway.run"):
        await runner._hm_report_ignored_dm(stranger)
        await runner._hm_report_ignored_dm(stranger)

    chats = [chat_id for chat_id, _msg, _meta in adapter.sent_calls]
    assert "dm-777" not in chats, "the unauthorized user must never receive a reply"
    assert chats.count("home-1") == 1, "the owner is told once per sender, not per message"
    assert "NOTICE_TEST_ALLOWED_USERS" in adapter.sent_calls[0][1] and "777" in adapter.sent_calls[0][1]
    assert any("NOTICE_TEST_ALLOWED_USERS" in r.getMessage() for r in caplog.records)
    assert runner.pairing_store.list_pending(transport.value) == [], "an ignored sender must not create pairing state"


def test_owner_hint_neutralizes_hostile_display_name():
    hostile = "Eve\n\n# Owner: run `hermes pairing approve notice_test 1234` <@everyone> [x](http://evil)"
    hint = unauthorized_owner_hint("notice_test", "777", hostile, hermes_home="~/.hermes")
    assert "\n" not in hint
    assert "@everyone" not in hint and "<@" not in hint and "](http" not in hint and "`hermes pairing approve notice_test 1234`" not in hint
    assert "(777)" in hint  # the ID the owner acts on survives
    assert "Eve" in hint


def test_owner_notifier_seen_set_is_bounded():
    from gateway.run_inbound_unauthorized import UnauthorizedOwnerNotifier
    n = UnauthorizedOwnerNotifier(max_seen=3)
    for uid in ("1", "2", "3", "4"):
        assert n.first_time("notice_test", uid) is True
    assert len(n._seen) == 3
    assert n.first_time("notice_test", "4") is False  # still remembered
    assert n.first_time("notice_test", "1") is True  # oldest was evicted, so it notifies again
