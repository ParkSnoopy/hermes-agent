"""Native authority fixture: Ink ``!cmd`` on the shared owner; no inference."""
import asyncio
import io
import json
import os
from pathlib import Path
import sys
import threading
import traceback


async def probe():
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from tui_gateway.transport import StdioTransport

    home = Path(os.environ['HERMES_HOME'])
    work = home / 'session-checkout'
    work.mkdir()
    (home / 'config.yaml').write_text('gateway:\n  multiplex_profiles: false\nmodel:\n  provider: custom\n  default: no-inference\n  base_url: http://127.0.0.1:1/v1\nauxiliary:\n  title_generation:\n    enabled: false\n')
    # A credential the long-lived owner holds must never reach a user-typed child.
    os.environ['OPENROUTER_API_KEY'] = 'sk-or-v1-' + 'f' * 48
    runner = GatewayRunner()
    authority = await initialize_session_authority(runner, profile_id=str(home), instance_id='shell')
    stream = io.StringIO()
    connection = AuthorityConnection(authority, StdioTransport(lambda: stream, threading.Lock()), {'user_id': 'owner'})
    receipt = {'owner_cwd': os.getcwd(), 'session_cwd': str(work)}
    async def rpc(method, **params):
        return await connection.dispatch({'jsonrpc': '2.0', 'id': method, 'method': method, 'params': params})
    try:
        sid = (await rpc('session.create', request_id='shell', source='tui', cwd=str(work),
                         toolsets=[]))['result']['session_id']
        ran = await rpc('shell.exec', session_id=sid,
                        command='pwd; printf "key=%s" "${OPENROUTER_API_KEY:-}" >&2; exit 3')
        receipt['ran'] = ran.get('result', ran.get('error'))
        dangerous = await rpc('shell.exec', session_id=sid, command='rm -rf /')
        receipt['dangerous'] = dangerous.get('error', dangerous.get('result'))
        receipt['sessionless'] = (await rpc('shell.exec', command='pwd')).get('error', {}).get('code')
    finally:
        await connection.close()
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
