"""Bot Chat delivery fails explicitly when the canonical owner is unavailable."""
from unittest.mock import Mock

from cron import scheduler_delivery as delivery
from tools import bot_live_delivery as mailbox


def test_bot_chat_target_without_live_owner_is_unverified(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(mailbox, "read_delivery_result", lambda home, key: None)
    monkeypatch.setattr(mailbox, "find_canonical_live_owner",
                        Mock(side_effect=ValueError("profile authority is not ready")))
    job = dict(id="digest", name="Digest", execution_id="run-1", deliver="bot-chat")
    error = delivery._deliver_to_bot_chat(job, "the report", "")
    assert error is not None
    assert "unverified" in error
    assert "profile authority is not ready" in error
    assert not (tmp_path / "cron" / "bot_chat_pending").exists()
