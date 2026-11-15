"""Gateway status keys isolate lanes, generations and event types."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import SendResult
from gateway.run_turn_runner import TurnRunner
from gateway.turn_context import TurnContext


@pytest.mark.asyncio
@pytest.mark.parametrize("other_lane,other_generation,other_event,thread", [
    ("topic-a", 2, "lifecycle", "1001"),
    ("topic-b", 1, "lifecycle", "1002"),
    ("topic-a", 1, "model-switch", "1001"),
])
async def test_gateway_status_belongs_to_turn_lane_and_event(
    monkeypatch, other_lane, other_generation, other_event, thread,
):
    pending = []
    adapter = SimpleNamespace(send_or_update_status=AsyncMock(return_value=SendResult(success=True)))
    monkeypatch.setattr(TurnRunner, "_schedule", lambda self, coro, message: pending.append(coro))

    async def emit(lane, generation, event="lifecycle", thread_id="1001"):
        ctx = TurnContext(
            source=SimpleNamespace(platform=Platform.WEBHOOK),
            session_key=lane, run_generation=generation, _run_still_current=lambda: True,
            _status_adapter=adapter, _status_chat_id="chat",
            _status_thread_metadata={"thread_id": thread_id}, user_config={},
        )
        TurnRunner(MagicMock(), ctx)._status_callback_sync(event, "Recalling memories")
        assert len(pending) == 1
        assert (await pending.pop()).success
        return adapter.send_or_update_status.await_args

    first = await emit("topic-a", 1)
    assert (await emit("topic-a", 1)).args[1] == first.args[1]
    second = await emit(other_lane, other_generation, other_event, thread)
    assert second.args[1] != first.args[1]
    assert second.kwargs["metadata"] == {"thread_id": thread}
    assert (await emit(other_lane, other_generation, other_event, thread)).args[1] == second.args[1]
    incomplete = await emit(None, 1)
    assert (await emit("topic-a", None)).args[1] == incomplete.args[1]
    assert (await emit("topic-a", 0)).args[1] != incomplete.args[1]
