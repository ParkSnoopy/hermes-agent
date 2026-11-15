"""Messaging /refine preserves the stored transcript after agent eviction."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from gateway.run import GatewayRunner


@pytest.mark.asyncio
async def test_cold_refine_spawns_explicit_review_and_delivers_summary(monkeypatch):
    from contextlib import contextmanager
    from gateway.session_cold_review import start_cold_refine
    from tests.gateway.test_platform_reconnect import register_test_platform

    platform = register_test_platform(monkeypatch)
    source = SessionSource(platform=platform, chat_id='chat', thread_id='thread', chat_type='group')
    adapter = SimpleNamespace(send=AsyncMock())
    runner = SimpleNamespace(_delivery_adapter_for=lambda source: adapter,
                             _thread_metadata_for_source=lambda source: {'thread_id': source.thread_id})
    parent = SimpleNamespace(valid_tool_names={'skill_manage'}, _spawn_background_review=MagicMock())
    @contextmanager
    def cold_parent(*args):
        yield parent
    monkeypatch.setattr('gateway.session_cold_review.cold_review_parent', cold_parent)
    monkeypatch.setattr('agent.memory_provider.spawn_context_thread',
                        lambda body, **kwargs: SimpleNamespace(start=body))
    history = [{'role': 'user', 'content': 'stored'}]
    await start_cold_refine(runner, source, 'route', 'session', history, 'focus')
    parent._spawn_background_review.assert_called_once_with(
        messages_snapshot=history, review_memory=True, review_skills=True,
        focus='focus', explicit=True)
    parent.background_review_callback('summary')
    import asyncio
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    adapter.send.assert_awaited_once()
    assert adapter.send.await_args.args == ('chat', 'summary')
    assert adapter.send.await_args.kwargs['metadata']['thread_id'] == 'thread'


@pytest.mark.asyncio
@pytest.mark.parametrize('focus', ['', 'cold pass'])
async def test_refine_without_cached_agent_reads_stored_conversation(monkeypatch, focus):
    history = [{'role': 'user', 'content': 'OPENED_PR_42'},
               {'role': 'assistant', 'content': 'Noted.'}]
    runner = object.__new__(GatewayRunner)
    runner._running_agents = {}
    runner._session_key_for_source = lambda source: 'route'
    runner._cached_agent_for = lambda key: None
    runner.session_store = MagicMock()
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store, peek_session_id=AsyncMock(return_value='session'),
        load_transcript=AsyncMock(return_value=history),
    )
    cold = AsyncMock()
    monkeypatch.setattr('gateway.session_refine.refine_local_session', AsyncMock(return_value=None))
    monkeypatch.setattr('gateway.session_cold_review.start_cold_refine', cold)
    from tests.gateway.test_platform_reconnect import register_test_platform
    source = SessionSource(platform=register_test_platform(monkeypatch), chat_id='chat', chat_type='dm')
    response = await runner._handle_refine_command(MessageEvent(text='/refine ' + focus, source=source))
    assert response and 'nothing' not in response.lower()
    cold.assert_awaited_once_with(runner, source, 'route', 'session', history, focus)
    runner.async_session_store.load_transcript.assert_awaited_once_with('session')


@pytest.mark.asyncio
@pytest.mark.parametrize('focus', ['', 'resident pass'])
async def test_refine_resident_agent_is_explicit(monkeypatch, focus):
    history = [{'role': 'user', 'content': 'OPENED_PR_42'}]
    agent = SimpleNamespace(_session_messages=history, valid_tool_names={'skill_manage'},
                            _spawn_background_review=MagicMock())
    runner = object.__new__(GatewayRunner)
    runner._running_agents = {}
    runner._session_key_for_source = lambda source: 'route'
    runner._cached_agent_for = lambda key: agent
    from tests.gateway.test_platform_reconnect import register_test_platform
    source = SessionSource(platform=register_test_platform(monkeypatch), chat_id='chat', chat_type='dm')
    response = await runner._handle_refine_command(MessageEvent(text='/refine ' + focus, source=source))
    assert response and 'nothing' not in response.lower()
    agent._spawn_background_review.assert_called_once_with(
        messages_snapshot=history, review_memory=True, review_skills=True,
        focus=focus or None, explicit=True)
