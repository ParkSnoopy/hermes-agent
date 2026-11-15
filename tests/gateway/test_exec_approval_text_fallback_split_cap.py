"""Approval fallback must preserve instructions when delivery cannot prove the whole command."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.run_turn_runner import TurnRunner
from tools import approval
from tools.approval_gateway_wait import _ApprovalEntry

SESSION = "agent:main:test:dm:555"
TAIL = "&& rm -rf ~ # TAIL_MARKER"


class CaptureAdapter(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = 2000
    MAX_SPLIT_MESSAGES = 5
    splits_long_messages = True

    def __init__(self):
        super().__init__(PlatformConfig(enabled=True), Platform.LOCAL)
        self.sent = []

    async def connect(self, *, is_reconnect=False):
        return True

    async def disconnect(self):
        pass

    async def get_chat_info(self, chat_id):
        return {}

    async def send(self, chat_id, content, **kwargs):
        self.sent.extend(self.truncate_message(self.format_message(content), self.MAX_MESSAGE_LENGTH)[:self.MAX_SPLIT_MESSAGES])
        return SendResult(success=True, message_id='sent')


def _render(adapter, command: str) -> None:
    """Drive the production fallback path (``TurnRunner._render_approval_sync``) on ``adapter``."""
    loop = asyncio.new_event_loop()
    turn = object.__new__(TurnRunner)
    turn._ctx = SimpleNamespace(_status_adapter=adapter, _status_chat_id="555", session_key=SESSION,
                                _status_thread_metadata=None, _run_still_current=lambda: True)
    turn._schedule = lambda coro, _m: SimpleNamespace(result=lambda timeout=None: loop.run_until_complete(coro))
    turn._close_native_stream_boundary = lambda *_a, **_k: True
    try:
        turn._render_approval_sync({"command": command, "description": "recursive delete", "request_id": "r1"})
    finally:
        loop.close()


@pytest.fixture()
def pending():
    entry = _ApprovalEntry({"request_id": "r1", "command": "x"})
    with approval._lock:
        approval._gateway_queues[SESSION] = [entry]
    yield entry
    with approval._lock:
        approval._gateway_queues.clear()


def test_fallback_past_the_chunk_cap_keeps_instructions_and_offers_only_deny(pending):
    adapter = CaptureAdapter()
    _render(adapter, "echo " + "A" * 20000 + " " + TAIL)
    delivered = "".join(adapter.sent)
    assert "/deny" in delivered and "it will NOT run" in delivered  # instructions and deadline survive
    assert TAIL not in delivered and "/approve" not in delivered  # an unseen tail is never offered for approval
    assert len(adapter.sent) <= adapter.MAX_SPLIT_MESSAGES


def test_unproven_partial_delivery_offers_only_deny(pending):
    adapter = CaptureAdapter()
    adapter.reports_partial_delivery = False
    _render(adapter, "echo " + "A" * 6000 + " " + TAIL)
    assert len(adapter.sent) == 1 and len(adapter.sent[0]) <= 2000
    assert "/deny" in adapter.sent[0] and "/approve" not in adapter.sent[0] and TAIL not in adapter.sent[0]


def test_fallback_within_the_chunk_cap_keeps_every_choice(pending):
    adapter = CaptureAdapter()
    _render(adapter, "echo " + "A" * 5000 + " " + TAIL)
    delivered = "".join(adapter.sent)
    assert TAIL in delivered and "/approve" in delivered and "/deny" in delivered
