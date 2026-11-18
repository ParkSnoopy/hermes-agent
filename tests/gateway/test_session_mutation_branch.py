"""Branches keep an independent route and frozen policy across owner restart."""
import pytest

from gateway.session_local import create_local_session
from hermes_state_local import local_receipt
from tests.gateway.test_session_mutation_retirement import local_authority


@pytest.mark.asyncio
async def test_branch_route_and_frozen_policy_survive_owner_restart(tmp_path, monkeypatch):
    authority, owner = local_authority(tmp_path, monkeypatch)
    db = authority.db
    ref = create_local_session(authority, owner.actor, {'request_id': 'branch-owner', 'source': 'cli',
                               'cwd': str(tmp_path), 'model': 'm', 'toolsets': []})
    row = db.get_session(ref.session_id)
    params = {'session_id': ref.session_id, 'request_id': 'branch', 'expected_revision': row['runtime_revision'],
              'expected_generation': row['runtime_generation'], 'operation': 'branch', 'payload': {}}
    request = {'id': 1, 'method': 'session.mutate', 'params': params}
    result = await owner.dispatch(request)
    assert 'result' in result, result
    child = result['result']['branched_session_id']
    parent_policy, child_policy = (local_receipt(db, sid) for sid in (ref.session_id, child))
    assert child != ref.session_id
    assert parent_policy['policy'] == child_policy['policy']
    assert parent_policy['principal_id'] == child_policy['principal_id']
    assert parent_policy['route'] != child_policy['route']
    assert db.get_session(child)['parent_session_id'] == ref.session_id
    assert db.get_session(child)['title']
    assert child in owner.subscriptions
    stale = await owner.dispatch({**request, 'params': {**params, 'request_id': 'stale'}})
    assert stale['error']['message'] == 'revision_conflict', stale
    changed = await owner.dispatch({**request, 'params': {**params, 'payload': {'title': 'changed'}}})
    assert changed['error']['message'] == 'admission_conflict', changed
    await owner.close()
    db.close()
    authority, owner = local_authority(tmp_path, monkeypatch, instance_id='restarted')
    try:
        assert await owner.dispatch(request) == result
    finally:
        await owner.close()
        authority.db.close()
