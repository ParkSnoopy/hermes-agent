"""Deleting a logical local owner retires its physical transcript and cache generation."""
import json

import pytest

from gateway.session_local import create_local_session
from tests.gateway.test_session_mutation_retirement import local_authority


@pytest.mark.asyncio
async def test_delete_after_reset_covers_physical_route_and_cache_generation(tmp_path, monkeypatch):
    authority, owner = local_authority(tmp_path, monkeypatch)
    db = authority.db
    try:
        ref = create_local_session(authority, owner.actor, {'request_id': 'reset-owner', 'source': 'cli',
                                   'cwd': str(tmp_path), 'model': 'm', 'toolsets': []})
        sid = ref.session_id
        row = db.get_session(sid)
        reset = await owner.dispatch({'id': 1, 'method': 'session.mutate', 'params': {
            'session_id': sid, 'request_id': 'reset', 'expected_revision': row['runtime_revision'],
            'expected_generation': row['runtime_generation'], 'operation': 'reset', 'payload': {}}})
        assert 'result' in reset, reset
        receipt = reset['result']
        target = receipt['target_session_id']
        with db._read_ctx() as conn:
            route, source = conn.execute('SELECT session_key,source FROM sessions WHERE id=?', (target,)).fetchone()
            before = conn.execute('SELECT generation FROM conversation_generations WHERE source=? AND session_key=?',
                                  (source, route)).fetchone()[0]
        deleted = await owner.dispatch({'id': 2, 'method': 'session.mutate', 'params': {
            'session_id': sid, 'request_id': 'delete-reset', 'expected_revision': receipt['revision'],
            'expected_generation': receipt['execution_generation'], 'operation': 'delete', 'payload': {}}})
        assert set(deleted['result']['deleted_ids']) == {sid, target}, deleted
        assert db.get_session(sid) is None and db.get_session(target) is None
        assert not any(json.loads(row)['session_id'] in {sid, target}
                       for row in db.load_gateway_routing_entries(scope='').values())
        with db._read_ctx() as conn:
            after = conn.execute('SELECT generation FROM conversation_generations WHERE source=? AND session_key=?',
                                 (source, route)).fetchone()[0]
        assert after > before
    finally:
        await owner.close()
        db.close()
