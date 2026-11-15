"""Producer completions and heartbeats keep entering a messaging conversation after compression
moved its route to a child and after a prior input's retained media was released."""
import asyncio
import time

import pytest


def _plugin_platform(monkeypatch):
    from gateway.config import Platform
    from gateway.platform_registry import PlatformEntry, platform_registry
    entry = PlatformEntry(name='test_chat', label='Test chat', adapter_factory=lambda cfg: None,
                          check_fn=lambda: True)
    monkeypatch.setitem(platform_registry._entries, entry.name, entry)
    return Platform(entry.name)


def _chat_adapter(monkeypatch):
    from gateway.config import PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter, SendResult

    class ChatAdapter(BasePlatformAdapter):
        async def connect(self, *, is_reconnect=False):
            return True

        async def disconnect(self):
            pass

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            return SendResult(success=True, message_id='sent')

        async def get_chat_info(self, chat_id):
            return {'id': chat_id, 'type': 'dm'}

    monkeypatch.setenv('GATEWAY_ALLOWED_USERS', '42')
    return ChatAdapter(PlatformConfig(enabled=True, typing_indicator=False), _plugin_platform(monkeypatch))


def pending(key, name):
    from tools import async_delegation as delegation
    evt = {'type': 'async_delegation', 'session_key': key, 'delegation_id': name,
           'summary': name, 'status': 'completed', 'dispatched_at': time.time()}
    delegation._persist_dispatch(evt)
    delegation._persist_completion(evt, {'status': 'completed', 'summary': name})
    return evt


async def _chat_owner(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority, SessionAuthority


    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    runner = GatewayRunner(GatewayConfig())
    adapter = _chat_adapter(monkeypatch)
    runner.adapters = {adapter.platform: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    return runner, adapter, authority, adapter.build_source(chat_id='42', chat_type='dm', user_id='42')


async def _admit_human(runner, authority, event):
    from gateway.session_ingress_context import native_callback
    from hermes_constants import get_hermes_home
    with native_callback(runner, event, get_hermes_home()):
        return await authority.admit_native(event)


@pytest.mark.asyncio
async def test_completion_after_compression_admits_to_the_logical_root(tmp_path, monkeypatch):
    from gateway.platforms.event import MessageEvent
    from hermes_state_runtime import list_session_admissions

    runner, _, authority, source = await _chat_owner(tmp_path, monkeypatch)
    receipt = await _admit_human(runner, authority, MessageEvent(text='human', source=source, message_id='human'))
    key = runner.session_store._generate_session_key(source)
    root = receipt.ref.session_id
    db = authority.db
    db.append_message(root, 'user', 'human')
    child = root + '-child'
    db.publish_compression_child(parent_session_id=root, child_session_id=child, source=source.platform.value,
                                 messages=[{'role': 'user', 'content': 'summary'}], require_compression_lease=False)
    assert runner.session_store.advance_compression_session(key, root, child) is not None
    event = dict(pending(key, 'after-compression'), parent_session_id=child)
    assert await runner._deliver_async_delegation_group([event]) is True
    rows = list_session_admissions(db, session_id=root, pending_only=False)
    assert next(r['request_id'] for r in rows) == 'human' and len(rows) == 2, rows
    assert 'after-compression' in rows[-1]['payload']['text']
    assert list_session_admissions(db, session_id=child, pending_only=False) == []
    # A lost-ACK redelivery is the same admission, found under the root's ledger.
    from gateway.session_automation import completion_admission
    assert completion_admission(runner, event)['admission_id'] == rows[-1]['admission_id']
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_text_completion_after_a_released_photo_input_is_admitted(tmp_path, monkeypatch):
    from gateway.platforms.base import get_image_cache_dir
    from gateway.platforms.event import MessageEvent, MessageType
    from gateway.session_ingress_media import release_admission_media
    from hermes_state_runtime import claim_session_input, list_session_admissions, settle_session_input

    runner, _, authority, source = await _chat_owner(tmp_path, monkeypatch)
    photo = get_image_cache_dir() / 'photo.png'
    photo.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)
    receipt = await _admit_human(runner, authority, MessageEvent(
        text='look', source=source, message_id='photo', message_type=MessageType.PHOTO,
        media_urls=[str(photo)], media_types=['image/png']))
    sid = receipt.ref.session_id
    started = claim_session_input(authority.db, epoch=authority.epoch, session_id=sid)
    settle_session_input(authority.db, epoch=authority.epoch, admission_id=started['admission_id'],
                         generation=started['generation'], outcome='completed')
    assert release_admission_media(authority.db, started['admission_id']) == 1  # what _drain does
    key = runner.session_store._generate_session_key(source)
    assert await runner._deliver_async_delegation_group([dict(pending(key, 'after-photo'), parent_session_id=sid)])
    rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
    assert len(rows) == 2 and 'after-photo' in rows[-1]['payload']['text'], rows
    assert 'media' not in rows[-1]['payload']['native_text_v1']
    await asyncio.sleep(0)
