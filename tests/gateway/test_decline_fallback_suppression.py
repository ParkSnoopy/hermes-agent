"""Refusals suppress fallback; ordinary failures still deliver it."""
import asyncio
from types import SimpleNamespace

import pytest

from gateway.delivery_refusal import EGRESS_DECLINE_CODE
from gateway.platforms.base import SendResult


DECLINE = {'success': False, 'code': EGRESS_DECLINE_CODE}


@pytest.mark.parametrize('declined', [True, False])
def test_exec_approval_fallback_is_suppressed_only_on_decline(declined):
    from gateway.run_turn_runner import TurnRunner, _ExecApprovalDeclined

    class Adapter:
        typed_command_prefix = '/'
        text_sends = None
        def __init__(self):
            self.text_sends = []
        def pause_typing_for_chat(self, chat_id):
            pass
        def resume_typing_for_chat(self, chat_id):
            pass
        async def send_exec_approval(self, *args, **kwargs):
            return SendResult(success=False, error='declined' if declined else 'failure',
                              raw_response=DECLINE if declined else None)
        async def send(self, chat_id, message, **kwargs):
            self.text_sends.append(message)
            return SendResult(success=True, message_id='m1')

    adapter = Adapter()
    ctx = SimpleNamespace(_status_adapter=adapter, _status_chat_id='C1', _status_thread_metadata={},
                          _session_key='sk1', session_key='sk1',
                          source=SimpleNamespace(chat_id='C1', platform='probe', session_key='sk1'))
    runner = TurnRunner(SimpleNamespace(), ctx)
    class Future:
        def __init__(self, result): self.value = result
        def result(self, timeout=None): return self.value
    runner._schedule = lambda coro, label: Future(asyncio.run(coro))
    runner._close_native_stream_boundary = lambda why: None
    approval = {'command': 'rm -rf /', 'description': 'danger', 'pattern_key': 'k'}
    if declined:
        with pytest.raises(_ExecApprovalDeclined):
            runner._approval_notify_sync(approval)
        assert adapter.text_sends == []
    else:
        runner._approval_notify_sync(approval)
        assert len(adapter.text_sends) == 1 and 'rm -rf /' in adapter.text_sends[0]


@pytest.mark.parametrize('declined', [True, False])
def test_slash_confirm_fallback_is_suppressed_only_on_decline(declined):
    from gateway.run_busy import GatewayBusySessionMixin
    from tools import slash_confirm

    class Adapter:
        async def send_slash_confirm(self, **kwargs):
            return SendResult(success=False, error='declined' if declined else 'failure',
                              raw_response=DECLINE if declined else None)
    busy = object.__new__(GatewayBusySessionMixin)
    busy._delivery_adapter_for = lambda source: Adapter()
    busy._thread_metadata_for_source = lambda source, anchor: {}
    busy._reply_anchor_for_event = lambda event: None
    busy._session_key_for_source = lambda source: 'sk1'
    event = SimpleNamespace(source=SimpleNamespace(chat_id='C1', platform='probe', user_id='u1'))
    reply = asyncio.run(busy._request_slash_confirm(event=event, command='/wipe', title='Confirm',
        message='really wipe?', handler=lambda choice: 'done'))
    assert reply == (None if declined else 'really wipe?')
    if declined:
        assert slash_confirm.get_pending('sk1') in (None, {}, [])


@pytest.mark.parametrize('declined', [True, False])
def test_queued_reconcile_fallback_is_suppressed_only_on_decline(declined):
    from gateway.run_notifications import GatewayNotificationsMixin

    class Adapter:
        def __init__(self): self.ops = []
        @staticmethod
        def extract_media(text): return [], text
        async def edit_message(self, **kwargs):
            self.ops.append('edit')
            return SendResult(success=False, error='declined' if declined else 'message too old',
                              raw_response=DECLINE if declined else None)
        async def send(self, *args, **kwargs):
            self.ops.append('send')
            return SendResult(success=True, message_id='m1')
    adapter = Adapter()
    mixin = object.__new__(GatewayNotificationsMixin)
    source = SimpleNamespace(chat_id='C1', platform='probe')
    consumer = SimpleNamespace(message_id='m0', _turn_split_delivery=False)
    asyncio.run(mixin._deliver_queued_first_response('SECRET', source, adapter,
                stream_consumer=consumer, deliver_media=False))
    assert adapter.ops == (['edit'] if declined else ['edit', 'send'])