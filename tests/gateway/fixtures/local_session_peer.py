"""Owned loopback fixture: no native credentials or alternative agent runtime."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import sys
import threading
import traceback

class ModelPeer(BaseHTTPRequestHandler):
    """Loopback model for the approval and clarification execution fixtures."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data": []}')

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = request.get('messages', [])
        asking = bool(messages) and not any(m['role'] == 'tool' for m in messages)
        kind = self.server.kind
        self.server.requests.append(messages)
        message = {'role': 'assistant', 'content': kind.upper() + '_FINISHED'}
        if asking:
            arguments = ({'command': self.server.command} if kind == 'approval' else
                         {'questions': [{'question': 'Pick a color', 'choices': ['blue', 'green']}]})
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'owned-' + kind, 'type': 'function', 'function': {
                    'name': 'terminal' if kind == 'approval' else 'clarify',
                    'arguments': json.dumps(arguments)}}]}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if request.get('stream') else 'application/json')
        self.end_headers()
        if request.get('stream'):
            if asking:
                message['tool_calls'][0]['index'] = 0
            for delta, finish in [(message, None), ({}, 'tool_calls' if asking else 'stop')]:
                frame = {'id': 'local', 'object': 'chat.completion.chunk', 'created': 1,
                         'model': 'local-control', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(frame) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        else:
            self.wfile.write(json.dumps({'id': 'local', 'object': 'chat.completion', 'created': 1,
                'model': 'local-control', 'choices': [{'index': 0, 'message': message,
                'finish_reason': 'tool_calls' if asking else 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode())
        self.wfile.flush()


async def probe(peer, target, kind):
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import SessionRef
    from gateway.session_controls import AuthorityConnection
    from tests.gateway.fixtures.execution_frames_capture import Peer
    from run_agent import AIAgent
    from tools.approval import resolve_gateway_approval
    from tests.waiting import async_wait_until

    runner = GatewayRunner()
    authority = await initialize_session_authority(runner, profile_id='fixture', instance_id='local')
    sid = None
    sockets = []

    async def connect():
        ws = AuthorityConnection(authority, Peer(), {'user_id': 'fixture-creator'}, operator=True)
        sockets.append(ws)
        return ws

    async def rpc(ws, method, **params):
        return await ws.dispatch({'jsonrpc': '2.0', 'id': method, 'method': method, 'params': params})

    try:
        a = await connect()
        for payload in ({'source': 'telegram'}, {'source': 'cron'}, {'internal': True},
                        {'profile': 'foreign'}, {'cwd': 'relative'}, {'checkpoints': 'yes'}):
            denied = await rpc(a, 'session.create', **payload)
            assert 'error' in denied, denied
        before_sessions = set(authority.sessions)
        runner._draining = True
        try:
            draining = await rpc(a, 'session.create', request_id='during-drain', source='cli')
            assert draining.get('error', {}).get('message') == 'runtime_draining', draining
            assert set(authority.sessions) == before_sessions
        finally:
            runner._draining = False
        created = await rpc(a, 'session.create', request_id='fresh', source='cli')
        assert 'result' in created, created
        sid = created['result']['session_id']
        assert sid == created['result']['stored_session_id']
        listed = await rpc(a, 'session.list', limit=10)
        assert any(row['session_id'] == sid for row in listed['result']['sessions']), listed
        info = await rpc(a, 'session.info', session_id=sid)
        assert info['result']['source'] == 'cli', info
        assert info['result']['lazy'] is True, info
        assert authority.db.get_session(sid) is not None
        # Private route identity cannot be reconstructed from its serialized source,
        # even under an unrelated messaging allow-all policy.
        from dataclasses import replace
        source = authority.sessions[sid].source
        assert runner._is_user_authorized_for_source(source)
        os.environ['GATEWAY_ALLOW_ALL_USERS'] = 'true'
        try:
            assert not runner._is_user_authorized_for_source(replace(source, role_authorized=True))
            assert not runner._is_user_authorized_for_source(replace(source, profile='foreign'))
        finally:
            os.environ.pop('GATEWAY_ALLOW_ALL_USERS')
        from gateway.session_local import create_local_session
        from gateway.session_contract import SessionActor
        from hermes_state_runtime import RuntimeStoreError
        for actor, reason in ((SessionActor('read-only', 'fixture', frozenset({'session:read'}), 'readonly'),
                               'permission_denied'),
                              (SessionActor('creator', 'foreign', frozenset({'session:create'}), 'foreign'),
                               'profile_mismatch')):
            try:
                create_local_session(authority, actor, {'api_key': 'denied-launch-key',
                    'provider': 'custom', 'base_url': 'http://127.0.0.1:9/v1', 'ignore_rules': True})
            except RuntimeStoreError as exc:
                assert exc.reason == reason
            else:
                raise AssertionError('restricted principal created a local route')
        repeated = await rpc(a, 'session.create', request_id='fresh', source='cli')
        assert repeated['result']['session_id'] == sid, repeated
        b = await connect()
        resumed = await rpc(b, 'session.resume', session_id=sid)
        assert resumed['result']['session_id'] == sid, resumed
        submitted = await rpc(a, 'prompt.submit', session_id=sid, input_id='first', text='Remove owned fixture')
        assert submitted.get('result', {}).get('status') == 'queued', submitted
        async def pending_prompt():
            snapshot = await rpc(b, 'session.resume', session_id=sid)
            assert 'result' in snapshot, snapshot
            return snapshot if snapshot['result'].get('prompts') else None

        snapshot = await async_wait_until(pending_prompt, timeout=25,
                                          what='pending prompt on the shared attachment')
        assert target.exists(), 'effect preceded consent'
        ref = SessionRef(authority.profile_id, sid)
        agent = authority.agent(ref)
        assert isinstance(agent, AIAgent)
        prompt = snapshot['result']['prompts'][0]
        assert prompt['kind'] == kind, prompt
        await a.close()
        await b.close()
        assert target.exists()
        c = await connect()
        snapshot = await rpc(c, 'session.resume', session_id=sid)
        assert snapshot['result']['prompts'][0]['prompt_id'] == prompt['prompt_id']
        assert authority.agent(ref) is agent
        response = {'choice': 'once'} if kind == 'approval' else {'answer': 'green'}
        answer = await rpc(c, kind + '.respond', session_id=sid,
                           prompt_id=prompt['prompt_id'], execution_generation=prompt['execution_generation'], **response)
        assert answer.get('result', {}).get('status') == 'resolved', answer
        await asyncio.wait_for(authority.sessions[sid].task, 25)
        if kind == 'approval':
            assert not target.exists(), 'real owned terminal effect missing'
        else:
            assert any('green' in json.dumps(m) for messages in peer.requests
                       for m in messages if m['role'] == 'tool'), peer.requests
        final = await rpc(c, 'session.resume', session_id=sid)
        assert final['result']['session_id'] == sid
        assert any(m.get('content') == kind.upper() + '_FINISHED' for m in final['result']['messages']), final
        assert authority.agent(ref) is agent
        pong = await rpc(c, 'ping')
        assert pong.get('result', {}).get('pong') is True, pong
        described = await rpc(c, 'runtime.describe')
        assert described['result']['instance_id'] == authority.instance_id
        assert str(Path(os.environ['HERMES_HOME'])) not in json.dumps(described)
        # An unverified identity cannot create a local session.
        bad = AuthorityConnection(authority, Peer(), {})
        sockets.append(bad)
        before_sessions = set(authority.sessions)
        denied = await rpc(bad, 'session.create', request_id='forged')
        assert denied.get('error', {}).get('message') == 'permission_denied', denied
        assert set(authority.sessions) == before_sessions
        Path(os.environ['HERMES_HOME'], 'receipt.json').write_text(json.dumps({
            'terminal_effect': not target.exists(), 'human_response': True,
            'same_agent': True, 'detached_pending': True,
            'negative_controls': True, 'reconnected_identity': sid, 'model_requests': len(peer.requests)}))
    finally:
        if sid is not None:
            resolve_gateway_approval(authority.sessions[sid].route, 'deny', resolve_all=True)
            from tools import clarify_gateway
            clarify_gateway.clear_session(authority.sessions[sid].route)
            task = authority.sessions[sid].task
            if task is not None:
                await asyncio.wait_for(task, 30)
        for ws in sockets:
            await ws.close()
        runner.session_store.close_all_db_handles()


def main():
    target = Path(os.environ['HERMES_HOME'], 'owned-removal')
    target.mkdir()
    (target / 'owned.txt').write_text('disposable')
    kind = sys.argv[1] if len(sys.argv) > 1 else 'approval'
    peer = ThreadingHTTPServer(('127.0.0.1', 0), ModelPeer)
    peer.kind = kind
    peer.requests = []
    peer.command = 'rm -r -- ' + shlex.quote(str(target))
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{peer.server_port}/v1'
    os.environ.update(OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=url, TERMINAL_ENV='local')
    Path(os.environ['HERMES_HOME'], 'config.yaml').write_text(
        f'model:\n  default: local-control\n  provider: custom\n  base_url: {url}\n'
        'approvals:\n  mode: manual\n  timeout: 45\n'
        'streaming:\n  enabled: false\n'
        'auxiliary:\n  title_generation:\n    enabled: false\n')
    try:
        asyncio.run(probe(peer, target, kind))
    finally:
        peer.shutdown()
        peer.server_close()


if __name__ == '__main__':
    status = 0
    try:
        main()
    except BaseException:
        traceback.print_exc()
        status = 1
    os._exit(status)
