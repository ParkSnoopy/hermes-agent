"""Native authority process/MCP controls scoped to the owning session."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import sys
import traceback


async def probe():
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from tests.gateway.fixtures.execution_frames_capture import Peer
    from tools.process_registry import process_registry

    home = Path(os.environ['HERMES_HOME'])
    (home / 'config.yaml').write_text('gateway:\n  multiplex_profiles: false\nmodel:\n  provider: custom\n  default: no-inference\n  base_url: http://127.0.0.1:1/v1\nauxiliary:\n  title_generation:\n    enabled: false\n')
    runner = GatewayRunner()
    authority = await initialize_session_authority(runner, profile_id=str(home), instance_id='local-controls')
    ws = AuthorityConnection(authority, Peer(), {'user_id': 'owner'}, operator=True)
    reader = AuthorityConnection(authority, Peer(), {'user_id': 'owner', 'capabilities': ['session:read']})
    async def rpc(connection, method, **params):
        return await connection.dispatch({'jsonrpc': '2.0', 'id': method,
                                          'method': method, 'params': params})
    receipt, processes = {}, []
    command = shlex.quote(sys.executable) + ' -c ' + shlex.quote('import time; time.sleep(60)')
    try:
        sid = (await rpc(ws, 'session.create', request_id='process-controls', source='tui', toolsets=[]))['result']['session_id']
        route = authority.sessions[sid].route
        for task, key in [(sid, route), (sid, route), ('foreign', 'agent:main:telegram:dm:1')]:
            processes.append(process_registry.spawn_local(command, cwd=str(home), task_id=task, owner_task_id=task,
                                                          session_key=key))
        own, other, foreign = processes
        attached = await rpc(reader, 'session.resume', session_id=sid)
        assert attached.get('result', {}).get('session_id') == sid, attached
        for method, params in [('process.kill', {'process_id': own.id}),
                               ('process.stop', {}), ('reload.mcp', {'confirm': True})]:
            denied = await rpc(reader, method, session_id=sid, **params)
            assert denied.get('error', {}).get('message') == 'permission_denied', denied
        assert not any(proc.exited for proc in processes)
        # The classic CLI's dock reads its rows' timing / verdict fields from process.list.
        listed = (await rpc(ws, 'process.list', session_id=sid))['result']['processes']
        receipt['list_fields'] = sorted({k for row in listed for k in ('started_epoch', 'exited_at', 'completion_reason')
                                         if k in row})
        receipt['list_ids'] = sorted(row['session_id'] for row in listed) == sorted([own.id, other.id])
        # The classic CLI's /tools: the session's catalog from the owner (toolsets=[] -> none).
        shown = await rpc(ws, 'tools.show', session_id=sid)
        receipt['tools_show'] = shown.get('result', shown.get('error'))
        # The classic CLI's /reload-skills: the owner rescans (a skill added on disk shows as added).
        skill = home / 'skills' / 'test' / 'zeta-skill'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: zeta-skill\ndescription: Zeta.\n---\nZETA\n')
        reloaded = await rpc(ws, 'skills.reload', session_id=sid)
        receipt['skills_reload'] = [a.get('name') for a in reloaded.get('result', {}).get('result', {}).get('added', [])] \
            if 'result' in reloaded else reloaded.get('error')
        # Kill one process owned by this session.
        kill = await rpc(ws, 'process.kill', process_id=own.id, session_id=sid)
        await asyncio.to_thread(own._completion_event.wait, 10)
        receipt['kill'] = kill.get('result', {}).get('status', kill.get('error'))
        receipt['foreign_kill'] = (await rpc(ws, 'process.kill', process_id=foreign.id, session_id=sid)).get('error', {}).get('message')
        # Stop the remaining owned process.
        stop = await rpc(ws, 'process.stop', session_id=sid)
        await asyncio.to_thread(other._completion_event.wait, 10)
        receipt['stop'] = stop.get('result', stop.get('error'))
        receipt['exited'] = [p.exited for p in processes]
        # Reconcile the owning profile MCP configuration.
        unconfirmed = await rpc(ws, 'reload.mcp', session_id=sid)
        assert unconfirmed.get('result', {}).get('status') == 'confirm_required', unconfirmed
        reload = await rpc(ws, 'reload.mcp', confirm=True, session_id=sid)
        receipt['reload_mcp'] = reload.get('result', {}).get('status', reload.get('error'))
        # Stage a local file and client-supplied bytes.
        (home / 'note.txt').write_text('hello')
        by_path = await rpc(ws, 'file.attach', name='note.txt', path=str(home / 'note.txt'), session_id=sid)
        by_bytes = await rpc(ws, 'file.attach', name='n.txt', path='C:/x/n.txt', data_url='data:text/plain;base64,aGk=',
                             session_id=sid)
        receipt['file_attach'] = [r.get('result', {}).get('ref_text', r.get('error')) for r in (by_path, by_bytes)]
        receipt['staged'] = sorted(p.read_text() for p in (home / 'attachments').iterdir())
    finally:
        for proc in processes:
            if not proc.exited:
                process_registry.kill_process(proc.id)
        await reader.close()
        await ws.close()
        runner.session_store.close_all_db_handles()
    (home / 'receipt.json').write_text(json.dumps(receipt))


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
