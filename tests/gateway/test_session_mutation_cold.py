"""Cold imported history remains bound and editable after owner restart."""
from types import SimpleNamespace

import pytest

from gateway.session_authority import SessionAuthority
from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch


@pytest.mark.asyncio
async def test_cold_import_mutation_binds_owner_across_restart(tmp_path):
    path = tmp_path / 'state.db'
    with SessionDB(db_path=path) as db:
        authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id='owned', instance_id='first',
                                     db=db, epoch=begin_runtime_epoch(db, instance_id='first'))
        owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
        imported = await owner.dispatch({'id': 1, 'method': 'session.mutate', 'params': {
            'session_id': 'cold', 'request_id': 'import', 'expected_revision': 0, 'operation': 'import',
            'payload': {'sessions': [{'id': 'cold', 'source': 'cli',
                                      'messages': [{'role': 'user', 'content': 'history'}]}]}}})
        assert imported['result']['imported_ids'] == ['cold'], imported
        assert db.get_messages('cold')[0]['content'] == 'history'
        assert 'cold' not in authority.sessions
        edit = {'id': 2, 'method': 'session.mutate', 'params': {'session_id': 'cold', 'request_id': 'edit',
                'expected_revision': db.get_session('cold')['runtime_revision'], 'operation': 'rename',
                'payload': {'title': 'Cold history'}}}
        edited = await owner.dispatch(edit)
        assert edited['result']['title'] == 'Cold history', edited
        stale = await owner.dispatch({**edit, 'params': {**edit['params'], 'request_id': 'competitor'}})
        assert stale['error']['message'] == 'revision_conflict', stale
        await owner.close()
    with SessionDB(db_path=path) as db:
        authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id='owned', instance_id='second',
                                     db=db, epoch=begin_runtime_epoch(db, instance_id='second'))
        owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
        stranger = AuthorityConnection(authority, object(), {'user_id': 'stranger'})
        try:
            assert await owner.dispatch(edit) == edited
            denied = await stranger.dispatch({**edit, 'params': {**edit['params'], 'request_id': 'stranger',
                'expected_revision': db.get_session('cold')['runtime_revision']}})
            assert denied['error']['message'] == 'permission_denied', denied
            assert db.get_session('cold')['title'] == 'Cold history'
        finally:
            await owner.close()
            await stranger.close()
