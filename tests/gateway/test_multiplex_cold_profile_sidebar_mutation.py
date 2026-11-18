"""Cold profile mutations stay scoped while another profile has a live session."""
from pathlib import Path

import pytest

from gateway.run_runtime import initialize_gateway_runtime
from gateway.runtime_ownership import process_ownership
from gateway.session_authorities import owner_scope
from gateway.session_controls import AuthorityConnection
from gateway.session_local import create_local_session
from tests.gateway.test_session_authorities_multiplex import _reserve_homes, _runner


@pytest.mark.asyncio
async def test_cold_profile_archive_unarchive_while_another_profile_is_live(tmp_path, monkeypatch):
    root, homes = _reserve_homes(tmp_path, monkeypatch, names=('warm', 'cold'))
    process_ownership.reserve([home for _, home in homes])
    runner = None
    connections = []
    try:
        runner = _runner(root, homes)
        await initialize_gateway_runtime(runner)
        warm, cold = (runner.session_authorities.for_home(home) for _, home in homes[1:])
        for authority in (warm, cold):
            with owner_scope(authority):
                connections.append(AuthorityConnection(authority, object(), {'user_id': 'human'}))
        warm_owner, cold_owner = connections
        refs = []
        for authority, owner, name in ((warm, warm_owner, 'warm'), (cold, cold_owner, 'cold')):
            with owner_scope(authority):
                refs.append(create_local_session(authority, owner.actor, {'request_id': name, 'source': 'cli',
                    'cwd': str(tmp_path), 'model': 'm', 'toolsets': []}))
        warm_ref, cold_ref = refs
        with owner_scope(warm):
            attached = await warm_owner.dispatch({'id': 4, 'method': 'session.resume',
                                                  'params': {'session_id': warm_ref.session_id}})
        assert 'result' in attached, attached
        assert warm_ref.session_id in warm_owner.subscriptions
        params = {'session_id': cold_ref.session_id, 'request_id': 'archive',
                  'expected_revision': cold.db.get_session(cold_ref.session_id)['runtime_revision'],
                  'operation': 'archive', 'payload': {'archived': True}}
        with owner_scope(cold):
            archived = await cold_owner.dispatch({'id': 1, 'method': 'session.mutate', 'params': params})
            assert archived['result']['archived'] is True, archived
            params = {**params, 'request_id': 'unarchive', 'expected_revision': archived['result']['revision'],
                      'payload': {'archived': False}}
            unarchived = await cold_owner.dispatch({'id': 2, 'method': 'session.mutate', 'params': params})
        assert unarchived['result']['archived'] is False, unarchived
        assert unarchived['result']['revision'] == archived['result']['revision'] + 1
        assert cold.db.get_session(cold_ref.session_id)['archived'] == 0
        assert runner.session_authority.db.get_session(cold_ref.session_id) is None
        assert warm_ref.session_id in warm.sessions
        with owner_scope(warm):
            foreign = await warm_owner.dispatch({'id': 3, 'method': 'session.mutate',
                'params': {**params, 'profile': 'cold'}})
        assert foreign['error']['message'] == 'profile_mismatch', foreign
    finally:
        for connection in connections:
            with owner_scope(connection.authority):
                await connection.close()
        for authority in list(getattr(runner, 'session_authorities', []) or []):
            authority.db.close()
        for _, home in homes:
            process_ownership.release(Path(home))
