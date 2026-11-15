"""Under the session authority a busy bare "yes" still answers a blocking approval (#46866)."""
import asyncio

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session_authority import SessionAuthority, initialize_session_authority
from hermes_state_runtime import list_session_admissions

CHAT = 'human-42'


class _HumanAdapter(BasePlatformAdapter):
    async def connect(self, *, is_reconnect=False):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id='s')

    async def get_chat_info(self, chat_id):
        return {'name': chat_id, 'type': 'dm'}


def _human_adapter(monkeypatch):
    from gateway import platform_registry
    from gateway.platform_registry import PlatformEntry, PlatformRegistry

    registry = PlatformRegistry()
    monkeypatch.setattr(platform_registry, 'platform_registry', registry)
    registry.register(PlatformEntry(name='human_fixture', label='Human fixture',
                                    adapter_factory=lambda config: None,
                                    check_fn=lambda: True,
                                    allowed_users_env='HUMAN_FIXTURE_ALLOWED_USERS'))
    monkeypatch.setenv('HUMAN_FIXTURE_ALLOWED_USERS', CHAT)
    return _HumanAdapter(PlatformConfig(enabled=True), Platform('human_fixture'))


class _RunningAgent:
    def __init__(self):
        self.interrupts = []

    def interrupt(self, *args):
        self.interrupts.append(args)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['interrupt', 'queue'])
async def test_busy_plaintext_yes_resolves_blocking_approval_not_interrupt_or_queue(tmp_path, monkeypatch, mode):
    from tools.approval import _gateway_queues
    from tools.approval_gateway_wait import _ApprovalEntry
    # No drain: an admitted "yes" would then wait on a turn, which is exactly the regression.
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    adapter = _human_adapter(monkeypatch)
    adapter.gateway_runner, runner.adapters = runner, {adapter.platform: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='approval')
    runner._wire_adapter_handlers(adapter)
    sent = []

    async def send(chat_id, content, **_):
        sent.append(content)
        return SendResult(success=True, message_id='s')

    monkeypatch.setattr(adapter, 'send', send)
    monkeypatch.setattr(runner, '_effective_busy_input_mode', lambda source: mode)
    source = adapter.build_source(chat_id=CHAT, user_id=CHAT, chat_type='dm')
    key = runner._session_key_for_source(source)
    agent = _RunningAgent()
    runner._session_state(key).turn.agent = agent
    entry = _ApprovalEntry({'command': 'rm -rf build'})
    _gateway_queues.setdefault(key, []).append(entry)
    try:
        handled = asyncio.ensure_future(runner._handle_message(MessageEvent(text='yes', source=source, message_id='y1')))
        done, _ = await asyncio.wait({handled}, timeout=10)
        handled.cancel()
        assert done, '"yes" was admitted as a turn behind the blocked approval'
        assert entry.event.is_set() and entry.result == 'once'
        assert agent.interrupts == []
        admitted = [row for sid in authority.sessions
                    for row in list_session_admissions(authority.db, session_id=sid, pending_only=False)]
        assert admitted == [], admitted
    finally:
        _gateway_queues.pop(key, None)
        authority.db.close()
