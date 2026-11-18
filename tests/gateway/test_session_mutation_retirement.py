"""Native deletion retires routing and keeps only exact authorized retries."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

def local_authority(tmp_path, monkeypatch, *, instance_id='owner'):
    """The canonical in-process owner and its durable routing store."""
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import SessionAuthority
    from gateway.session_controls import AuthorityConnection
    from hermes_state_runtime import begin_runtime_epoch

    monkeypatch.setattr(run, '_load_gateway_config', dict)
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                             _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
                             _adapter_for_source=lambda source: None)
    authority = SessionAuthority(runner, profile_id='owned', instance_id=instance_id, db=db,
                                 epoch=begin_runtime_epoch(db, instance_id=instance_id))
    runner.session_authority = authority
    return authority, AuthorityConnection(authority, object(), {'user_id': 'human'})


@pytest.mark.asyncio
async def test_native_delete_exact_retry_survives_owner_restart(tmp_path, monkeypatch):
    from gateway.session_local import create_local_session
    authority, owner = local_authority(tmp_path, monkeypatch)
    db = authority.db
    ref = create_local_session(authority, owner.actor, {'request_id': 'retirement', 'source': 'cli',
                               'cwd': str(tmp_path), 'model': 'm', 'toolsets': []})
    sid = ref.session_id
    row = db.get_session(sid)
    params = {'session_id': sid, 'request_id': 'delete', 'expected_revision': row['runtime_revision'],
              'expected_generation': row['runtime_generation'], 'operation': 'delete', 'payload': {}}
    request = {'id': 1, 'method': 'session.mutate', 'params': params}
    first = await owner.dispatch(request)
    assert 'result' in first, first
    assert db.get_session(sid) is None
    assert not any(json.loads(row)['session_id'] == sid for row in db.load_gateway_routing_entries(scope='').values())
    assert await owner.dispatch(request) == first
    await owner.close()
    db.close()
    authority, owner = local_authority(tmp_path, monkeypatch, instance_id='restarted')
    try:
        assert await owner.dispatch(request) == first
        changed = await owner.dispatch({**request, 'params': {**params, 'expected_revision': 99}})
        assert changed['error']['message'] == 'admission_conflict', changed
        fresh = await owner.dispatch({**request, 'params': {**params, 'request_id': 'different'}})
        assert fresh['error']['message'] == 'not_found', fresh
    finally:
        await owner.close()
        authority.db.close()


def test_route_retirement_rolls_back_with_receipt_and_rejects_stale_mirror(tmp_path):
    from gateway.config import GatewayConfig, Platform
    from gateway.session import SessionStore, SessionSource
    from hermes_state import SessionDB
    import hermes_state_runtime as rt
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        store = SessionStore(tmp_path / 'sessions', GatewayConfig())
        store._db = db
        source = SessionSource(platform=Platform.TELEGRAM, chat_id='owned')
        entry = store.get_or_create_session(source)
        sid = entry.session_id
        if db.get_session(sid) is None:
            db.create_session(sid, source='telegram')
        serialized = json.dumps(entry.to_dict())
        epoch = rt.begin_runtime_epoch(db, instance_id='owner')
        args = dict(epoch=epoch, principal_id='human', session_id=sid, request_id='delete',
                    expected_revision=0, expected_generation=0, operation='delete', payload={})
        db._execute_write(lambda conn: conn.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON state_meta WHEN NEW.key LIKE 'gateway.mutation.v1.%' BEGIN SELECT RAISE(ABORT,'receipt unavailable'); END"))
        with pytest.raises(sqlite3.IntegrityError, match='receipt unavailable'):
            rt.mutate_runtime_session(db, **args)
        assert db.get_session(sid) is not None
        assert db.load_gateway_routing_entries(scope=store._routing_scope())
        db._execute_write(lambda conn: conn.execute('DROP TRIGGER reject_receipt'))
        rt.mutate_runtime_session(db, **args)
        assert not db.load_gateway_routing_entries(scope=store._routing_scope())
        db.save_gateway_routing_entry(entry.session_key, serialized, scope=store._routing_scope())
        db.replace_gateway_routing_entries({entry.session_key: serialized}, scope=store._routing_scope())
        assert not db.load_gateway_routing_entries(scope=store._routing_scope())
        # The pre-commit mirror remains on disk as in a crash before publication.
        restored = SessionStore(store.sessions_dir, GatewayConfig())
        restored._db = db
        assert restored.lookup_by_session_id(sid) is None
