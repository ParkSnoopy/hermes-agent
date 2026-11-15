"""Warnings and media fallback must not prevent a requested final reply."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.status_output import StatusOutputMixin
from gateway.platforms.base import SendResult
from gateway.run_turn_runner import TurnRunner
from gateway.turn_context import TurnContext
from tests.gateway.test_automation_compression_media import _chat_adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [None, False, True])
async def test_warning_and_media_fallback_do_not_prevent_final(tmp_path, monkeypatch, setting):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(tmp_path / "managed"))
    cfg = {} if setting is None else {"display": {"suppress_warning_notifications": setting}}
    (tmp_path / "config.yaml").write_text(json.dumps(cfg))
    adapter = _chat_adapter(monkeypatch)
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="sent"))
    source = adapter.build_source(chat_id="chat", chat_type="dm", user_id="42")
    metadata = {"thread_id": "thread"}
    ctx = TurnContext(source=source, user_config=cfg, _run_still_current=lambda: True,
                      _status_adapter=adapter, _status_chat_id="chat", _status_thread_metadata=metadata)
    turn = TurnRunner(SimpleNamespace(), ctx)
    loop = asyncio.get_running_loop()
    scheduled = []

    def schedule(coro, *args):
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        scheduled.append(future)
        return future

    turn._schedule = schedule
    agent = StatusOutputMixin()
    agent.suppress_status_output = True
    agent.status_callback = turn._status_callback_sync
    await asyncio.to_thread(agent._warn_uncompressed_context_overflow, 200, 100)
    await asyncio.gather(*(asyncio.wrap_future(f) for f in scheduled))
    assert agent._last_ctx_overflow_warn
    assert adapter.send.await_count == (0 if setting is True else 1)

    document = tmp_path / "fixture.pdf"
    document.write_bytes(b"fixture document")
    media = await adapter.send_document("chat", str(document), metadata=metadata)
    assert media.success is not (setting is True)
    assert adapter.send.await_count == (0 if setting is True else 2)
    result = await adapter.send("chat", "Requested final", metadata=metadata)
    assert result.success
    assert adapter.send.await_args.args == ("chat", "Requested final")
    assert adapter.send.await_args.kwargs["metadata"] == metadata
