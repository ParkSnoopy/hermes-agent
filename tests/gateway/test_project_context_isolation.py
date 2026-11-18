"""Project context isolation and resume authorization across API sessions."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.session import SessionContext, SessionEntry, SessionSource, build_session_key
from hermes_state import AsyncSessionDB

PROJECT_A_ROOM_ID = "project-a"
PROJECT_B_ROOM_ID = "project-b"
PROJECT_A_NAME = "Project - Project A"
PROJECT_B_NAME = "Project - Project B"
PROJECT_A_TOPIC = "Architecture and deploy plan for Project A"
PROJECT_B_TOPIC = "Migration and branch plan for Project B"
SENDER = "alice"


def _source(room_id, room_name, topic):
    return SessionSource(
        platform=Platform.API_SERVER, chat_id=room_id, chat_name=room_name,
        chat_type="group", user_id=SENDER, user_name="Alice", chat_topic=topic,
    )


def _entry(source, session_id, title=None):
    return SessionEntry(
        session_key=build_session_key(source), session_id=session_id,
        created_at=datetime.now(), updated_at=datetime.now(), origin=source,
        display_name=title or source.chat_name, platform=Platform.API_SERVER,
        chat_type="group",
    )


def _make_runner(current_source, entries):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.API_SERVER: PlatformConfig(enabled=True)})
    runner.adapters = {}
    runner.session_store = MagicMock()
    runner.session_store._entries = {entry.session_key: entry for entry in entries}
    current = next((e for e in entries if e.origin and e.origin.chat_id == current_source.chat_id), entries[0])
    runner.session_store.get_or_create_session.return_value = current
    runner.session_store.switch_session.return_value = current
    runner.session_store.load_transcript.return_value = [{"role": "user", "content": "hello"}]
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._release_running_agent_state = MagicMock()
    runner._clear_session_boundary_security_state = MagicMock()
    runner._evict_cached_agent = MagicMock()
    runner._queue_depth = MagicMock(return_value=0)
    runner._session_db = AsyncSessionDB(MagicMock())
    runner._session_db._db.list_sessions_rich.return_value = [
        {"id": entry.session_id, "title": entry.display_name, "preview": ""}
        for entry in entries
    ]
    runner._session_db._db.resolve_resume_session_id.side_effect = lambda sid: sid
    runner._session_db._db.get_session_title.side_effect = lambda sid: {
        entry.session_id: entry.display_name for entry in entries
    }.get(sid)
    runner._session_db._db.get_session.return_value = None
    return runner


def _event(text, source):
    return MessageEvent(text=text, source=source, message_id="cmd")


@pytest.mark.asyncio
async def test_project_context_survives_concurrent_messages():
    from gateway.run import GatewayRunner
    from gateway.session_context import get_session_env

    async def observe(source):
        context = SessionContext(
            source=source, connected_platforms=[Platform.API_SERVER], home_channels={},
            session_key=build_session_key(source), session_id="session-test",
        )
        runner = object.__new__(GatewayRunner)
        tokens = runner._set_session_env(context)
        try:
            await asyncio.sleep(0)
            return SimpleNamespace(
                chat_id=get_session_env("HERMES_SESSION_CHAT_ID"),
                chat_name=get_session_env("HERMES_SESSION_CHAT_NAME"),
                session_key=get_session_env("HERMES_SESSION_KEY"),
            )
        finally:
            runner._clear_session_env(tokens)

    observed_a, observed_b = await asyncio.gather(
        observe(_source(PROJECT_A_ROOM_ID, PROJECT_A_NAME, PROJECT_A_TOPIC)),
        observe(_source(PROJECT_B_ROOM_ID, PROJECT_B_NAME, PROJECT_B_TOPIC)),
    )
    assert observed_a.chat_id == PROJECT_A_ROOM_ID
    assert observed_b.chat_id == PROJECT_B_ROOM_ID
    assert observed_a.chat_name == PROJECT_A_NAME
    assert observed_b.chat_name == PROJECT_B_NAME
    assert observed_a.session_key != observed_b.session_key


@pytest.mark.asyncio
async def test_resume_quoted_title_same_room():
    source_b = _source(PROJECT_B_ROOM_ID, PROJECT_B_NAME, PROJECT_B_TOPIC)
    entry_b = _entry(source_b, "session-b-old", "Project B Plan")
    runner = _make_runner(source_b, [entry_b])
    runner.session_store.get_or_create_session.return_value = _entry(
        source_b, "session-b-current", "Current Project B"
    )
    runner.session_store.switch_session.return_value = entry_b
    runner._session_db._db.resolve_session_by_title.return_value = "session-b-old"

    result = await runner._handle_resume_command(_event('/resume "Project B Plan"', source_b))

    assert "Resumed session" in result
    runner._session_db._db.resolve_session_by_title.assert_called_once_with("Project B Plan")


@pytest.mark.asyncio
async def test_resume_cross_room_requires_explicit_admin_override():
    source_a = _source(PROJECT_A_ROOM_ID, PROJECT_A_NAME, PROJECT_A_TOPIC)
    source_b = _source(PROJECT_B_ROOM_ID, PROJECT_B_NAME, PROJECT_B_TOPIC)
    entry_a = _entry(source_a, "session-a", "Project A Plan")
    entry_b = _entry(source_b, "session-b", "Project B Plan")
    runner = _make_runner(source_b, [entry_a, entry_b])
    runner.session_store.switch_session.return_value = entry_a
    runner._session_db._db.resolve_session_by_title.return_value = "session-a"
    runner._resume_caller_is_admin = MagicMock(return_value=True)

    denied = await runner._handle_resume_command(_event("/resume Project A Plan", source_b))
    assert "Resumed session" not in denied
    runner.session_store.switch_session.assert_not_called()

    result = await runner._handle_resume_command(_event("/resume --cross-room Project A Plan", source_b))
    assert "Resumed session" in result
    runner.session_store.switch_session.assert_called_once()