"""A wake arriving during restart drain must be refused so its producer can retry."""

import asyncio

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.wake import WakeNotAccepted, deliver_wake
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


async def drain(adapter):
    while adapter._background_tasks:
        await asyncio.gather(*list(adapter._background_tasks), return_exceptions=True)
        await asyncio.sleep(0)


def wake_route():
    runner, adapter = make_restart_runner()
    adapter.platform = Platform.LOCAL
    adapter.gateway_runner = runner
    adapter.set_message_handler(runner._handle_message)
    runner._delivery_adapter_for = lambda source: adapter
    turns = []

    async def agent_turn(event, source, quick_key, run_generation):
        turns.append(event.text)

    runner._handle_message_with_agent = agent_turn
    source = make_restart_source()
    source.platform = Platform.LOCAL
    return runner, adapter, source, turns


@pytest.mark.asyncio
async def test_refused_wake_can_be_retried_after_drain(monkeypatch):
    runner, adapter, source, _turns = wake_route()
    accepted = []

    async def record(event):
        accepted.append(event.text)
        event._gateway_accepted = True

    monkeypatch.setattr(adapter, "handle_message", record)
    runner._draining = True
    with pytest.raises(WakeNotAccepted) as refused:
        await deliver_wake(adapter, text="Background process completed.", source=source)
    assert refused.value.reason == "runtime_draining" and accepted == []
    runner._draining = False
    await deliver_wake(adapter, text="Background process completed.", source=source)
    assert accepted == ["Background process completed."]


@pytest.mark.asyncio
async def test_wake_for_a_busy_session_is_refused_while_draining():
    runner, adapter, source, _turns = wake_route()
    key = adapter._event_session_key(MessageEvent(text="", source=source))
    adapter._active_sessions[key] = asyncio.Event()
    runner._draining = True
    with pytest.raises(WakeNotAccepted) as refused:
        await deliver_wake(adapter, text="Background process completed.", source=source)
    assert refused.value.reason == "runtime_draining" and not refused.value.permanent
    assert key not in adapter._pending_messages


@pytest.mark.asyncio
async def test_wake_is_refused_when_drain_starts_during_admission(monkeypatch):
    runner, adapter, source, turns = wake_route()
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_admission(event):
        entered.set()
        await release.wait()
        event._gateway_accepted = True

    monkeypatch.setattr(adapter, "handle_message", slow_admission)
    admission = asyncio.create_task(deliver_wake(adapter, text="Background process completed.", source=source))
    await asyncio.wait_for(entered.wait(), 5)
    runner._draining = True
    release.set()
    with pytest.raises(WakeNotAccepted) as refused:
        await admission
    assert refused.value.reason == "runtime_draining"
    await drain(adapter)
    assert turns == []


@pytest.mark.asyncio
async def test_wake_is_refused_when_drain_starts_after_the_busy_handler_queued_it():
    """A queued follow-up doomed by drain must be refused and retried."""
    runner, adapter, source, _turns = wake_route()
    key = adapter._event_session_key(MessageEvent(text="", source=source))
    adapter._active_sessions[key] = asyncio.Event()
    store = adapter._busy_session_handler

    async def store_then_drain(event, session_key):
        handled = await store(event, session_key)
        runner._draining = True
        return handled

    adapter._busy_session_handler = store_then_drain
    with pytest.raises(WakeNotAccepted) as refused:
        await deliver_wake(adapter, text="Background process completed.", source=source)
    assert "during admission" in str(refused.value)
    event, text = await runner._run_agent_drain_pending({"final_response": "done"}, adapter, source, key)
    assert (event, text) == (None, None)
