"""Reset commits the prepared physical target without changing logical ownership."""
import pytest

from gateway.session_local import create_local_session
from tests.gateway.test_session_mutation_retirement import local_authority


@pytest.mark.asyncio
async def test_reset_receipt_and_physical_target_survive_native_owner_restart(tmp_path, monkeypatch):
    authority, owner = local_authority(tmp_path, monkeypatch)
    db = authority.db
    ref = create_local_session(authority, owner.actor, {'request_id': 'reset-owner', 'source': 'cli',
                               'cwd': str(tmp_path), 'model': 'm', 'toolsets': []})
    row = db.get_session(ref.session_id)
    params = {'session_id': ref.session_id, 'request_id': 'reset', 'expected_revision': row['runtime_revision'],
              'expected_generation': row['runtime_generation'], 'operation': 'reset', 'payload': {}}
    request = {'id': 1, 'method': 'session.mutate', 'params': params}
    response = await owner.dispatch(request)
    assert 'result' in response, response
    receipt = response['result']
    target = receipt['target_session_id']
    assert receipt['session_id'] == ref.session_id and target != ref.session_id
    root = db.get_session(ref.session_id)
    assert (root['runtime_revision'], root['runtime_generation'], root['end_reason']) == (
        receipt['revision'], receipt['execution_generation'], 'session_reset')
    assert db.get_session(target)['parent_session_id'] == ref.session_id
    assert db.get_messages(target) == []
    stale = await owner.dispatch({**request, 'params': {**params, 'request_id': 'stale'}})
    assert stale['error']['message'] == 'revision_conflict', stale
    await owner.close()
    db.close()
    authority, owner = local_authority(tmp_path, monkeypatch, instance_id='restarted')
    try:
        assert await owner.dispatch(request) == response
    finally:
        await owner.close()
        authority.db.close()
