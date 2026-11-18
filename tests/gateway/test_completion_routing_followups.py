"""Completion ownership and durable routing regressions."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from gateway.config import Platform
from gateway.run import GatewayRunner, _USER_BOUNDARY_END_REASONS
from gateway.session import AsyncSessionStore, SessionEntry
from tests.gateway.test_automation_compression_media import _plugin_platform

@pytest.fixture(autouse=True)
def registered_chat(monkeypatch):
    _plugin_platform(monkeypatch)

def _entry(session_id, session_key="agent:main:test_chat:dm:U1"):
    return SessionEntry(
        session_key=session_key,
        session_id=session_id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform('test_chat'),
        chat_type="dm",
    )



def _runner_with_rows(rows, *, switched_entry=None):
    runner = object.__new__(GatewayRunner)
    db = MagicMock()
    db.get_session = AsyncMock(side_effect=lambda session_id: rows.get(session_id))
    db.get_compression_tip = AsyncMock(return_value=None)
    runner._session_db = db
    runner.session_store = MagicMock()
    runner.session_store.switch_session = MagicMock(return_value=switched_entry)
    runner.session_store.advance_compression_session = MagicMock(
        return_value=switched_entry
    )
    runner._async_session_store = AsyncSessionStore(runner.session_store)
    return runner



@pytest.mark.asyncio
@pytest.mark.parametrize("end_reason", ["idle", "idle_timeout", "timeout", None, ""])
async def test_resolver_retargets_idle_ended_pin_to_current_session(end_reason):
    """The delivery leg the classifier's "deliver" verdict promises: an
    idle-ended pin must resolve to the chat's CURRENT session, not drop."""
    current = _entry("sess_current")
    runner = _runner_with_rows(
        {
            "sess_idle": {
                "id": "sess_idle",
                "ended_at": "2026-08-09T00:00:00",
                "end_reason": end_reason,
            }
        }
    )

    resolved = await runner._resolve_async_delegation_session(
        current, "sess_idle"
    )

    assert resolved is current, (
        f"end_reason={end_reason!r}: idle-ended pin must retarget to the "
        f"chat's current session (got {resolved!r}); dropping here after "
        "the classifier said 'deliver' acks the durable row for a message "
        "that never went out"
    )



@pytest.mark.asyncio
@pytest.mark.parametrize("end_reason", sorted(_USER_BOUNDARY_END_REASONS))
async def test_resolver_still_drops_user_boundary_ends(end_reason):
    current = _entry("sess_current")
    runner = _runner_with_rows(
        {
            "sess_closed": {
                "id": "sess_closed",
                "ended_at": "2026-08-09T00:00:00",
                "end_reason": end_reason,
            }
        }
    )
    resolved = await runner._resolve_async_delegation_session(
        current, "sess_closed"
    )
    assert resolved is None



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "end_reason",
    ["idle", "idle_timeout", "timeout", "", None, "agent_close", "cron_complete"],
)
async def test_classifier_and_resolver_agree_on_ended_parents(end_reason):
    """Coherence invariant: whenever the pre-flight classifier says
    "deliver" for an ended parent, the in-pipeline resolver must actually
    deliver (return a session), and when it says "terminal" the resolver
    must drop. Divergence in the deliver->drop direction acks the durable
    row for an injection the pipeline then discards."""
    row = {
        "id": "sess_x",
        "ended_at": "2026-08-09T00:00:00",
        "end_reason": end_reason,
    }
    current = _entry("sess_current")
    runner = _runner_with_rows({"sess_x": row})

    verdict = await runner._classify_completion_target("sess_x")
    resolved = await runner._resolve_async_delegation_session(current, "sess_x")

    if verdict == "deliver":
        assert resolved is not None, (
            f"classifier said deliver for end_reason={end_reason!r} but the "
            "resolver dropped — durable row would be falsely acknowledged"
        )
    elif verdict == "terminal":
        assert resolved is None



def _fallback_runner():
    runner = object.__new__(GatewayRunner)
    store = MagicMock()
    store._ensure_loaded = MagicMock(side_effect=RuntimeError("store down"))
    store._entries = {}
    runner.session_store = store
    runner._session_sources = None
    return runner



def test_fallback_source_reconstruction_carries_scope_id():
    """When the session-store origin is unavailable, the reconstructed
    SessionSource must carry the event's scope_id and tenant discriminator."""
    runner = _fallback_runner()
    evt = {
        "session_key": "agent:main:test_chat:group:C123:U9",
        "platform": "test_chat",
        "chat_type": "group",
        "chat_id": "C123",
        "user_id": "U9",
        "scope_id": "G777",
        "type": "async_delegation",
    }
    source = runner._build_process_event_source(evt)
    assert source is not None
    assert source.scope_id == "G777"
    assert source.user_id == "U9"



def test_fallback_source_reconstruction_without_scope_still_routes():
    """Absent scope_id must not fail the reconstruction (DMs and
    author-bound scoped chats still route via user_id)."""
    runner = _fallback_runner()
    evt = {
        "session_key": "agent:main:test_chat:dm:D42",
        "platform": "test_chat",
        "chat_type": "dm",
        "chat_id": "D42",
        "user_id": "U1",
        "type": "async_delegation",
    }
    source = runner._build_process_event_source(evt)
    assert source is not None
    assert source.scope_id is None
    assert source.user_id == "U1"



def test_durable_dispatch_persists_and_recovers_scope_id(tmp_path, monkeypatch):
    """End-to-end restart shape: dispatch with a scoped session context bound,
    simulate owner death, recover — the recovered completion event must carry
    scope_id/user_id, and the reconstructed SessionSource must prime them."""
    import tools.async_delegation as ad
    from gateway.session_context import clear_session_vars, set_session_vars

    ad._reset_for_tests()
    monkeypatch.setattr(ad, "_db_path", lambda: tmp_path / "state.db")

    tokens = set_session_vars(
        platform="test_chat",
        chat_id="C123",
        chat_type="group",
        user_id="U9",
        scope_id="G777",
        session_key="agent:main:test_chat:group:C123:U9",
    )
    try:
        record = {
            "delegation_id": "d-scope-1",
            "session_key": "agent:main:test_chat:group:C123:U9",
            "origin_ui_session_id": "",
            "origin_session_id": "",
            "parent_session_id": "sess-p",
            "goal": "scoped goal",
            "dispatched_at": 100.0,
            **ad._capture_routing_origin(),
        }
        assert record.get("scope_id") == "G777", (
            "dispatch-time capture must snapshot HERMES_SESSION_SCOPE_ID"
        )
        ad._persist_dispatch(record)
    finally:
        clear_session_vars(tokens)

    # Simulate the owner process being gone: recovery marks the row unknown
    # and rebuilds the completion event from the durable task_json.
    monkeypatch.setattr("gateway.status._pid_exists", lambda pid: False)
    ad.recover_abandoned_delegations()

    with ad._transaction() as conn:
        row = conn.execute(
            "SELECT event_json FROM async_delegations WHERE delegation_id='d-scope-1'"
        ).fetchone()
    assert row and row[0], "recovered row must have an event_json"
    import json as _json

    evt = _json.loads(row[0])
    assert evt.get("scope_id") == "G777", (
        "recovered completion event lost the scoped routing origin"
    )
    assert evt.get("user_id") == "U9"

    # The gateway-side fallback reconstruction must carry it into the source.
    runner = _fallback_runner()
    source = runner._build_process_event_source(evt)
    assert source is not None
    assert source.scope_id == "G777"
    assert source.user_id == "U9"



def test_live_completion_event_carries_scope_id(tmp_path, monkeypatch):
    """The live (non-restart) completion event must carry the dispatch-time
    routing origin too, so priming works even when the in-memory source
    cache was evicted."""
    import tools.async_delegation as ad

    record = {
        "delegation_id": "d-live-1",
        "session_key": "agent:main:test_chat:group:C123:U9",
        "scope_id": "G777",
        "user_id": "U9",
        "goal": "g",
        "dispatched_at": 100.0,
        "completed_at": 101.0,
    }

    captured = {}

    class _Q:
        def put(self, evt):
            captured.update(evt)

    class _PR:
        completion_queue = _Q()

    monkeypatch.setattr(ad, "_db_path", lambda: tmp_path / "state.db")
    monkeypatch.setattr(
        "tools.process_registry.process_registry", _PR(), raising=False
    )
    ad._push_completion_event(record, {"summary": "ok"}, "completed")
    assert captured.get("scope_id") == "G777"
    assert captured.get("user_id") == "U9"

