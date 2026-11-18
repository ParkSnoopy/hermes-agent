"""Redaction-safe forensic logging at the Nous OAuth quarantine path.

A NAS-hosted Fly agent's Nous bootstrap session can take a terminal
``invalid_grant`` and get quarantined (dead tokens cleared from auth.json).
Historically this was completely silent — no WARNING+ record at the terminal
rejection, only a downstream "No access token found" warning once the pool was
already empty. The Fly log drain is WARNING-only, so nothing about the terminal
death reached centralized logging. These tests lock in that
``_quarantine_nous_oauth_state`` now emits a WARNING+ forensic record, and — the
load-bearing assertion — that the raw refresh token never appears in that output.
"""

import hashlib
import logging

import pytest

from hermes_cli.auth import AuthError, _quarantine_nous_oauth_state


# A distinctive, obviously-fake refresh token so the redaction assertion is
# unambiguous if it ever leaks.
_FAKE_RT = "nous_rt_LEAK_CANARY_do_not_log_raw_0123456789abcdef"
_EXPECTED_FP = hashlib.sha256(_FAKE_RT.encode("utf-8")).hexdigest()[:12]


def _make_state(**overrides):
    state = {
        "portal_base_url": "https://portal.example.com",
        "client_id": "test-client-id",
        "access_token": "nous_at_SECRET_access_token_material",
        "refresh_token": _FAKE_RT,
        "agent_key": "nous_agent_key_SECRET_material",
        "agent_key_id": "ak-12345",
        "expires_at": "2020-01-01T00:00:00+00:00",  # in the past
        "obtained_at": "2019-12-31T00:00:00+00:00",
    }
    state.update(overrides)
    return state


def _error():
    return AuthError(
        "invalid_grant: token expired or revoked",
        provider="nous",
        code="invalid_grant",
        relogin_required=True,
    )






def test_raw_refresh_token_never_logged(caplog):
    """Load-bearing redaction-safety test: the raw secret must never appear."""
    state = _make_state()
    with caplog.at_level(logging.DEBUG, logger="hermes_cli.auth"):
        _quarantine_nous_oauth_state(state, _error(), reason="unit_test_quarantine")

    text = caplog.text
    assert _FAKE_RT not in text, "RAW refresh token leaked into log output!"
    assert _EXPECTED_FP not in text
    assert "refresh_token_fp" not in text
    assert any("Nous OAuth state quarantined" in record.getMessage()
               and record.levelno >= logging.WARNING for record in caplog.records)
    assert "refresh_token" not in state
    # Belt-and-suspenders: the access token and agent key must not leak either.
    assert "nous_at_SECRET_access_token_material" not in text
    assert "nous_agent_key_SECRET_material" not in text


def test_terminal_refresh_quarantines_singleton_pool_and_persists(monkeypatch):
    from hermes_cli import auth, auth_nous

    state = _make_state()
    store = {"credential_pool": {"nous": [
        {"source": auth.NOUS_DEVICE_CODE_SOURCE}, {"source": "manual:other"}]}}
    writes = []

    def reject(**_kwargs):
        raise _error()

    monkeypatch.setattr(auth, "_refresh_access_token", reject)
    with pytest.raises(AuthError):
        auth_nous._refresh_nous_or_quarantine(
            client=None, auth_store=store, state=state, portal_base_url="https://example.test",
            client_id="test", refresh_token=_FAKE_RT, reason="terminal_test",
            persist=lambda: writes.append(True))
    assert writes == [True]
    assert "refresh_token" not in state
    assert store["credential_pool"]["nous"] == [{"source": "manual:other"}]


def test_proxy_terminal_failure_quarantines_pool(monkeypatch):
    from hermes_cli.proxy.adapters import nous_portal

    state = _make_state()
    store = {"providers": {"nous": state.copy()}, "credential_pool": {"nous": [
        {"source": "device_code"}, {"source": "manual:other"}]}}
    saved = []
    monkeypatch.setattr(nous_portal, "_load_auth_store", lambda: store)
    monkeypatch.setattr(nous_portal, "_save_auth_store", lambda value: saved.append(value.copy()))
    monkeypatch.setattr(nous_portal, "_write_shared_nous_state", lambda *_a, **_kw: None)

    def reject(**_kwargs):
        raise _error()

    monkeypatch.setattr(nous_portal, "resolve_nous_runtime_credentials", reject)
    with pytest.raises(RuntimeError, match="Failed to refresh"):
        nous_portal.NousPortalAdapter().get_credential()
    assert saved
    assert "refresh_token" not in saved[0]["providers"]["nous"]
    assert saved[0]["credential_pool"]["nous"] == [{"source": "manual:other"}]


