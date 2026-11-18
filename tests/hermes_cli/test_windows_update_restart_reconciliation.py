"""Linux pause outcomes are merged into shared restart reconciliation."""

from types import SimpleNamespace

import pytest

from hermes_cli import update_cmd_posix_pause as pause
from hermes_cli import update_receipt


@pytest.mark.parametrize("fails", [False, True])
def test_merge_resume_outcomes_and_failure(monkeypatch, fails):
    outcome = SimpleNamespace(incomplete=False, phase_errors=[], restarted_services=["existing"],
                              failed_or_stale_units=[], relaunched_profiles=["profile"],
                              externally_supervised_profiles=[], killed_pids=set())
    token = {"relaunched_profiles": ["profile", "other"],
             "restarted_services": ["existing", "unit"]}
    receipts = []

    def resume(_token):
        if fails:
            raise RuntimeError("restart failed")

    monkeypatch.setattr(pause, "resume_gateways_after_update", resume)
    monkeypatch.setattr(update_receipt, "record_gateway_restart", lambda **kw: receipts.append(kw))
    pause.resume_and_merge_outcome(outcome, token, False)

    assert outcome.relaunched_profiles == ["profile", "other"]
    assert outcome.restarted_services == ["existing", "unit"]
    assert outcome.incomplete is fails
    assert outcome.phase_errors == (["restart failed"] if fails else [])
    assert receipts[0]["incomplete"] is fails