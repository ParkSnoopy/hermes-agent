"""Approvals in shared sessions bind to the actual actor, or fail closed after mixed turns."""

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource, build_session_context, build_session_key
from gateway.session_context import _UNSET, _VAR_MAP
from tests.gateway.test_approval_requester_binding import DEFAULT, write_approvers



@pytest.fixture(autouse=True)
def _clean_state():
    from tools import approval as approval_mod

    approval_mod._gateway_queues.clear()
    yield
    approval_mod._gateway_queues.clear()
    for var in _VAR_MAP.values():
        var.set(_UNSET)


def _observed_event(adapter, uid, name, body):
    return MessageEvent(text=body, source=SessionSource(
        platform=adapter, chat_id="shared", chat_type="group"),
        user_id=str(uid), user_name=name)


def _approval_raised_in(event):
    """The approval the agent raises inside *event*'s turn, through the gateway's real session binding."""
    from gateway.run import GatewayRunner
    from tools import approval as approval_mod
    from tools.approval_gateway_wait import _ApprovalEntry

    key = build_session_key(event.source)
    runner = object.__new__(GatewayRunner)
    tokens = runner._set_session_env(build_session_context(event.source, GatewayConfig(), event=event))
    try:
        entry = _ApprovalEntry({"command": "rm -rf notes"})
    finally:
        runner._clear_session_env(tokens)
    approval_mod._gateway_queues.setdefault(key, []).append(entry)
    return key


def _observe_adapter():
    return Platform.API_SERVER


@pytest.mark.asyncio
async def test_an_observed_group_approval_belongs_to_the_member_who_asked():
    from gateway.approval_actor import approval_refusal
    from tests.gateway.test_approval_requester_binding import _runner

    write_approvers([])
    adapter = _observe_adapter()
    key = _approval_raised_in(_observed_event(adapter, 111, "Alice Example", "delete my notes"))

    assert approval_refusal(key, "222") == "Alice Example"
    # A bare "yes" is a sender-less observed text turn, routed to /approve (run_busy): Bob's is
    # refused, Alice's answers.
    runner = _runner()
    runner.config.platforms[Platform.API_SERVER] = runner.config.platforms.pop(Platform.TELEGRAM)
    runner.adapters[Platform.API_SERVER] = runner.adapters.pop(Platform.TELEGRAM)
    bob, alice = _observed_event(adapter, 222, "Bob", "yes"), _observed_event(adapter, 111, "Alice Example", "yes")
    bob.text = alice.text = "/approve"
    assert await runner._handle_approve_command(bob) == "Only Alice Example can answer this approval."
    await runner._handle_approve_command(alice)
    from tools import approval as approval_mod
    assert not approval_mod._gateway_queues.get(key)


def test_an_approval_with_no_requester_is_answered_only_by_approvers_and_admins():
    """No requester recorded (a turn merged from two members) fails closed: never "anyone"."""
    from gateway.approval_actor import approval_refusal, refusal_text
    from gateway.platforms.base import merge_pending_message_event

    adapter = _observe_adapter()
    pending = {}
    merge_pending_message_event(pending, "k", _observed_event(adapter, 111, "Alice Example", "save my notes"))
    merge_pending_message_event(pending, "k", _observed_event(adapter, 222, "Bob", "delete my notes"), merge_text=True)
    write_approvers([])
    key = _approval_raised_in(pending["k"])

    for member in ("111", "222", "333"):
        refusal = approval_refusal(key, member)
        assert refusal and refusal_text(refusal) == "Only an admin or a listed approver can answer this approval."
    assert approval_refusal(key, "444", extra={"group_allow_admin_from": ["444"]}) is None
    from tools import approval as approval_mod
    for approvers, member in ((["333"], "333"), (DEFAULT, "222")):  # a listed approver; legacy: anyone
        approval_mod._gateway_queues.clear()
        write_approvers(approvers)
        assert approval_refusal(_approval_raised_in(pending["k"]), member) is None

    # One member's own follow-ups still merge into a turn bound to that member.
    same = {}
    merge_pending_message_event(same, "k", _observed_event(adapter, 111, "Alice Example", "save my notes"))
    merge_pending_message_event(same, "k", _observed_event(adapter, 111, "Alice Example", "and delete"), merge_text=True)
    approval_mod._gateway_queues.clear()
    write_approvers([])
    key = _approval_raised_in(same["k"])
    assert approval_refusal(key, "222") == "Alice Example" and approval_refusal(key, "111") is None

