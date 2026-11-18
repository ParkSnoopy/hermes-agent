"""Canonical local receipts keep route and credential ownership through a future turn.

The fake servers and AIAgent are exercised only when this integration suite is run.
"""
import json

import pytest

from tests.fakes.fake_llm_provider import FakeLLMServer, Text
from tests.gateway.test_session_follow_profile_config import _owner


def _profile(home, url, key, *, model='profile-model', extra=None):
    home.mkdir(parents=True, exist_ok=True)
    config = {'model': {'default': model, 'provider': 'custom', 'base_url': url, 'api_key': key},
              'agent': {'api_max_retries': 1, 'reasoning_effort': 'high'},
              'platform_toolsets': {'tui': []}}
    config.update(extra or {})
    (home / 'config.yaml').write_text(json.dumps(config))


async def _create(owner, authority, home, **extra):
    response = await owner.dispatch({'id': 'create', 'method': 'session.create', 'params': {
        'request_id': 'turn-route', 'source': 'cli', 'cwd': str(home), 'toolsets': [], **extra}})
    assert 'result' in response, response
    sid = response['result']['session_id']
    # Reopen by the canonical receipt and actor-authorized lineage, not a foreign row.
    from gateway.session_local_route import resolve_local_target
    from gateway.session_local_recovery import restore_local_session
    assert resolve_local_target(authority, owner.actor, sid).session_id == sid
    authority.sessions.pop(sid, None)
    ref = restore_local_session(authority, sid)
    assert ref.session_id == sid
    return authority.sessions[sid]


def _resolved(runner, live):
    from gateway.run_turn_prepare import _resolve_policy_agent_runtime
    from gateway.session_policy import policy_for_source
    policy = policy_for_source(runner, live.source)
    return policy, _resolve_policy_agent_runtime(runner, policy, fallback=False)


def _run_turn(policy, model, runtime):
    from run_agent import AIAgent
    agent = AIAgent(model=model, **{k: runtime[k] for k in ('api_key', 'base_url', 'provider', 'api_mode')},
                    reasoning_config=policy.reasoning_config, quiet_mode=True, skip_context_files=True,
                    skip_memory=True, enabled_toolsets=['memory'])
    try:
        return agent.run_conversation('hello again')
    finally:
        agent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('credential', ['inline', 'env-ref'])
async def test_frozen_native_receipt_turn_uses_its_own_endpoint_and_key(monkeypatch, credential):
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    monkeypatch.setattr('gateway.run._hermes_home', home)
    monkeypatch.setenv('NATIVE_ROUTE_KEY', 'saved-key')
    with FakeLLMServer([Text('saved ok')], api_key='saved-key') as saved, \
            FakeLLMServer([Text('profile')], api_key='profile-key') as profile:
        key = 'saved-key' if credential == 'inline' else '${NATIVE_ROUTE_KEY}'
        _profile(home, saved.base_url, key, model='saved-model')
        store, runner, authority, owner = _owner(home)
        try:
            live = await _create(owner, authority, home)
            _profile(home, profile.base_url, 'profile-key')
            policy, (model, runtime) = _resolved(runner, live)
            result = _run_turn(policy, model, runtime)
            assert result['final_response'] == 'saved ok'
            assert profile.requests == []
            sent = saved.main_requests()
            assert [r['model'] for r in sent] == ['saved-model']
            assert sent[0]['reasoning_effort'] == 'high'
            assert {r['auth'] for r in saved.requests} == {'Bearer saved-key'}
            assert all('profile-key' not in str(r['headers']) for r in saved.requests)
        finally:
            await owner.close()
            store.close_all_db_handles()


@pytest.mark.asyncio
async def test_following_native_receipt_turn_uses_current_endpoint_and_key(monkeypatch):
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    monkeypatch.setattr('gateway.run._hermes_home', home)
    with FakeLLMServer([Text('old')], api_key='old-key') as old, \
            FakeLLMServer([Text('new')], api_key='new-key') as new:
        _profile(home, old.base_url, 'old-key', model='old-model')
        store, runner, authority, owner = _owner(home)
        try:
            live = await _create(owner, authority, home, follow_profile_config=True)
            _profile(home, new.base_url, 'new-key', model='new-model')
            policy, (model, runtime) = _resolved(runner, live)
            result = _run_turn(policy, model, runtime)
            assert result['final_response'] == 'new'
            assert old.requests == []
            assert [r['model'] for r in new.main_requests()] == ['new-model']
            assert {r['auth'] for r in new.requests} == {'Bearer new-key'}
            assert all('old-key' not in str(r['headers']) for r in new.requests)
        finally:
            await owner.close()
            store.close_all_db_handles()


@pytest.mark.asyncio
async def test_native_receipt_missing_named_provider_is_refused(monkeypatch):
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    monkeypatch.setattr('gateway.run._hermes_home', home)
    with FakeLLMServer([Text('profile')]) as profile:
        _profile(home, profile.base_url, 'profile-key')
        store, runner, authority, owner = _owner(home)
        try:
            live = await _create(owner, authority, home, model='saved-model', provider='custom:missing')
            with pytest.raises(Exception) as caught:
                _resolved(runner, live)
            assert 'missing' in str(caught.value)
            assert profile.requests == []
        finally:
            await owner.close()
            store.close_all_db_handles()


@pytest.mark.asyncio
async def test_named_pick_moved_never_sends_new_entry_key_to_old_endpoint(monkeypatch):
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    monkeypatch.setattr('gateway.run._hermes_home', home)
    with FakeLLMServer([Text('primary')], api_key='primary-key') as primary, \
            FakeLLMServer([Text('old')], api_key='old-key') as old, \
            FakeLLMServer([Text('moved')], api_key='moved-key') as moved:
        def configure(url, key):
            _profile(home, primary.base_url, 'primary-key', extra={'providers': {
                'picked': {'base_url': url, 'api_key': key}}})
        configure(old.base_url, 'old-key')
        store, runner, authority, owner = _owner(home)
        try:
            live = await _create(owner, authority, home, follow_profile_config=True,
                                 model='picked-model', provider='custom:picked', base_url=old.base_url)
            configure(moved.base_url, 'moved-key')
            policy, (model, runtime) = _resolved(runner, live)
            result = _run_turn(policy, model, runtime)
            assert result['final_response'] == 'primary'
            assert [r['model'] for r in primary.main_requests()] == ['profile-model']
            assert {r['auth'] for r in primary.requests} == {'Bearer primary-key'}
            assert old.requests == [] and moved.requests == []
        finally:
            await owner.close()
            store.close_all_db_handles()
