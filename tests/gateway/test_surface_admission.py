"""HUD surface fields survive admission and remain scoped to one turn."""
import asyncio
from contextlib import closing
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from tests.gateway.fixtures.local_recovery_probe import Model, child_env, daemon, rpc, websocket


async def _authority(tmp_path, monkeypatch, answer):
    from gateway.config import GatewayConfig, Platform
    from gateway.session import SessionSource, SessionStore
    from gateway.session_authority import LiveSession, initialize_session_authority

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(_session_db=store._db, session_store=store, _draining=False,
                             _handle_message=answer, _adapter_for_source=lambda source: None)
    authority = await initialize_session_authority(runner, profile_id='p', instance_id='owner')
    store._db.create_session('s', source='telegram')
    authority.sessions['s'] = LiveSession(SessionSource(platform=Platform.TELEGRAM, chat_id='c'), 's')
    return authority


@pytest.mark.asyncio
async def test_surface_fields_are_admitted_and_scoped_to_their_own_turn(tmp_path, monkeypatch):
    from gateway.session_controls import AuthorityConnection
    from gateway.session_surface import surface_turn_note
    from hermes_state_runtime import list_session_admissions

    seen = []
    async def answer(event):
        seen.append((event.text, surface_turn_note(SimpleNamespace(valid_tool_names={'read_window_below'}))))
        return 'ok'
    authority = await _authority(tmp_path, monkeypatch, answer)
    connection = AuthorityConnection(authority, object(), {'user_id': 'owner'})
    try:
        await connection.dispatch({'id': 1, 'method': 'session.resume', 'params': {'session_id': 's'}})
        for name, extra in (('hud', {'surface': 'hud'}), ('typed', {})):
            reply = await connection.dispatch({'id': 2, 'method': 'prompt.submit', 'params': {
                'session_id': 's', 'submission_id': name, 'text': name, **extra}})
            assert reply['result']['status'] == 'queued', reply
            await authority.sessions['s'].task
        for name, extra in (('voice', {'surface': 'voice-live'}),
                            ('context', {'voice_context': 'User: smuggled'}),
                            ('turn', {'voice_turn': True}), ('interrupted', {'interrupted': True})):
            rejected = await connection.dispatch({'id': 3, 'method': 'prompt.submit', 'params': {
                'session_id': 's', 'submission_id': name, 'text': 'x', **extra}})
            assert rejected['error']['message'] == 'invalid_params', rejected
    finally:
        await connection.close()
    rows = {row['request_id']: row for row in list_session_admissions(authority.db, session_id='s', pending_only=False)}
    assert rows['hud']['payload']['surface_v1'] == {'surface': 'hud'}
    assert 'surface_v1' not in rows['typed']['payload']
    assert not ({'voice', 'context', 'turn', 'interrupted'} & rows.keys())
    assert seen[0][0] == 'hud' and seen[0][1]
    assert seen[1] == ('typed', '')


def _admissions(home):
    with closing(sqlite3.connect(f'file:{home / "state.db"}?mode=ro', uri=True)) as db:
        return dict(db.execute('SELECT request_id, status FROM session_admissions'))


def _wait_for(predicate):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    pytest.fail('Timed out waiting for the admitted turn')


def test_real_daemon_accepts_hud_submits_and_preserves_the_model_prefix(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    home, user = tmp_path / 'state', tmp_path / 'user'
    home.mkdir(mode=0o700)
    user.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.setenv('USERPROFILE', str(user))
    model = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    model.requests = []
    model.blocked, model.release = threading.Event(), threading.Event()
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{model.server_port}/v1'
    (home / 'config.yaml').write_text(json.dumps({
        'gateway': {'multiplex_profiles': False},
        'model': {'provider': 'custom', 'default': 'local-wire-stub', 'base_url': base},
        'auxiliary': {'title_generation': {'enabled': False}},
        'platform_toolsets': {'gui': []}, 'terminal': {'cwd': str(home)},
    }))
    env = child_env() | dict(HOME=str(user), USERPROFILE=str(user), HERMES_HOME=str(home),
        PYTHONPATH=str(root), OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=base, PYTHONUNBUFFERED='1')

    async def drive(desc):
        async with websocket(home, desc) as ws:
            created = await rpc(ws, 'session.create', request_id='surface-target', source='gui', cwd=str(home), toolsets=[])
            assert 'result' in created, created
            sid = created['result']['session_id']
            replies = {}
            for name, extra in (('window', {}), ('hud', {'surface': 'hud'})):
                replies[name] = await rpc(ws, 'prompt.submit', session_id=sid, submission_id=name,
                                          text=name.upper() + '_TURN', **extra)
                await asyncio.to_thread(_wait_for, lambda: _admissions(home).get(name) == 'terminal')
            return replies

    try:
        with daemon(root, home, env, barrier=False) as (_proc, desc):
            replies = asyncio.run(drive(desc))
    finally:
        model.release.set()
        model.shutdown()
        model.server_close()
        thread.join(timeout=5)

    assert all(reply.get('result', {}).get('status') == 'queued' for reply in replies.values()), replies
    users = {}
    for request in model.requests:
        content = next(m['content'] for m in reversed(request['messages']) if m['role'] == 'user')
        text = content if isinstance(content, str) else ''.join(p.get('text', '') for p in content)
        for name in ('window', 'hud'):
            if name.upper() + '_TURN' in text:
                users[name] = text
    assert set(users) == {'window', 'hud'}, users
    assert 'HUD mode' not in users['hud'] and 'HUD mode' not in users['window']
    assert all(request['messages'][0]['role'] == 'system' for request in model.requests)
    assert len({request['messages'][0]['content'] for request in model.requests}) == 1
