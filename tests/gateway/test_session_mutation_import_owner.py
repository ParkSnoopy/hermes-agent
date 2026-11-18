"""Import binds each newly imported history to its authorizing principal."""
from types import SimpleNamespace

import pytest

from gateway.session_authority import SessionAuthority
from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB
from hermes_state_mutation_binding import history_binding, binding_names
from hermes_state_runtime import begin_runtime_epoch


@pytest.mark.asyncio
async def test_import_atomically_binds_each_new_history_to_its_importer(tmp_path):
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id='owned', instance_id='owner',
                                     db=db, epoch=begin_runtime_epoch(db, instance_id='owner'))
        owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
        stranger = AuthorityConnection(authority, object(), {'user_id': 'stranger'})
        params = {'session_id': 'cli-import', 'request_id': 'owned-import', 'expected_revision': 0,
                  'operation': 'import', 'payload': {'sessions': [
                      {'id': 'cli-import', 'source': 'cli'}, {'id': 'foreign-import', 'source': 'telegram'}]}}
        try:
            imported = await owner.dispatch({'id': 1, 'method': 'session.mutate', 'params': params})
            assert imported['result']['imported_ids'] == ['cli-import', 'foreign-import'], imported
            for sid in ('cli-import', 'foreign-import'):
                with db._read_ctx() as conn:
                    binding = history_binding(conn, sid)
                assert binding_names(binding, owner.actor.subject)
                assert not binding_names(binding, stranger.actor.subject)
                edit = {'id': 2, 'method': 'session.mutate', 'params': {
                    'session_id': sid, 'request_id': 'edit', 'expected_revision': db.get_session(sid)['runtime_revision'],
                    'operation': 'rename', 'payload': {'title': sid}}}
                denied = await stranger.dispatch(edit)
                assert denied['error']['message'] == 'permission_denied', denied
                renamed = await owner.dispatch(edit)
                assert renamed['result']['title'] == sid, renamed
                assert await owner.dispatch(edit) == renamed
        finally:
            await owner.close()
            await stranger.close()
