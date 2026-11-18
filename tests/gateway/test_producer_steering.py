"""Local producer transfers retain their notice if the receiving claim is lost."""
import json
import sqlite3

import pytest

from gateway.session_automation import _notice_producer
from hermes_state_runtime import settle_steer_transfers


def _row(kind, *, principal='automation:route'):
    identity = json.dumps([kind, 'process', 'event'], separators=(',', ':'))
    return {'admission_id': 'notice', 'principal_id': principal, 'request_id': identity,
            'payload': {'text': 'result', 'local_automation_v1': {
                'identity': identity, 'route': 'route'}}}


@pytest.mark.parametrize('kind', ['completion', 'async_delegation', 'watch_match', 'watch_disabled'])
def test_notice_transfer_identity_and_failure_recovery(monkeypatch, kind):
    import hermes_state_runtime as runtime
    row = _row(kind)
    assert _notice_producer(row)
    assert not _notice_producer(_row(kind, principal='human'))
    monkeypatch.setattr(runtime, '_admission', lambda conn, admission_id: row)
    monkeypatch.setattr(runtime, '_row', lambda value: value)
    conn = sqlite3.connect(':memory:')
    try:
        conn.execute('CREATE TABLE session_admissions (admission_id TEXT, status TEXT, outcome TEXT, owner_epoch INTEGER, generation INTEGER)')
        for ending, expected in [('completed', ('terminal', 'steered')), ('interrupted', ('queued', None)),
                                 ('unknown', ('queued', None))]:
            conn.execute('DELETE FROM session_admissions')
            conn.execute("INSERT INTO session_admissions VALUES ('notice', 'terminal', 'steering', 1, 2)")
            settle_steer_transfers(conn, epoch=1, generation=2, transfers={'notice': True}, outcome=ending)
            assert tuple(conn.execute('SELECT status,outcome FROM session_admissions').fetchone()) == expected
    finally:
        conn.close()
