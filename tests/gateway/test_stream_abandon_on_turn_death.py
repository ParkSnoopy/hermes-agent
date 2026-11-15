"""A cancelled or stale turn abandons a generic adapter draft without claiming delivery."""
import asyncio

import pytest

from gateway.config import Platform
from gateway.platforms.base import SendResult
from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig
from tests.gateway.test_platform_reconnect import StubAdapter, register_test_platform


class DraftAdapter(StubAdapter):
    def __init__(self, platform):
        super().__init__(platform=platform)
        self.frames = []
        self.abandoned = []

    def supports_draft_streaming(self, **kwargs):
        return True

    async def send_draft(self, chat_id, draft_id, content, metadata=None):
        self.frames.append((chat_id, draft_id, content, metadata))
        return SendResult(success=True)

    async def abandon_open_draft(self, chat_id, content, metadata=None):
        self.abandoned.append((chat_id, content, metadata))


def _consumer(platform, alive=None):
    adapter = DraftAdapter(platform)
    config = StreamConsumerConfig(transport="auto", chat_type="dm", edit_interval=0.01,
                                  buffer_threshold=1, cursor="")
    consumer = GatewayStreamConsumer(adapter, "chat", config, metadata={"thread_id": "thread"},
                                     run_still_current=alive)
    return consumer, adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("death", ["stale", "cancel"])
async def test_turn_death_abandons_draft_without_delivery(death, monkeypatch):
    alive = [True]
    consumer, adapter = _consumer(register_test_platform(monkeypatch), lambda: alive[0])
    task = asyncio.create_task(consumer.run())
    consumer.on_delta("partial answer")
    for _ in range(100):
        if adapter.frames:
            break
        await asyncio.sleep(0.01)
    assert adapter.frames
    if death == "stale":
        alive[0] = False
    else:
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        assert death == "cancel"
    assert len(adapter.abandoned) == 1
    assert adapter.abandoned[0][0:2] == ("chat", "partial answer")
    assert consumer.final_response_sent is False


@pytest.mark.asyncio
async def test_abandon_without_draft_is_noop(monkeypatch):
    consumer, adapter = _consumer(register_test_platform(monkeypatch))
    await consumer._abandon_native_stream()
    assert adapter.abandoned == []
