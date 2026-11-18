"""Canonical native sessions preserve external-process provider selection."""
import json

import pytest

from tests.gateway.test_session_follow_profile_config import _owner, _profile


@pytest.mark.asyncio
async def test_native_session_keeps_its_external_process_provider_after_profile_moves(monkeypatch):
    import providers
    from gateway import run
    from gateway.session_local_recovery import restore_local_session
    from gateway.session_local_route import resolve_local_target
    from gateway.session_policy import policy_for_source
    from hermes_constants import get_hermes_home
    from providers.base import ProviderProfile

    name = 'proc-provider'
    profile = ProviderProfile(name=name, auth_type='external_process', base_url='process://proc-provider',
                              api_mode='chat_completions')
    real = providers.get_provider_profile
    monkeypatch.setattr(providers, 'get_provider_profile', lambda value: profile if value == name else real(value))
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run, '_hermes_home', home)
    (home / 'config.yaml').write_text(json.dumps({'model': {'provider': name, 'default': 'saved-model'}}))
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    try:
        response = await owner.dispatch({'id': 'create', 'method': 'session.create', 'params': {
            'request_id': 'process', 'source': 'cli', 'cwd': str(home)}})
        assert 'result' in response, response
        sid = response['result']['session_id']
        _profile(home, 'other-model', 1, 'other-key')
        assert resolve_local_target(authority, owner.actor, sid).session_id == sid
        authority.sessions.pop(sid)
        restore_local_session(authority, sid)
        policy = policy_for_source(runner, authority.sessions[sid].source)
        model = json.loads(policy.config_json)['model']
        assert policy.model == 'saved-model'
        assert model['provider'] == name
        assert not model.get('base_url')
        assert model.get('api_key') != 'other-key'
    finally:
        await owner.close()
        store.close_all_db_handles()