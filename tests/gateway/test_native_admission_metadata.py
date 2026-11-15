"""Native input snapshots retain reply and media contracts for API sources."""
from dataclasses import replace

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session_authority import initialize_session_authority
from gateway.session_envelope import restore_native, snapshot_native
from hermes_state_runtime import RuntimeStoreError


@pytest.mark.asyncio
async def test_native_codec_closed_metadata_contract_and_reply_expected(tmp_path, monkeypatch):
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    monkeypatch.setattr(runner, '_is_user_authorized_for_source', lambda *a, **k: True)
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    runner.adapters = {Platform.API_SERVER: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='codec')
    try:
        event = MessageEvent(text='chatter', source=adapter.build_source(chat_id='chat', user_id='user', chat_type='group'),
                             message_id='c1')
        assert 'reply_expected' not in snapshot_native(runner, event)['native_text_v1']['event']
        for value in (False, True):
            assert restore_native(snapshot_native(runner, replace(event, reply_expected=value))).reply_expected is value
        for metadata in ({'gateway_session_id': 's'}, {'_authorization_profile_home': '/x'}, {'arbitrary': 1}):
            with pytest.raises(RuntimeStoreError, match='invalid_params'):
                snapshot_native(runner, replace(event, metadata=metadata))
        for bad in (replace(event, reply_expected='no'), replace(event, text='/new now')):
            with pytest.raises(RuntimeStoreError, match='invalid_params'):
                snapshot_native(runner, bad)
        for name in ('media_urls', 'media_types', 'media_text_inlined', 'media_filenames'):
            assert snapshot_native(runner, replace(event, **{name: None})) == snapshot_native(runner, event)
            for bad in ('x', ('x',)):
                with pytest.raises(RuntimeStoreError, match='invalid_params'):
                    snapshot_native(runner, replace(event, **{name: bad}))
    finally:
        authority.db.close()