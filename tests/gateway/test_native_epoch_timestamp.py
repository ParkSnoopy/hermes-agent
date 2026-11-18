"""Producers that pin epoch 0 for a stable retry fingerprint still record local time (not 1970)."""
from datetime import UTC, datetime

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import SendResult
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session_authority import initialize_session_authority
from gateway.session_ingress import admit_message
from gateway.session_results import execution_result

CHAT = '15551234567@s.whatsapp.net'


@pytest.mark.asyncio
async def test_epoch_zero_producer_timestamp_runs_with_local_time_and_keeps_retry_identity(tmp_path, monkeypatch):
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.platform_registry import PlatformEntry, platform_registry
    monkeypatch.setenv('GATEWAY_ALLOW_ALL_USERS', 'true')
    platform_registry.register(PlatformEntry('test_timestamp_transport', 'Test', lambda config: None, lambda: True))
    platform = Platform('test_timestamp_transport')
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter.platform = platform
    adapter.gateway_runner, runner.adapters = runner, {platform: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='stamp')
    runner._wire_adapter_handlers(adapter)
    stamps = []

    async def send(chat_id, content, **_):
        return SendResult(success=True, message_id='s')

    async def turn(event, *_args, **_kwargs):
        stamps.append(event.timestamp)
        execution_result.get()['result'] = {'final_response': 'ok', 'messages': []}
        return 'ok'

    monkeypatch.setattr(adapter, 'send', send)
    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    source = adapter.build_source(chat_id=CHAT, user_id=CHAT, chat_type='dm')
    pinned = lambda: MessageEvent(text='hi', source=source, message_id='p1',
                                  timestamp=datetime.fromtimestamp(0, UTC))
    try:
        before = datetime.now()
        assert await admit_message(authority, pinned()) == 'ok'
        assert len(stamps) == 1 and stamps[0].timestamp() >= before.timestamp(), stamps  # was 1970-01-01 (persisted as 0)
        # The redelivered provider id is still the same admission: no second turn.
        first = await authority.admit_native(pinned())
        assert first.status == 'terminal' and len(stamps) == 1
    finally:
        authority.db.close()
