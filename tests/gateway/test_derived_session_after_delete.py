"""A deleted hosted-room member session continues on a new generation."""
import pytest


def _delete_past_the_horizon(authority, sid):
    from hermes_state_mutation_retirement import prune_retired_tombstones
    assert authority.db.delete_session(sid)
    authority.sessions.pop(sid, None)
    authority.db._execute_write(lambda conn: prune_retired_tombstones(conn, older_than=0))


@pytest.mark.asyncio
async def test_a_hosted_room_member_rebinds_on_a_new_session_after_a_delete(api, owner):
    from gateway.hosted_room_peer import HostedMemberDispatch
    from gateway.session_api import restore_api_session
    from tests.gateway.test_hosted_room_execution_policy import _dispatch, _policy
    dispatch = _dispatch(_policy())
    deleted = await api._ensure_hosted_member_session(dispatch)
    _delete_past_the_horizon(owner, deleted)
    later = HostedMemberDispatch.from_mapping(dict(dispatch.as_mapping(), task_id='task-2', execution_generation=2))
    rebound = await api._ensure_hosted_member_session(later)
    assert rebound != deleted
    assert owner.db.get_session(deleted) is None, 'the deleted member session was recreated'
    assert owner.db.get_session(rebound)['title'] == f'Group: {dispatch.room_id}'
    assert await api._ensure_hosted_member_session(later) == rebound
    owner.sessions.pop(rebound, None)
    assert restore_api_session(owner, rebound).session_id == rebound
