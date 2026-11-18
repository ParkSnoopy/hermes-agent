import sqlite3

import pytest

from hermes_state import SessionDB
from hermes_state_schema import UnsupportedStateSchemaError


def test_old_schema_is_refused_without_modifying_file(tmp_path):
    path = tmp_path / 'state.db'
    conn = sqlite3.connect(path)
    conn.executescript('CREATE TABLE schema_version(version INTEGER); INSERT INTO schema_version VALUES(1);'
                       'CREATE TABLE sessions(id TEXT); INSERT INTO sessions VALUES("keep");')
    conn.close()
    before = path.read_bytes()
    with pytest.raises(UnsupportedStateSchemaError):
        SessionDB(path)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['state.db']


def test_current_schema_fresh_and_reopen(tmp_path):
    path = tmp_path / 'state.db'
    db = SessionDB(path)
    db.create_session('current', source='cli')
    db.close()
    db = SessionDB(path)
    try:
        assert db.get_session('current')['source'] == 'cli'
    finally:
        db.close()