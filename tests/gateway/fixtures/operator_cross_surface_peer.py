"""Native operator connections share durable admissions without sharing grants."""
import asyncio
import json
import os
from pathlib import Path
import sys
import traceback


async def probe():
    from gateway.owner_identity import host_owner_subject
    from gateway.runtime_bootstrap import TicketStore
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from hermes_state_local import local_receipt
    from hermes_state_runtime import list_session_admissions
    from tests.gateway.fixtures.execution_frames_capture import Peer

    home = Path(os.environ['HERMES_HOME'])
    (home / 'config.yaml').write_text(
        'gateway:\n  multiplex_profiles: false\nmodel:\n  provider: custom\n'
        '  default: no-inference\n  base_url: http://127.0.0.1:1/v1\n'
        'auxiliary:\n  title_generation:\n    enabled: false\n')
    runner = GatewayRunner()
    authority = await initialize_session_authority(runner, profile_id=str(home), instance_id='operator')
    tickets = TicketStore('operator', frozenset({str(home)}))
    ticket = tickets.mint(profile_id=str(home), subject=host_owner_subject(), purpose='interactive')
    grant = tickets.redeem(ticket, profile_id=str(home), purpose='interactive')
    identity = {'user_id': grant['subject'], 'provider': 'local', 'profile_id': grant['profile_id'],
                'instance_id': grant['instance_id'], 'capabilities': grant['capabilities']}
    try:
        tickets.mint(profile_id=str(home / 'other'), subject=host_owner_subject(), purpose='interactive')
    except PermissionError:
        pass
    else:
        raise AssertionError('bootstrap accepted foreign profile')
    owner = AuthorityConnection(authority, Peer(), identity, operator=True)
    viewer = AuthorityConnection(authority, Peer(), identity, operator=True)
    # A worker-adoption grant is neither an interactive grant nor an operator.
    ticket = tickets.mint(profile_id=str(home), subject=host_owner_subject(), purpose='worker-adoption')
    worker_grant = tickets.redeem(ticket, profile_id=str(home), purpose='worker-adoption')
    restricted = AuthorityConnection(authority, Peer(), {**identity, 'capabilities': worker_grant['capabilities']})
    foreign = AuthorityConnection(authority, Peer(), {**identity, 'profile_id': str(home / 'other')}, operator=True)
    wrong_instance = AuthorityConnection(authority, Peer(), {**identity, 'instance_id': 'another-instance'})
    connections = [owner, viewer, restricted, foreign, wrong_instance]
    parked = None

    async def rpc(connection, method, error=None, **params):
        reply = await connection.dispatch({'id': method, 'method': method, 'params': params})
        if error:
            assert reply.get('error', {}).get('message') == error, reply
            return reply['error']
        assert 'result' in reply, reply
        return reply['result']

    try:
        created = await rpc(owner, 'session.create', request_id='operator-create', source='tui',
                            cwd=str(home), toolsets=[])
        sid = created['session_id']
        assert created['stored_session_id'] == sid
        original_owner = local_receipt(authority.db, sid)['principal_id']
        initial = await rpc(owner, 'session.resume', session_id=sid)
        await rpc(restricted, 'session.resume', error='permission_denied', session_id=sid)
        await rpc(restricted, 'prompt.submit', error='permission_denied', session_id=sid,
                  input_id='restricted', text='not admitted')
        await rpc(foreign, 'session.resume', error='profile_mismatch', session_id=sid)
        await rpc(wrong_instance, 'session.resume', error='permission_denied', session_id=sid)
        for field, value in (('operator', True), ('operator_scope', str(home)),
                             ('capabilities', ['session:operator'])):
            await rpc(viewer, 'session.create', error='invalid_params',
                      request_id='unsupported-' + field, source='tui', **{field: value})

        attached = await rpc(viewer, 'session.resume', session_id=sid)
        assert attached['stored_session_id'] == sid
        assert attached['replay_epoch'] == initial['replay_epoch']
        assert attached['subscription_id'] != initial['subscription_id']
        listing = await rpc(viewer, 'session.list')
        assert sid in {row['session_id'] for row in listing['sessions']}

        # Keep the real admission queue parked, without starting inference or replacing dispatch.
        parked = asyncio.create_task(asyncio.Event().wait())
        authority.sessions[sid].task = parked
        first = await rpc(owner, 'prompt.submit', session_id=sid, input_id='first', text='first')
        assert first['status'] == 'queued', first
        followup = dict(session_id=sid, input_id='followup', text='followup')
        accepted = await rpc(viewer, 'prompt.submit', **followup)
        assert accepted['status'] == 'queued', accepted
        assert await rpc(viewer, 'prompt.submit', **followup) == accepted
        rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
        assert [row['payload']['text'] for row in rows] == ['first', 'followup'], rows
        assert all(row['principal_id'] == original_owner for row in rows), rows
        assert all('local_operator_v1' not in row['payload'] for row in rows), rows
        assert original_owner.startswith('uid:'), original_owner
        await owner.close()
        await viewer.close()
        resumed = AuthorityConnection(authority, Peer(), identity, operator=True)
        connections.append(resumed)
        assert (await rpc(resumed, 'session.resume', session_id=sid))['stored_session_id'] == sid
        assert await rpc(resumed, 'prompt.submit', **followup) == accepted
        assert len(list_session_admissions(authority.db, session_id=sid, pending_only=False)) == 2
        replay = await rpc(resumed, 'session.events.since', session_id=sid,
                           replay_epoch=initial['replay_epoch'], last_sequence=initial['last_sequence'])
        assert not replay['snapshot_required'], replay
        snapshot = await rpc(resumed, 'session.resume', session_id=sid)
        assert 'local_operator_v1' not in json.dumps([initial, attached, accepted, replay, snapshot])
        assert local_receipt(authority.db, sid)['principal_id'] == original_owner
        (home / 'operator-receipt.json').write_text(json.dumps({'native_boundaries': True}))
    finally:
        if parked is not None:
            parked.cancel()
            await asyncio.gather(parked, return_exceptions=True)
        for connection in connections:
            await connection.close()
        runner.session_store.close_all_db_handles()


if __name__ == '__main__':
    status = 0
    try:
        asyncio.run(probe())
    except BaseException:
        traceback.print_exc()
        status = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)