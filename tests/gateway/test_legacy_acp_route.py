"""Canonical native sessions retain their saved route and runtime choices."""
import json

import pytest

from tests.gateway.test_session_follow_profile_config import _owner, _profile


@pytest.mark.asyncio
async def test_canonical_tui_receipt_keeps_saved_route_and_settings(monkeypatch, tmp_path):
    from gateway import run
    from gateway.session_local_recovery import restore_local_session
    from gateway.session_local_route import resolve_local_target
    from hermes_constants import get_hermes_home
    from hermes_state_local import local_receipt

    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run, '_hermes_home', home)
    _profile(home, 'saved-model', 1, 'saved-key')
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    try:
        response = await owner.dispatch({'id': 'create', 'method': 'session.create', 'params': {
            'request_id': 'saved', 'source': 'tui', 'cwd': str(tmp_path),
            'reasoning': 'high', 'service_tier': 'priority'}})
        assert 'result' in response, response
        sid = response['result']['session_id']
        _profile(home, 'new-model', 2, 'new-key')
        assert resolve_local_target(authority, owner.actor, sid).session_id == sid
        policy = local_receipt(authority.db, sid)['policy']
        config = json.loads(policy['config_json'])
        assert policy['source'] == 'tui' and policy['model'] == 'saved-model'
        assert policy['cwd'] == str(tmp_path)
        assert config['model']['provider'] == 'custom'
        assert config['model']['base_url'] == 'http://127.0.0.1:1/v1'
        assert config['agent']['reasoning_effort'] == 'high'
        assert config['agent']['service_tier'] == 'priority'
        authority.sessions.pop(sid)
        assert restore_local_session(authority, sid).session_id == sid
    finally:
        await owner.close()
        store.close_all_db_handles()