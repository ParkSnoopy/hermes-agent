"""Foreign session rows stay read-only; routed plugin chats keep their messaging route."""
from types import SimpleNamespace

import pytest

_CAPS = ['session:create', 'session:read', 'session:submit', 'session:control', 'session:approve',
         'session:respond']


async def _owner(tmp_path, monkeypatch, toolsets):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.owner_identity import host_owner_subject
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': toolsets})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None, _restored_source=lambda entry: entry.origin,
                             _delivery_adapter_for=lambda source: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='first')
    desktop = AuthorityConnection(authority, object(), {'provider': 'local', 'user_id': host_owner_subject(),
        'profile_id': str(tmp_path), 'instance_id': 'first', 'capabilities': _CAPS})
    return store, desktop


@pytest.mark.asyncio
async def test_foreign_writer_rows_stay_read_only(tmp_path, monkeypatch):
    store, owner = await _owner(tmp_path, monkeypatch, {'gui': []})
    for i, source in enumerate(('webui', 'teams', 'curator')):
        sid = source + '-row'
        store._db.create_session(sid, source=source, model='m', cwd=str(tmp_path))
        store._db.append_message(sid, role='user', content='hi from ' + sid)
        store._db.end_session(sid, 'ws_orphan_reap')
        await owner.dispatch({'id': 30 + i, 'method': 'session.resume', 'params': {'session_id': sid}})
        sent = await owner.dispatch({'id': 40 + i, 'method': 'prompt.submit',
                                     'params': {'session_id': sid, 'text': 'go on', 'submission_id': 's-' + sid}})
        assert (sent.get('error', {}).get('data') or {}).get('reason') == 'session_read_only', (sid, sent)
        assert store._db.get_session(sid)['source'] == source


@pytest.mark.asyncio
async def test_a_plugin_platform_sharing_the_label_keeps_its_messaging_route(tmp_path, monkeypatch):
    from gateway.config import Platform
    from gateway.session import SessionSource

    store, desktop = await _owner(tmp_path, monkeypatch, {})
    Platform._add_pseudo_member('mobile')  # what the hermes-mobile plugin's platform registration yields
    entry = store.get_or_create_session(SessionSource(platform=Platform('mobile'), chat_id='device-1',
                                                      chat_type='dm', user_id='device-1'))
    store._db.append_message(entry.session_id, role='user', content='from the plugin app')
    resumed = await desktop.dispatch({'id': 1, 'method': 'session.resume',
                                      'params': {'session_id': entry.session_id}})
    assert [m['content'] for m in resumed.get('result', {}).get('messages', [])] == ['from the plugin app'], resumed
    assert store._db.get_session(entry.session_id)['source'] == 'mobile'
