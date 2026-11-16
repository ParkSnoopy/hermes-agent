"""Hygiene adopt step: an uncommitted attempt keeps the transcript, a committed in-place one is adopted (#71097).

Both tests drive the production adopt path (``_hmwa_hygiene_adopt_transcript``), the only
reader of the compressor's ``_last_compaction_in_place`` flag on the hygiene route.
"""

import logging
from types import SimpleNamespace

import pytest

from gateway.run_turn import GatewayTurnMixin

_HISTORY = [{"role": "user", "content": "x" * 400}, {"role": "assistant", "content": "y" * 400}] * 4
_COMPRESSED = [{"role": "user", "content": "summary"}, {"role": "assistant", "content": "ok"}]


def _agent(**signals):
    base = {
        "session_id": "sid-1",
        "_last_compaction_in_place": False,
        "_last_compression_attempt_recorded": True,
        "_last_compression_attempt_in_place": None,
        "_compression_skipped_due_to_lock": None,
        "_compression_blocked_transient": None,
        "_last_compression_timed_out": False,
        "_last_compression_summary_warning": None,
        "_session_db": object(),
    }
    base.update(signals)
    return SimpleNamespace(**base)


async def _adopt(agent, caplog):
    """Run the gateway adopt step for an unrotated hygiene attempt; returns (attempt, entry, result)."""
    runner = GatewayTurnMixin.__new__(GatewayTurnMixin)
    attempt = SimpleNamespace(agent=agent, history=_HISTORY)
    plan = SimpleNamespace(msg_count=len(_HISTORY), approx_tokens=4_000, warn_token_threshold=10**9)
    entry = SimpleNamespace(session_id="sid-1", last_prompt_tokens=4_000)
    with caplog.at_level(logging.WARNING, logger="gateway.run_turn"):
        result = await runner._hmwa_hygiene_adopt_transcript(
            attempt, _COMPRESSED, _HISTORY, plan, session_entry=entry, source=None, _quick_key=None, run_generation=0,
        )
    return attempt, entry, result


@pytest.mark.asyncio
async def test_aborted_attempt_on_db_backed_agent_keeps_the_transcript(caplog):
    # codex_app_server thread interrupted / summary aborted: the compressor never reached the
    # commit boundary, so `_last_compression_attempt_in_place` stays None while `_session_db` is real.
    attempt, entry, (rotated, in_place, count, tokens) = await _adopt(_agent(), caplog)
    assert (rotated, in_place, count, tokens) == (False, False, len(_HISTORY), 4_000)
    assert attempt.history is _HISTORY and entry.last_prompt_tokens == 4_000


@pytest.mark.asyncio
async def test_committed_in_place_compaction_is_adopted_not_warned_about(caplog):
    # A committed in-place attempt reaches adoption with `_last_compaction_in_place` set,
    # even though it did not rotate the session.
    agent = _agent(_last_compaction_in_place=True, _last_compression_attempt_in_place=True)
    attempt, entry, (rotated, in_place, count, _tokens) = await _adopt(agent, caplog)
    assert (rotated, in_place, count) == (False, True, len(_COMPRESSED))
    assert attempt.history is _COMPRESSED and entry.last_prompt_tokens == 0
    assert "did not rotate or compact in place" not in caplog.text
