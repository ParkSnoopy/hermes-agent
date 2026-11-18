"""Startup recovery never writes to the stdout of the command it runs ahead of.

``update_pause_record.recover()`` resumes gateways an interrupted update paused before ANY ``hermes``
command (``--json`` ones included) and prints its own notices to stderr; the resume path it
calls must do the same. Real resume entry point; the seam is the tree gate's
verdict (a torn checkout), so the deferral notice is what gets printed.
"""

from __future__ import annotations

import pytest

from hermes_cli import update_pause_record as pause_record
from hermes_cli.update_cmd_posix_pause import resume_gateways_after_update


def test_a_recovering_resume_prints_its_notices_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(pause_record, "tree_is_whole", lambda token: (False, "the checkout is mid-pull"))
    monkeypatch.setattr(pause_record, "sync", lambda token: None)
    token = {"platform": "posix", "resume_needed": True, "pause_id": "p", "recovery": True, "profiles": {"default": 4242}}

    resume_gateways_after_update(token)
    out, err = capsys.readouterr()
    assert out == "", "recovery wrote to the unrelated command's stdout"
    assert "left stopped: the checkout is mid-pull" in err
    assert token["resume_deferred"] == "the checkout is mid-pull"


def test_resume_invokes_linux_owner_and_checkpoints_even_on_failure(monkeypatch):
    from hermes_cli import update_cmd_posix_pause as pause

    calls = []
    token = {"platform": "posix", "resume_needed": True, "pause_id": "p"}
    monkeypatch.setattr(pause_record, "tree_is_whole", lambda token: (True, ""))
    monkeypatch.setattr(pause_record, "sync", lambda token: calls.append("sync"))

    def fail(token):
        calls.append("resume")
        raise RuntimeError("restart not verified")

    monkeypatch.setattr(pause, "resume_paused_set", fail)
    with pytest.raises(RuntimeError, match="restart not verified"):
        pause.resume_gateways_after_update(token)
    assert calls == ["resume", "sync"]
    assert token["resume_needed"] is True


def test_resume_rejects_unknown_platform_without_clearing_debt():
    token = {"platform": "unsupported", "resume_needed": True}
    with pytest.raises(ValueError, match="unsupported gateway pause"):
        resume_gateways_after_update(token)
    assert token["resume_needed"] is True


def test_resume_merge_preserves_verified_linux_units(monkeypatch):
    from types import SimpleNamespace
    from hermes_cli import update_cmd_posix_pause as pause, update_receipt

    monkeypatch.setattr(pause, "resume_gateways_after_update", lambda token: None)
    monkeypatch.setattr(update_receipt, "record_gateway_restart", lambda **kwargs: None)
    outcome = SimpleNamespace(relaunched_profiles=[], restarted_services=["existing"],
                              externally_supervised_profiles=[], killed_pids=set(),
                              failed_or_stale_units=[], incomplete=False, phase_errors=[])
    pause.resume_and_merge_outcome(outcome, {"restarted_services": ["gateway", "gateway"]}, False)
    assert outcome.restarted_services == ["existing", "gateway"]
