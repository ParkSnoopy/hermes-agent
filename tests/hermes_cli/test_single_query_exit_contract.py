"""One-shot ``chat -q`` runs report their outcome in the exit code, like ``-Q``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import cli


def _run_non_quiet(monkeypatch, turn_result):
    """Drive the non-quiet tail; return the exit code (None means fell through)."""
    monkeypatch.setattr(cli, "_should_seed_interactive", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_collect_query_images", lambda q, i: (q, []))
    monkeypatch.setattr(cli, "_finalize_single_query", lambda c: None)
    stub = SimpleNamespace(
        _single_query_mode=False,
        _claim_active_session=lambda *a, **k: True,
        console=SimpleNamespace(print=lambda *a, **k: None),
        _show_security_advisories=lambda: None,
        chat=lambda *a, **k: "response",
        _print_exit_summary=lambda **k: None,
        _last_turn_result=turn_result,
    )
    try:
        cli._run_single_query_mode(stub, "do the thing", None, False, True)
    except SystemExit as exc:
        return exc.code
    return None


@pytest.mark.parametrize(
    "reason", ["rate_limit", "upstream_rate_limit", "billing", "overloaded", "server_error", "timeout",
               "auth", "auth_permanent", "model_not_found", "ssl_cert_verification", "upstream_blocked",
               "some_unknown_reason"]
)
def test_one_shot_provider_failure_exits_nonzero(monkeypatch, reason):
    assert _run_non_quiet(monkeypatch, {"failed": True, "failure_reason": reason}) == 1


@pytest.mark.parametrize(
    ("turn_result", "expected"),
    [
        ({"final_response": "done", "completed": True}, 0),
        ({"failed": True, "failure_reason": "rate_limit"}, 1),
        ({"failed": True}, 1),
        ({"final_response": "half", "completed": False, "partial": True}, 1),
        ({"completed": False, "interrupted": True}, 130),
        (None, 1),
    ],
)
def test_a_plain_one_shot_run_reports_its_outcome(monkeypatch, turn_result, expected):
    assert _run_non_quiet(monkeypatch, turn_result) == expected


@pytest.mark.parametrize("rate_limited", [True, False])
def test_quiet_one_shot_exits_nonzero_when_credentials_fail(monkeypatch, rate_limited):
    monkeypatch.setattr(cli, "_should_seed_interactive", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_collect_query_images", lambda q, i: (q, []))
    monkeypatch.setattr(cli, "_finalize_single_query", lambda c: None)
    stub = SimpleNamespace(
        _claim_active_session=lambda *a, **k: True,
        _ensure_runtime_credentials=lambda: False,
        _credentials_rate_limited=rate_limited,
        session_id="s1",
        model="gpt-x",
    )
    with pytest.raises(SystemExit) as exc:
        cli._run_single_query_mode(stub, "do the thing", None, True, True)
    assert exc.value.code == 1
