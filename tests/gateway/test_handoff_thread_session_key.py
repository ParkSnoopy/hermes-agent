"""Handoff destination helper used by shared warning tests."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.session import build_session_key
from tests.gateway.test_automation_compression_media import _plugin_platform

def _handoff_destination(platform: Platform, channel_id: str, thread_id: str, scope_id, adapter):
    """Run the real handoff destination/key path against a configured home channel."""
    config = GatewayConfig(platforms={platform: PlatformConfig(enabled=True, token="test")})
    config.platforms[platform].home_channel = HomeChannel(
        platform=platform, chat_id=channel_id, name="home", thread_id=thread_id, scope_id=scope_id)
    adapter.create_handoff_thread = AsyncMock(return_value=thread_id)
    runner = object.__new__(GatewayRunner)
    runner.config = config
    runner.adapters = {platform: adapter}
    runner.session_store = None
    with patch("gateway.delivery.resolve_delivery_transport",
               lambda *_a: SimpleNamespace(adapter=adapter, send=AsyncMock())):
        dest = asyncio.run(runner._handoff_resolve_destination(
            {"id": "cli-session", "title": "work", "handoff_platform": platform.value}, profile_name=None))
    return dest, runner._handoff_session_key(dest, profile_name=None)


@pytest.mark.parametrize('chat_type,expected', [('group', 'group'), ('channel', 'channel'),
                                                ('dm', 'dm'), ('unknown', 'dm')])
@pytest.mark.parametrize('thread_id', [None, 'thread-7'])
def test_handoff_uses_configured_home_identity(monkeypatch, chat_type, expected, thread_id):
    platform = _plugin_platform(monkeypatch)
    adapter = SimpleNamespace(get_chat_info=AsyncMock(return_value={'type': chat_type}))
    dest, key = _handoff_destination(platform, 'home-1', thread_id, 'tenant-1', adapter)
    assert dest.source.chat_id == 'home-1'
    assert dest.source.thread_id == thread_id
    assert dest.source.chat_type == expected
    assert dest.source.scope_id == 'tenant-1'
    assert dest.source.user_id == 'system:handoff'
    assert key == build_session_key(dest.source, group_sessions_per_user=True,
                                    thread_sessions_per_user=False)
    adapter.create_handoff_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_repoints_to_conversation_head_and_clears_pin():
    runner = object.__new__(GatewayRunner)
    store = SimpleNamespace(get_or_create_session=AsyncMock(),
                            switch_session=AsyncMock(return_value='switched'))
    runner.session_store = object()
    store._store = runner.session_store
    runner._async_session_store = store
    source = object()
    dest = SimpleNamespace(source=source)
    with patch('gateway.session_route_recovery.conversation_head',
               new=AsyncMock(return_value=(None, None, 'head'))):
        assert await runner._handoff_repoint(dest, 'route', 'cli') == 'switched'
    store.get_or_create_session.assert_awaited_once_with(source)
    store.switch_session.assert_awaited_once_with('route', 'head', preserve_prompt_pin=False)


def test_handoff_key_keeps_multiplex_profile(monkeypatch):
    platform = _plugin_platform(monkeypatch)
    adapter = SimpleNamespace(get_chat_info=AsyncMock(return_value={'type': 'group'}))
    dest, _ = _handoff_destination(platform, 'home-1', 'thread-7', 'tenant-1', adapter)
    runner = object.__new__(GatewayRunner)
    key = runner._handoff_session_key(dest, profile_name='secondary')
    assert key == build_session_key(dest.source, group_sessions_per_user=True,
                                    thread_sessions_per_user=False, profile='secondary')
    assert key.startswith('agent:secondary:')

