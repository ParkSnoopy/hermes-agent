"""DM-only blocked-overflow warnings stay out of shared chats and handoff homes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.conversation_compression import CONTEXT_OVERFLOW_BLOCKED_WARNING_TEMPLATE
from gateway.session import SessionSource
from tests.gateway.test_handoff_thread_session_key import _handoff_destination
from tests.gateway.test_automation_compression_media import _plugin_platform

WARNING = CONTEXT_OVERFLOW_BLOCKED_WARNING_TEMPLATE.format(tokens=85_000, threshold=72_000, reason="cooldown:30")


def _delivered(source):
    """Run the production status callback for *source*; return what reached the chat."""
    from gateway.run_turn_runner import TurnRunner
    from gateway.turn_context import TurnContext

    sent = []

    class Transport:
        async def send(self, chat_id, text, metadata=None):
            sent.append(text)

    async def exercise():
        loop = asyncio.get_running_loop()
        ctx = TurnContext(source=source, user_config={}, _run_still_current=lambda: True, _loop_for_step=loop)
        ctx._status_adapter, ctx._status_chat_id, ctx._status_thread_metadata = Transport(), source.chat_id, None
        runner = TurnRunner(None, ctx)

        def emit():
            runner._status_callback_sync("warn", WARNING)
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(timeout=5)

        await asyncio.to_thread(emit)

    asyncio.run(exercise())
    return sent


@pytest.mark.parametrize(("chat_type", "shown"), [("group", False), ("dm", True)])
def test_warning_only_reaches_private_chats(monkeypatch, chat_type, shown):
    source = SessionSource(platform=_plugin_platform(monkeypatch), chat_id="chat", chat_type=chat_type)
    assert _delivered(source) == ([WARNING] if shown else [])


@pytest.mark.parametrize(("home_type", "shown"), [("channel", False), ("group", False), ("dm", True)])
def test_a_handoff_without_a_thread_keeps_the_home_chats_type(monkeypatch, home_type, shown):
    platform = _plugin_platform(monkeypatch)
    adapter = SimpleNamespace(get_chat_info=AsyncMock(return_value={"type": home_type}))
    dest, _ = _handoff_destination(platform, "home-1", None, None, adapter)
    assert _delivered(dest.source) == ([WARNING] if shown else [])
