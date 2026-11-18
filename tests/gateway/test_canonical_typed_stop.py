"""Typed stop remains ordinary text for admission and busy corrections."""
import pytest

from hermes_state_runtime import list_session_admissions
from tests.gateway.test_prompt_queued_controls import _call, _connection
from tests.gateway.test_session_authority_cancel_settlement import _authority


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from gateway.config import Platform
    from gateway.session_identity import authenticated_subject
    db, authority = _authority(tmp_path, monkeypatch, platform=Platform.LOCAL)
    authority.sessions['s'].source.chat_id = 's'
    authority.sessions['s'].source.user_id = authenticated_subject({'user_id': 'human'})
    monkeypatch.setattr(authority, '_schedule', lambda ref: None)
    return db, authority


@pytest.mark.asyncio
@pytest.mark.parametrize('typed', ['stop', '  Stop! ', 'stop doing that'])
async def test_typed_stop_is_admitted_and_retry_is_idempotent(owner, typed):
    db, authority = owner
    with db:
        first = await _call(_connection(authority), 'prompt.submit', text=typed, submission_id='r1')
        retry = await _call(_connection(authority), 'prompt.submit', text=typed, submission_id='r1')
    assert first['result']['status'] == 'queued', first
    assert retry.get('result'), retry
    rows = list_session_admissions(db, session_id='s', pending_only=False)
    assert [row['request_id'] for row in rows] == ['r1']
    assert rows[0]['payload']['text'] == typed


@pytest.mark.asyncio
async def test_unsubscribed_viewer_cannot_submit(owner):
    db, authority = owner
    conn = _connection(authority)
    conn.subscriptions.pop('s')
    with db:
        reply = await _call(conn, 'prompt.submit', text='stop', submission_id='r1')
    assert reply['error']['data']['reason'] == 'permission_denied', reply


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['session.redirect', 'session.steer'])
async def test_stop_text_reaches_busy_correction(owner, method):
    db, authority = owner
    calls = []
    authority.steer_agent = lambda *args: calls.append(args) or (None, True)
    authority.check_approval_generation = lambda *args: None
    with db:
        reply = await _call(_connection(authority), method, text='stop', execution_generation=0)
    assert reply['result']['status'] == 'queued', reply
    assert calls
