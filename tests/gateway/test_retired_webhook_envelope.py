"""Retired webhook deliveries must not enter the native admission ledger."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from gateway.session_envelope import _snapshot_native
from hermes_state_runtime import RuntimeStoreError


def test_webhook_snapshot_is_refused():
    source = SessionSource(platform=Platform.WEBHOOK, chat_id='route')
    event = MessageEvent(text='payload', source=source)
    runner = SimpleNamespace(session_store=SimpleNamespace(_generate_session_key=lambda _: 'route'))
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        _snapshot_native(runner, event, provenance=None, encoded_source={})


def test_local_snapshot_remains_available():
    source = SessionSource(platform=Platform.LOCAL, chat_id='local')
    event = MessageEvent(text='hello', source=source)
    runner = SimpleNamespace(session_store=SimpleNamespace(_generate_session_key=lambda _: 'local'))
    payload = _snapshot_native(runner, event, provenance=None, encoded_source={'platform': 'local'})
    assert payload['text'] == 'hello'
    assert payload['native_text_v1']['route'] == 'local'


@pytest.mark.asyncio
async def test_completion_readiness_keeps_local_push_and_rejects_retired_nonpush():
    from gateway.run_notifications import GatewayNotificationsMixin

    runner = GatewayNotificationsMixin.__new__(GatewayNotificationsMixin)
    source = SessionSource(platform=Platform.LOCAL, chat_id='local')
    runner._build_process_event_source = lambda evt: source
    runner._classify_completion_target = AsyncMock(return_value='deliver')
    adapter = SimpleNamespace(supports_async_delivery=True)
    runner._resolve_injection_adapter = lambda platform_name, source=None: adapter
    assert await runner._completion_delivery_ready({'parent_session_id': 's'}) is True
    adapter.supports_async_delivery = False
    assert await runner._completion_delivery_ready({'parent_session_id': 's'}) is False
