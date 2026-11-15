"""The plain-text approval prompt on an adapter whose ``send`` cuts long text: instructions and
the deadline survive the cut, and a command that cannot
be shown whole leaves only Deny, the same rule the button cards follow (#62359)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from gateway.run_turn_runner import TurnRunner
from tools import approval
from tools.approval_gateway_wait import _ApprovalEntry

SESSION = "agent:main:x:dm:chat"
TAIL = "&& rm -rf ~ # TAIL_MARKER"


def _human(monkeypatch):
    from gateway.platforms.base import SendResult
    from tests.gateway.test_authority_busy_plaintext_approval import _human_adapter

    adapter = _human_adapter(monkeypatch)
    wire = []
    adapter.max_message_length_for_chat = lambda _chat: 2000

    async def send(chat_id, content, reply_to=None, metadata=None):
        wire.append(content[:2000])
        return SendResult(success=True, message_id="m")

    adapter.send = send
    return adapter, wire


def _render(adapter, command: str) -> None:
    """Drive the production text-prompt path (``TurnRunner._render_approval_sync``) on ``adapter``."""
    loop = asyncio.new_event_loop()
    turn = object.__new__(TurnRunner)
    turn._ctx = SimpleNamespace(_status_adapter=adapter, _status_chat_id="chat", session_key=SESSION,
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


def test_long_command_keeps_instructions_and_offers_only_deny(monkeypatch, pending):
    adapter, wire = _human(monkeypatch)
    adapter.pause_typing_for_chat = lambda chat_id: None
    command = "echo " + "A" * 30000 + " " + TAIL
    _render(adapter, command)
    delivered = wire[-1]
    assert "/deny" in delivered and "/approve" not in delivered  # instructions survive; Deny alone
    assert "Preview shortened" in delivered and TAIL not in delivered
    assert pending.card_deny_only is True
    assert approval.resolve_gateway_approval_card(SESSION, "once", "r1") == 0  # a crafted approve resolves nothing


def test_short_command_is_shown_whole_with_every_choice(monkeypatch, pending):
    adapter, wire = _human(monkeypatch)
    adapter.pause_typing_for_chat = lambda chat_id: None
    _render(adapter, "rm -rf /tmp/x " + TAIL)
    delivered = wire[-1]
    assert TAIL in delivered and "/approve" in delivered and "/deny" in delivered
    assert not pending.card_deny_only


@pytest.mark.parametrize("command", ["echo " + "`" * 2100 + " ; rm -rf ~", "`" * 3000, "`" * 5000],
                         ids=["echo-2100-ticks", "3000-ticks", "5000-ticks"])
def test_a_long_backtick_run_never_leaves_the_fence_open_past_the_cut(command, pending, monkeypatch):
    """The fence is sized from the shortened command, so the cut can never drop the closing fence."""
    import re

    adapter, wire = _human(monkeypatch)
    adapter.pause_typing_for_chat = lambda chat_id: None
    _render(adapter, command)
    delivered = wire[-1]
    assert len(delivered) <= adapter.max_message_length_for_chat("chat") and "Preview shortened" in delivered
    fence = re.search(r"\n(`{3,})\n", delivered).group(1)
    assert delivered.endswith("\n" + fence)  # the closing fence survived the cut
    shown = delivered[delivered.index("\n" + fence + "\n") + len(fence) + 2:-len(fence) - 1]
    assert max(map(len, re.findall("`+", shown)), default=0) < len(fence)  # and nothing shown closes it early
    assert pending.card_deny_only is True


def test_command_that_fits_only_beside_deny_is_shown_whole_and_not_called_shortened(monkeypatch, pending):
    """One character too long for every choice, short enough for Deny alone: the whole command is
    shown, only Deny is offered, and the notice says why instead of claiming a shortened preview."""
    adapter, wire = _human(monkeypatch)
    adapter.pause_typing_for_chat = lambda chat_id: None
    _render(adapter, TAIL)
    room = adapter.max_message_length_for_chat("chat") - len(wire[-1])
    command = "A" * (room + 1) + TAIL
    _render(adapter, command)
    delivered = wire[-1]
    assert "\n" + command in delivered and "/approve" not in delivered and "/deny" in delivered
    assert "Preview shortened" not in delivered and "only Deny is offered" in delivered
    assert pending.card_deny_only is True
