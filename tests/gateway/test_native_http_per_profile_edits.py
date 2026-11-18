"""Profile-local sidebar edits retain history and refuse foreign ownership."""
from types import SimpleNamespace

import pytest

from gateway.session_authority import SessionAuthority
from gateway.session_contract import SessionActor, SessionRef
from gateway.session_mutations import mutate_session
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['cli', 'tui'])
@pytest.mark.parametrize('operation,payload,field,expected', [
    ('sidebar', {'pinned': True}, 'pinned', True),
    ('archive', {'archived': True}, 'archived', True),
    ('rename', {'title': 'Owned history'}, 'title', 'Owned history'),
])
async def test_per_profile_edits_stay_in_their_own_store(tmp_path, source, operation, payload, field, expected):
    caps = frozenset({'session:read', 'session:create', 'session:control'})
    with SessionDB(db_path=tmp_path / 'root.db') as root, SessionDB(db_path=tmp_path / 'beta.db') as beta:
        authorities = {}
        for name, db in (('root', root), ('beta', beta)):
            authorities[name] = SessionAuthority(SimpleNamespace(_draining=False), profile_id=name,
                instance_id='owner', db=db, epoch=begin_runtime_epoch(db, instance_id='owner'))
        root.create_session('shared-id', source=source)
        beta.create_session('foreign', source='cli', user_id='telegram:42')
        root_before = root.get_session('shared-id')
        foreign_before = beta.get_session('foreign')
        actor = SessionActor('uid:owner', 'beta', caps, 'local')
        imported = await mutate_session(authorities['beta'], actor, SessionRef('beta', 'shared-id'), {
            'session_id': 'shared-id', 'request_id': 'import', 'expected_revision': 0,
            'operation': 'import', 'payload': {'sessions': [{'id': 'shared-id', 'source': source,
                'messages': [{'role': 'user', 'content': 'history'},
                             {'role': 'assistant', 'content': 'reply'}]}]}})
        assert imported['imported_ids'] == ['shared-id']
        params = {'session_id': 'shared-id', 'request_id': 'edit', 'expected_revision': beta.get_session('shared-id')['runtime_revision'],
                  'operation': operation, 'payload': payload}
        result = await mutate_session(authorities['beta'], actor, SessionRef('beta', 'shared-id'), params)
        assert result[field] == expected
        assert beta.get_session('shared-id')[field] == expected
        assert [row['content'] for row in beta.get_messages('shared-id')] == ['history', 'reply']
        assert root.get_session('shared-id') == root_before
        with pytest.raises(RuntimeStoreError, match='profile_mismatch'):
            await mutate_session(authorities['root'], actor, SessionRef('root', 'shared-id'), params)
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            await mutate_session(authorities['beta'], actor, SessionRef('beta', 'foreign'),
                {**params, 'session_id': 'foreign', 'request_id': 'foreign'})
        assert beta.get_session('foreign') == foreign_before
