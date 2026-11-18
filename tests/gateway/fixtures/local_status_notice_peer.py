"""Real owner, real AIAgent turn, native local viewer: status and notices."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import traceback


class Peer(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"data": []}')

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.send_response(200)
        if request.get('stream'):
            self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            for delta, fin in [({'role': 'assistant', 'content': 'DONE'}, None), ({}, 'stop')]:
                frame = {'id': 'x', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'm',
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': fin}]}
                self.wfile.write(('data: ' + json.dumps(frame) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
            return
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'id': 'x', 'object': 'chat.completion', 'created': 1, 'model': 'm',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'DONE'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode())


async def probe():
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from tests.gateway.fixtures.execution_frames_capture import Peer as CapturePeer
    import run_agent
    from agent.credits_tracker import AgentNotice

    original = run_agent.AIAgent.run_conversation

    def run_conversation(self, *args, **kwargs):
        self._emit_warning('PROBE_WARNING_STATUS')
        self._emit_status('PROBE_LIFECYCLE_STATUS')
        self._emit_notice(AgentNotice(text='PROBE_NOTICE', level='warn', key='probe', id='probe'))
        self._emit_notice_clear('probe')  # recovery withdraws the sticky notice (main: notification.clear)
        return original(self, *args, **kwargs)
    run_agent.AIAgent.run_conversation = run_conversation

    runner = GatewayRunner()
    authority = await initialize_session_authority(runner, profile_id='fixture', instance_id='local')
    peer = CapturePeer()
    ws = AuthorityConnection(authority, CapturePeer(), {'user_id': 'fixture-creator'}, operator=True)
    viewer = AuthorityConnection(authority, peer, {'user_id': 'fixture-creator',
                                                   'capabilities': ['session:read']})
    frames = []

    async def rpc(method, **params):
        return await ws.dispatch({'jsonrpc': '2.0', 'id': method, 'method': method, 'params': params})
    try:
        sid = (await rpc('session.create', request_id='fresh', source='cli'))['result']['session_id']
        await ws.close()
        attached = await viewer.dispatch({'id': 'resume', 'method': 'session.resume',
                                          'params': {'session_id': sid}})
        assert attached['result']['session_id'] == sid, attached
        refused = await viewer.dispatch({'id': 'submit', 'method': 'prompt.submit',
                                         'params': {'session_id': sid, 'input_id': 'denied', 'text': 'no'}})
        assert refused.get('error', {}).get('message') == 'permission_denied', refused
        assert not authority.db.get_messages(sid)
        ws = AuthorityConnection(authority, CapturePeer(), {'user_id': 'fixture-creator'}, operator=True)
        assert 'result' in await rpc('session.resume', session_id=sid)
        submitted = await rpc('prompt.submit', session_id=sid, input_id='first', text='hi')
        assert submitted.get('result', {}).get('status') == 'queued', submitted
        async with asyncio.timeout(30):
            while not any(f.get('params', {}).get('type') == 'message.complete' for f in frames):
                frames.append(await asyncio.to_thread(peer.frames.get, True, 25))
        await asyncio.wait_for(authority.sessions[sid].task, 30)
        events = [f['params'] for f in frames if f.get('method') == 'event']
        # The classic `hermes chat` view renders what reached it on stderr.
        import contextlib
        import io
        from hermes_cli.gateway_chat_view import GatewayChatView
        view, printed = GatewayChatView(None, {'stored_session_id': sid}), io.StringIO()
        with contextlib.redirect_stderr(printed), contextlib.redirect_stdout(io.StringIO()):
            for params in events:
                view._dispatch_event(params['type'], params.get('admission_id'), params.get('payload', {}))
        Path(os.environ['HERMES_HOME'], 'receipt.json').write_text(json.dumps({
            'events': [[p['type'], p.get('payload')] for p in events if p['type'] in (
                'status.update', 'notification.show', 'notification.clear')], 'cli_stderr': printed.getvalue()}))
    finally:
        run_agent.AIAgent.run_conversation = original
        await ws.close()
        await viewer.close()
        runner.session_store.close_all_db_handles()


def main():
    suppress = len(sys.argv) > 1 and sys.argv[1] == 'suppress'
    peer = ThreadingHTTPServer(('127.0.0.1', 0), Peer)
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{peer.server_port}/v1'
    os.environ.update(OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=url, TERMINAL_ENV='local')
    Path(os.environ['HERMES_HOME'], 'config.yaml').write_text(
        f'model:\n  default: local-control\n  provider: custom\n  base_url: {url}\n'
        'streaming:\n  enabled: false\nauxiliary:\n  title_generation:\n    enabled: false\n'
        + ('display:\n  suppress_warning_notifications: true\n' if suppress else ''))
    try:
        asyncio.run(probe())
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
