"""Retired integrations cannot execute or adopt legacy state as local chat."""
import sqlite3

import pytest

from hermes_state import SessionDB
from hermes_state_local_migration import legacy_history_row
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch, register_worker_execution


@pytest.mark.parametrize('source', ['kanban', 'telegram', 'discord', 'google_chat', 'photon',
                                   'wecom_callback', 'telegram-platform', 'photon-platform'])
def test_retired_sources_are_not_adopted_as_local_history(source):
    assert not legacy_history_row({'source': source})


@pytest.mark.parametrize('kind', ['cron', 'child', 'compute'])
def test_supported_worker_kinds_register(tmp_path, kind):
    db = SessionDB(db_path=tmp_path / 'state.db')
    try:
        db.create_session('s', source='cli')
        epoch = begin_runtime_epoch(db, instance_id='owner')
        result = register_worker_execution(db, epoch=epoch, execution_id='w', session_id='s',
                                           generation=0, kind=kind, adoption_secret='secret')
        assert result['kind'] == kind
    finally:
        db.close()


def test_retired_worker_kind_rejected_at_runtime_and_schema(tmp_path):
    db = SessionDB(db_path=tmp_path / 'state.db')
    try:
        db.create_session('s', source='cli')
        epoch = begin_runtime_epoch(db, instance_id='owner')
        with pytest.raises(RuntimeStoreError, match='invalid_params'):
            register_worker_execution(db, epoch=epoch, execution_id='w', session_id='s',
                                      generation=0, kind='kanban', adoption_secret='secret')
        with pytest.raises(sqlite3.IntegrityError):
            db._conn.execute("INSERT INTO worker_executions "
                             "(execution_id,session_id,kind,owner_epoch,generation,status,adoption_digest) "
                             "VALUES ('w','s','kanban',?,0,'registered','x')", (epoch,))
    finally:
        db.close()


def test_existing_retired_worker_ledger_is_preserved(tmp_path):
    path = tmp_path / 'state.db'
    db = SessionDB(db_path=path)
    db.create_session('s', source='kanban')
    db.close()
    conn = sqlite3.connect(path)
    conn.execute('DROP TABLE worker_executions')
    conn.execute("""CREATE TABLE worker_executions (
        execution_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
        kind TEXT NOT NULL CHECK (kind IN ('cron','child','compute','kanban')),
        owner_epoch INTEGER NOT NULL, generation INTEGER NOT NULL,
        status TEXT NOT NULL, adoption_digest TEXT NOT NULL,
        last_sequence INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("INSERT INTO worker_executions VALUES ('old','s','kanban',1,0,'terminal','x',0)")
    conn.commit()
    conn.close()
    db = SessionDB(db_path=path)
    try:
        assert tuple(db._read_one("SELECT execution_id,kind FROM worker_executions")) == ('old', 'kanban')
        epoch = begin_runtime_epoch(db, instance_id='owner')
        with pytest.raises(RuntimeStoreError, match='invalid_params'):
            register_worker_execution(db, epoch=epoch, execution_id='new', session_id='s',
                                      generation=0, kind='kanban', adoption_secret='secret')
    finally:
        db.close()