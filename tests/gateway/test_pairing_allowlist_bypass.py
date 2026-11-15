"""Pairing grants and configured allowlists remain a union for plugin transports."""

import os
from types import SimpleNamespace

import pytest

from gateway.session import Platform, SessionSource


@pytest.fixture(autouse=True)
def _human_transport(monkeypatch):
    from tests.gateway.test_authority_busy_plaintext_approval import _human_adapter
    _human_adapter(monkeypatch)
    for name in ("HUMAN_FIXTURE_ALLOWED_USERS", "GATEWAY_ALLOWED_USERS", "GATEWAY_ALLOW_ALL_USERS"):
        monkeypatch.delenv(name, raising=False)


def _make_runner(*, paired):
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.pairing_store = SimpleNamespace(is_approved=lambda *_a, **_kw: paired)
    return runner


def _make_source(user_id="pairme"):
    return SessionSource(platform=Platform("human_fixture"), chat_id="123", chat_type="dm",
                         user_id=user_id, user_name="SomeHuman", is_bot=False)


def test_paired_user_authorized_even_when_not_in_allowlist(monkeypatch):
    monkeypatch.setenv("HUMAN_FIXTURE_ALLOWED_USERS", "owner1,owner2")
    assert _make_runner(paired=True)._is_user_authorized(_make_source()) is True


def test_unpaired_user_in_allowlist_still_authorized(monkeypatch):
    monkeypatch.setenv("HUMAN_FIXTURE_ALLOWED_USERS", "owner1")
    assert _make_runner(paired=False)._is_user_authorized(_make_source("owner1")) is True


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    import importlib
    import gateway.pairing as pairing_mod
    importlib.reload(pairing_mod)
    return pairing_mod.PairingStore()


def test_approval_adds_to_configured_allowlist(store, monkeypatch):
    import hermes_cli.config as cfg
    monkeypatch.setenv("HUMAN_FIXTURE_ALLOWED_USERS", "owner1")
    captured = {}
    monkeypatch.setattr(cfg, "save_env_value",
                        lambda k, v: (captured.__setitem__(k, v), os.environ.__setitem__(k, v)))
    code = store.generate_code("human_fixture", "newuser99", "")
    assert code is not None
    assert store.approve_code("human_fixture", code) is not None
    assert captured["HUMAN_FIXTURE_ALLOWED_USERS"] == "owner1,newuser99"


def test_revoke_removes_from_allowlist(store, monkeypatch):
    import hermes_cli.config as cfg
    monkeypatch.setenv("HUMAN_FIXTURE_ALLOWED_USERS", "owner1,newuser99")
    saved = {}
    monkeypatch.setattr(cfg, "save_env_value",
                        lambda k, v: (saved.__setitem__(k, v), os.environ.__setitem__(k, v)))
    store._approve_user("human_fixture", "newuser99", "")
    assert store.revoke("human_fixture", "newuser99") is True
    assert saved["HUMAN_FIXTURE_ALLOWED_USERS"] == "owner1"


def test_approval_does_not_create_an_unconfigured_allowlist(store, monkeypatch):
    import hermes_cli.config as cfg
    writes = []
    monkeypatch.setattr(cfg, "save_env_value", lambda *args: writes.append(args))
    code = store.generate_code("human_fixture", "newuser99", "")
    assert code is not None
    assert store.approve_code("human_fixture", code) is not None
    assert store.is_approved("human_fixture", "newuser99")
    assert writes == []


@pytest.mark.parametrize("platform", ["whatsapp", "whatsapp_cloud"])
def test_archived_pairing_identity_can_be_revoked_by_device_alias(store, platform):
    """Legacy pairing records remain revocable without a native adapter."""
    store._approve_user(platform, "15551234567@s.whatsapp.net", "")
    assert store.is_approved(platform, "15551234567@s.whatsapp.net")
    assert store.revoke(platform, "15551234567:47@s.whatsapp.net")
    assert not store.is_approved(platform, "15551234567@s.whatsapp.net")
