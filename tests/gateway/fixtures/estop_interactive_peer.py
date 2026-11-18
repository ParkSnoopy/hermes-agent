"""GW-1 fixture: `hermes pause` holds every gateway turn, including interactive (TUI/CLI) ones.

A real GatewayRunner + session authority + native event transport in a fake HERMES_HOME, a loopback model. Phase 1
starts a turn that blocks in the model, engages the ESTOP and releases it: the running turn must
finish normally. Phase 2 submits a new interactive turn while paused: its completion must carry
``estop.paused_reply()`` (the reason and "run `hermes resume`") and the model is never called.
Phase 3 resumes and the next turn runs. Writes receipt.json into HERMES_HOME.
"""
import asyncio
from http.server import ThreadingHTTPServer
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
    from tests.gateway.fixtures.local_recovery_probe import Model
    from agent import estop

    home = Path(os.environ['HERMES_HOME'])
    peer = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    peer.requests, peer.blocked, peer.release = [], threading.Event(), threading.Event()
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{peer.server_port}/v1'
    os.environ.update(OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=url)
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'provider': 'custom', 'default': 'loopback', 'base_url': url},
        'auxiliary': {'title_generation': {'enabled': False}}, 'platform_toolsets': {'cli': []}}))
    runner = GatewayRunner()
    authority = await initialize_session_authority(runner, profile_id=str(home), instance_id='estop-probe')
    loop = asyncio.get_running_loop()
    frames = asyncio.Queue()

    class EventTransport:
        def write(self, frame):
            loop.call_soon_threadsafe(frames.put_nowait, frame)
            return True

        def close(self):
            pass

    connection = AuthorityConnection(authority, EventTransport(), {'user_id': 'owner'})
    receipt = {}
    try:
        async def rpc(method, **params):
            return await connection.dispatch({'jsonrpc': '2.0', 'id': method, 'method': method, 'params': params})

        async def complete():
            while True:
                frame = await asyncio.wait_for(frames.get(), 60)
                event = frame.get('params', {})
                if event.get('type') == 'message.complete':
                    return event.get('payload') or {}

        sid = (await rpc('session.create', request_id='estop', source='tui', cwd=str(home),
                         toolsets=[]))['result']['session_id']
        # Phase 1: a turn already running when the pause lands finishes.
        await rpc('prompt.submit', session_id=sid, input_id='running', text='BLOCK_STARTED')
        assert await asyncio.to_thread(peer.blocked.wait, 30), 'model never reached'
        estop.engage(reason='maintenance window')
        peer.release.set()
        receipt['running'] = await complete()
        # Phase 2: a new interactive turn while paused gets the named refusal; no model call.
        calls = len(peer.requests)
        receipt['paused_submit'] = (await rpc('prompt.submit', session_id=sid, input_id='paused',
                                              text='NEW_WHILE_PAUSED')).get('result')
        receipt['paused'] = await complete()
        receipt['paused_model_calls'] = len(peer.requests) - calls
        receipt['expected_notice'] = estop.paused_reply()
        # Phase 3: resume; the next turn runs.
        estop.disengage()
        await rpc('prompt.submit', session_id=sid, input_id='after', text='AFTER_RESUME')
        receipt['after'] = await complete()
    finally:
        await connection.close()
        runner.session_store.close_all_db_handles()
        peer.shutdown()
    (home / 'receipt.json').write_text(json.dumps(receipt, default=str))


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
