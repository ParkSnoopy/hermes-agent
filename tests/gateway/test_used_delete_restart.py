"""A used session cannot be deleted while busy; retirement survives owner restart."""
import asyncio
import json
from pathlib import Path
import threading

import pytest

from gateway.session_local import create_local_session
import hermes_state_runtime as rt
from tests.gateway.test_session_mutation_retirement import local_authority


@pytest.mark.asyncio
async def test_used_native_delete_restart_exact_retry(tmp_path, monkeypatch):
    authority, owner = local_authority(tmp_path, monkeypatch)
    db = authority.db
    ref = create_local_session(authority, owner.actor, {'request_id': 'native-used', 'source': 'cli',
                               'cwd': str(tmp_path), 'model': 'm', 'toolsets': []})
    sid = ref.session_id
    db.append_message(sid, 'user', 'used once')
    db.append_message(sid, 'assistant', 'answer')
    admission = rt.admit_session_input(db, epoch=authority.epoch, principal_id=owner.actor.subject,
                                      session_id=sid, request_id='once', payload={'text': 'used once'})
    claim = rt.claim_session_input(db, epoch=authority.epoch, session_id=sid)
    row = db.get_session(sid)
    params = {'session_id': sid, 'request_id': 'delete', 'expected_revision': row['runtime_revision'],
              'expected_generation': row['runtime_generation'], 'operation': 'delete', 'payload': {}}
    request = {'id': 1, 'method': 'session.mutate', 'params': params}
    busy = await owner.dispatch(request)
    assert busy['error']['message'] == 'session_busy', busy
    assert db.get_session(sid) is not None
    rt.settle_session_input(db, epoch=authority.epoch, admission_id=admission['admission_id'],
                            generation=claim['generation'], outcome='completed')
    row = db.get_session(sid)
    params = {**params, 'expected_revision': row['runtime_revision'], 'expected_generation': row['runtime_generation']}
    request = {**request, 'params': params}
    deleted = await owner.dispatch(request)
    assert 'result' in deleted, deleted
    assert db.get_session(sid) is None and not db.get_messages(sid)
    with db._read_ctx() as conn:
        assert not conn.execute('SELECT 1 FROM session_admissions WHERE target_session_id=?', (sid,)).fetchone()
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    await owner.close()
    db.close()
    authority, owner = local_authority(tmp_path, monkeypatch, instance_id='restarted')
    try:
        assert await owner.dispatch(request) == deleted
        replay = rt.admit_session_input(authority.db, epoch=authority.epoch, principal_id=owner.actor.subject,
                                        session_id=sid, request_id='once', payload={'text': 'used once'})
        assert replay['admission_id'] == admission['admission_id'] and replay['status'] == 'terminal'
        with pytest.raises(rt.RuntimeStoreError, match='admission_conflict'):
            rt.admit_session_input(authority.db, epoch=authority.epoch, principal_id=owner.actor.subject,
                                   session_id=sid, request_id='once', payload={'text': 'changed'})
        changed = await owner.dispatch({**request, 'params': {**params, 'expected_revision': 999}})
        assert changed['error']['message'] == 'admission_conflict', changed
        stale = await owner.dispatch({'id': 2, 'method': 'session.resume', 'params': {'session_id': sid}})
        assert stale['error']['message'] == 'not_found', stale
    finally:
        await owner.close()
        authority.db.close()


@pytest.mark.asyncio
async def test_real_local_turn_delete_and_owner_restart(tmp_path, monkeypatch):
    """Exercise the real runner and provider, not a pre-seeded conversation."""
    from gateway import run
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import SessionRef
    from gateway.session_controls import AuthorityConnection
    from gateway.session_results import admission_result
    from run_agent import AIAgent
    from tests.e2e.core.history._helpers import NO_BACKGROUND_REVIEW, OFFLINE_CONFIG
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home
    from tests.gateway.fixtures.execution_frames_capture import Peer

    home = tmp_path / 'home'
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-fake-e2e')
    monkeypatch.setattr(run, '_hermes_home', home)
    blocked, release = threading.Event(), threading.Event()

    def respond(record):
        if 'BLOCK_STARTED' in json.dumps(record['body']):
            blocked.set()
            assert release.wait(30), 'blocked provider was not released'
            return Text('busy turn completed')
        return Text('used turn completed')

    async def rpc(connection, method, **params):
        return await connection.dispatch({'jsonrpc': '2.0', 'id': method,
                                          'method': method, 'params': params})

    with FakeLLMServer(respond) as provider:
        write_hermes_home(home, provider.base_url, extra_config=OFFLINE_CONFIG + NO_BACKGROUND_REVIEW
                          + 'gateway:\n  multiplex_profiles: false\n'
                            'auxiliary:\n  title_generation:\n    enabled: false\n')
        monkeypatch.setenv('OPENAI_BASE_URL', provider.base_url)
        monkeypatch.setattr(run, '_resolve_runtime_agent_kwargs', lambda: {
            'api_key': 'sk-fake-e2e', 'base_url': provider.base_url,
            'provider': 'custom', 'api_mode': 'chat_completions'})

        async def owner(instance):
            runner = run.GatewayRunner()
            authority = await initialize_session_authority(runner, profile_id=str(home), instance_id=instance)
            peer = Peer()
            connection = AuthorityConnection(authority, peer, {'user_id': 'owner'}, operator=True)
            return runner, authority, connection, peer

        runner, authority, connection, peer = await owner('first-owner')
        try:
            created = await rpc(connection, 'session.create', request_id='used-create', source='cli',
                                cwd=str(tmp_path), toolsets=['memory'])
            assert 'result' in created, created
            sid = created['result']['session_id']
            ref = SessionRef(authority.profile_id, sid)
            route = authority.sessions[sid].route
            assert runner.session_store.lookup_by_session_key(route).session_id == sid
            first = await rpc(connection, 'prompt.submit', session_id=sid, input_id='used-once',
                              text='NATIVE_USED_ONCE')
            assert first.get('result', {}).get('status') == 'queued', first
            admission_id = first['result']['admission_id']
            await asyncio.wait_for(authority.sessions[sid].task, timeout=60)
            frames = await asyncio.to_thread(peer.drain, 'message.complete')
            assert 'used turn completed' in json.dumps(frames), frames
            assert isinstance(authority.agent(ref), AIAgent)
            receipt = await rpc(connection, 'prompt.receipt', session_id=sid, admission_id=admission_id)
            assert receipt.get('result', {}).get('status') == 'terminal', receipt
            assert receipt['result']['outcome'] == 'completed', receipt
            admitted_payload = rt.get_session_admission(authority.db, admission_id=admission_id)['payload']
            assert admitted_payload['text'] == 'NATIVE_USED_ONCE'
            saved_result = admission_result(authority.db, admission_id)
            assert saved_result['result']['final_response'] == 'used turn completed', saved_result
            count = len(provider.requests)
            repeated = await rpc(connection, 'prompt.submit', session_id=sid, input_id='used-once',
                                 text='NATIVE_USED_ONCE')
            assert repeated['result']['admission_id'] == admission_id, repeated
            assert repeated['result']['status'] == 'terminal', repeated
            assert len(provider.requests) == count
            messages = authority.db.get_messages(sid)
            assert any(m['role'] == 'user' and 'NATIVE_USED_ONCE' in (m.get('content') or '') for m in messages)
            assert any(m['role'] == 'assistant' and 'used turn completed' in (m.get('content') or '') for m in messages)
            assert any(r['kind'] == 'main' and 'NATIVE_USED_ONCE' in json.dumps(r['body'])
                       for r in provider.requests)

            busy = await rpc(connection, 'prompt.submit', session_id=sid, input_id='busy-once',
                             text='BLOCK_STARTED')
            assert busy.get('result', {}).get('status') == 'queued', busy
            assert await asyncio.to_thread(blocked.wait, 30), 'provider did not receive busy turn'
            row = authority.db.get_session(sid)
            params = {'session_id': sid, 'request_id': 'delete-used', 'operation': 'delete', 'payload': {},
                      'expected_revision': row['runtime_revision'], 'expected_generation': row['runtime_generation']}
            refused = await rpc(connection, 'session.mutate', **params)
            assert refused.get('error', {}).get('message') == 'session_busy', refused
            assert authority.db.get_session(sid) is not None
            assert runner.session_store.lookup_by_session_key(route).session_id == sid
            release.set()
            await asyncio.wait_for(authority.sessions[sid].task, timeout=60)
            row = authority.db.get_session(sid)
            params.update(expected_revision=row['runtime_revision'], expected_generation=row['runtime_generation'])
            deleted = await rpc(connection, 'session.mutate', **params)
            assert 'result' in deleted, deleted
            assert authority.db.get_session(sid) is None and not authority.db.get_messages(sid)
            assert runner.session_store.lookup_by_session_key(route) is None
            assert sid not in authority.sessions
            with authority.db._read_ctx() as db:
                assert not db.execute('SELECT 1 FROM session_admissions WHERE target_session_id=?', (sid,)).fetchone()
                assert not db.execute('PRAGMA foreign_key_check').fetchall()
            before_restart = len(provider.requests)
        finally:
            release.set()
            live = authority.sessions.get(locals().get('sid'))
            if live is not None and live.task is not None and not live.task.done():
                await asyncio.wait_for(live.task, timeout=60)
            await connection.close()
            runner.session_store.close_all_db_handles()
            runner.close_all_session_db_handles()

        runner, authority, connection, _ = await owner('restarted-owner')
        other = AuthorityConnection(authority, Peer(), {'user_id': 'other'})
        try:
            assert await rpc(connection, 'session.mutate', **params) == deleted
            denied = await rpc(other, 'session.mutate', **params)
            assert 'error' in denied, denied
            changed = await rpc(connection, 'session.mutate', **{**params, 'expected_revision': 999})
            assert changed.get('error', {}).get('message') == 'admission_conflict', changed
            stale = await rpc(connection, 'session.resume', session_id=sid)
            assert stale.get('error', {}).get('message') == 'not_found', stale
            stale = await rpc(connection, 'prompt.submit', session_id=sid, input_id='stale-new',
                              text='MUST_NOT_RUN')
            assert 'error' in stale, stale
            assert runner.session_store.lookup_by_session_key(route) is None
            assert authority.db.get_session(sid) is None and not authority.db.get_messages(sid)
            replay = rt.admit_session_input(authority.db, epoch=authority.epoch,
                                            principal_id=connection.actor.subject, session_id=sid,
                                            request_id='used-once', payload=admitted_payload)
            assert replay['admission_id'] == admission_id and replay['status'] == 'terminal', replay
            retired_result = admission_result(authority.db, admission_id)
            assert retired_result['result']['final_response'] == saved_result['result']['final_response']
            assert retired_result.get('usage') == saved_result.get('usage')
            assert retired_result['result']['messages'] == []
            with pytest.raises(rt.RuntimeStoreError, match='admission_conflict'):
                rt.admit_session_input(authority.db, epoch=authority.epoch,
                                       principal_id=connection.actor.subject, session_id=sid,
                                       request_id='used-once', payload={**admitted_payload, 'text': 'changed'})
            with authority.db._read_ctx() as db:
                assert not db.execute('SELECT 1 FROM session_admissions WHERE target_session_id=?', (sid,)).fetchone()
                assert not db.execute('PRAGMA foreign_key_check').fetchall()
            assert len(provider.requests) == before_restart
        finally:
            await other.close()
            await connection.close()
            runner.session_store.close_all_db_handles()
            runner.close_all_session_db_handles()
