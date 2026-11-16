"""Regression tests: a slow gateway handoff dispatch must not be misreported
as "gateway not running" — and no waiter may stomp a claimed (running) row.

Bug shape (live-reproduced on main @1c5ee5815f): /handoff poll-waited a flat
60s for a TERMINAL state. The gateway watcher claims within seconds, but the
dispatch is a FULL synthetic agent turn (whole transcript replay + delivery)
that routinely exceeds 60s. The CLI then printed "Timed out waiting for the
gateway. Is `hermes gateway` running?" (false diagnosis), called
fail_handoff() on the RUNNING row (stomping the gateway's claim), and claimed
"Your CLI session is intact" after switch_session had already re-pointed the
session. The gateway later overwrote failed -> completed: split-brain.

Fix under test:
  1. SessionDB.fail_handoff(only_states=...) — CAS: waiters can only fail
     rows still in the given states.
  2. CLI _handle_handoff_command: 60s deadline applies only to PENDING;
     a RUNNING row gets a long (15 min) wait and is never failed by the CLI.
"""

from __future__ import annotations


import pytest


from hermes_state import SessionDB


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    d = SessionDB(db_path=tmp_path / "state.db")
    yield d
    d.close()


# ---------------------------------------------------------------------------
# 1. State layer: fail_handoff CAS
# ---------------------------------------------------------------------------

class TestFailHandoffCAS:
    def test_cas_fails_pending_row(self, db):
        db.ensure_session("s1", "cli")
        assert db.request_handoff("s1", "discord")
        assert db.fail_handoff("s1", "timed out", only_states=("pending",)) is True
        assert db.get_handoff_state("s1")["state"] == "failed"

    def test_cas_refuses_running_row(self, db):
        """A waiter timeout must NOT stomp a row the gateway has claimed."""
        db.ensure_session("s2", "cli")
        assert db.request_handoff("s2", "discord")
        assert db.claim_handoff("s2")  # gateway claimed: pending -> running
        assert db.fail_handoff("s2", "timed out", only_states=("pending",)) is False
        assert db.get_handoff_state("s2")["state"] == "running"
        # gateway still reaches its own terminal state
        db.complete_handoff("s2")
        assert db.get_handoff_state("s2")["state"] == "completed"

    def test_unconditional_fail_still_available_to_owner(self, db):
        """The gateway watcher (owner of a claimed row) fails unconditionally."""
        db.ensure_session("s3", "cli")
        assert db.request_handoff("s3", "discord")
        assert db.claim_handoff("s3")
        assert db.fail_handoff("s3", "dispatch raised") is True
        assert db.get_handoff_state("s3")["state"] == "failed"

    def test_cas_refuses_terminal_rows(self, db):
        db.ensure_session("s4", "cli")
        assert db.request_handoff("s4", "discord")
        assert db.claim_handoff("s4")
        db.complete_handoff("s4")
        assert db.fail_handoff("s4", "late timeout", only_states=("pending",)) is False
        assert db.get_handoff_state("s4")["state"] == "completed"
