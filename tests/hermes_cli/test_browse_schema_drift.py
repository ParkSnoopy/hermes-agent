"""Session schema migration and gateway ownership contracts."""
from __future__ import annotations
import sqlite3
from contextlib import closing
import pytest
from hermes_state import SessionDB
from hermes_state_common import SCHEMA_VERSION
from hermes_state_reconcile import reconcile_drift_for_browse
_ADDED_TABLES = ("runtime_epoch", "session_admissions", "worker_executions", "worker_receipts")
_ADDED_COLUMNS = (("sessions", "runtime_revision"), ("sessions", "runtime_generation"),
                  ("async_delegations", "owner_execution_id"))

def _pre_merge_store(path, *, version=31):
    """The shape a store kept by the release before #106742 has: built by SessionDB, then the
    tables/columns #106742 added removed again, stamped with the version that release wrote."""
    with SessionDB(db_path=path) as db:
        db.create_session("kept", source="cli")
        db.append_message("kept", role="user", content="history survives")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TRIGGER IF EXISTS sessions_runtime_ledger_delete_guard")
        for table in reversed(_ADDED_TABLES):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("DROP INDEX IF EXISTS idx_sessions_local_created_started")
        for table, column in _ADDED_COLUMNS:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.execute("UPDATE schema_version SET version = ?", (version,))
        conn.commit()
    return path


def _tables(path):
    with closing(sqlite3.connect(path)) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _profile_home(tmp_path, name="scribe"):
    home = tmp_path / "profiles" / name
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("{}\n", encoding="utf-8")
    return home


def test_unowned_lagging_store_reconciles_once_without_losing_history(tmp_path):
    path = _pre_merge_store(_profile_home(tmp_path) / "state.db")
    assert reconcile_drift_for_browse(path) is True
    assert set(_ADDED_TABLES) <= _tables(path)
    before = path.read_bytes()
    assert reconcile_drift_for_browse(path) is False
    assert path.read_bytes() == before
    with SessionDB(db_path=path, read_only=True) as db:
        assert [m["content"] for m in db.get_messages("kept")] == ["history survives"]


def test_owned_lagging_store_is_not_written_and_names_the_remedy(tmp_path):
    from gateway.runtime_ownership import ProfileOwnership
    from hermes_state_reconcile import DeclarativeDriftError

    home = _profile_home(tmp_path)
    path = _pre_merge_store(home / "state.db")
    before = path.read_bytes()
    owner = ProfileOwnership()
    owner.reserve([home])
    try:
        with pytest.raises(DeclarativeDriftError) as caught:
            reconcile_drift_for_browse(path)
    finally:
        owner.close()
    assert caught.value.owned
    assert "running gateway owns it" in str(caught.value)
    assert "gateway restart" in str(caught.value)
    assert path.read_bytes() == before
    assert not set(_ADDED_TABLES) & _tables(path)


def test_missing_history_table_is_refused_not_recreated(tmp_path):
    from hermes_state_reconcile import DeclarativeDriftError

    path = _pre_merge_store(tmp_path / "state.db")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE messages")
        conn.commit()
    before = path.read_bytes()
    with pytest.raises(DeclarativeDriftError, match="sessions recover"):
        reconcile_drift_for_browse(path)
    assert path.read_bytes() == before


def test_version_bump_migration_is_additive_and_idempotent(tmp_path):
    path = _pre_merge_store(tmp_path / "state.db")
    assert SCHEMA_VERSION > 31

    for _ in range(2):
        with SessionDB(db_path=path) as db:
            assert db._conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
            assert db._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert set(_ADDED_TABLES) <= _tables(path)
    with closing(sqlite3.connect(path)) as conn:
        for table, column in _ADDED_COLUMNS:
            assert column in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    # A browse-reconciled store (still stamped 31) also upgrades cleanly on the owner's open.
    other = _pre_merge_store(tmp_path / "other.db")
    assert reconcile_drift_for_browse(other) is True
    with SessionDB(db_path=other) as db:
        assert db._conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION


def _drop_session_column(path, column):
    with closing(sqlite3.connect(path)) as conn:
        # SQLite refuses DROP COLUMN while an index references it; a pre-column store has neither.
        conn.execute("DROP INDEX IF EXISTS idx_sessions_effective_activity")
        conn.execute(f"ALTER TABLE sessions DROP COLUMN {column}")
        conn.commit()


def _session_columns(path):
    with closing(sqlite3.connect(path)) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}


def _lagging_store_with_history(home, rows=400):
    """A real store with enough history for a multi-page ``messages`` b-tree, one column dropped."""
    path = home / "state.db"
    with SessionDB(db_path=path) as db:
        db.create_session("kept", source="cli")
        for i in range(rows):
            db.append_message("kept", role="user", content=f"history {i} " * 40)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("DROP INDEX IF EXISTS idx_sessions_effective_activity")
        conn.execute("ALTER TABLE sessions DROP COLUMN last_read_at")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return path


_PURPOSE_HOLDER = r'''
import sys, time
from pathlib import Path
from gateway.runtime_ownership import ProfileOwnership
purpose = sys.argv[3] or None
owner = ProfileOwnership()
owner.reserve([Path(sys.argv[1])], purpose=purpose)
print("held", flush=True)
time.sleep(float(sys.argv[2]))
'''


def _hold(home, seconds, purpose=""):
    import os
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", _PURPOSE_HOLDER, str(home), str(seconds), purpose],
                            env=os.environ, stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


_RAW_FLOCK_HOLDER = r'''
import fcntl, os, sys, time
from pathlib import Path
home = Path(sys.argv[1]); home.mkdir(parents=True, exist_ok=True)
fh = open(home / "gateway.lock", "a+")
fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
fh.seek(0); fh.truncate(); fh.flush()  # flock held, record not yet published (reserve()'s window)
print("held", flush=True)
time.sleep(float(sys.argv[2]))
'''


@pytest.mark.platforms("linux", "macos")
def test_gateway_boot_reserve_waits_out_a_browse_reconcile(tmp_path):
    """P3-1: a gateway booting while a browse reconcile holds gateway.lock waits for it (bounded)
    instead of exiting as though another gateway owned the profile. A real owner still refuses."""
    import time

    from gateway.run_bootstrap import _reserve_waiting_out_browse_reconcile
    from gateway.runtime_ownership import OwnershipConflict, ProfileOwnership
    from hermes_state_reconcile import BROWSE_RECONCILE_PURPOSE

    home = _profile_home(tmp_path)
    holder = _hold(home, 1.0, BROWSE_RECONCILE_PURPOSE)
    booting = ProfileOwnership()
    try:
        started = time.monotonic()
        _reserve_waiting_out_browse_reconcile(booting, [home], wait_s=10)
        assert booting.owns(home)
        assert time.monotonic() - started < 9
    finally:
        booting.close()
        holder.kill()
        holder.wait()

    gateway = _hold(home, 30)  # an unmarked holder is a runtime owner: refuse at once
    contender = ProfileOwnership()
    try:
        started = time.monotonic()
        with pytest.raises(OwnershipConflict):
            _reserve_waiting_out_browse_reconcile(contender, [home], wait_s=10)
        assert time.monotonic() - started < 2
    finally:
        contender.close()
        gateway.kill()
        gateway.wait()


@pytest.mark.platforms("linux", "macos")
def test_gateway_boot_waits_while_the_holder_has_not_published_its_record(tmp_path):
    """R2-1: reserve() takes the flock before it writes the purpose record. A boot that loses the
    flock in that window must retry (bounded), not abort as though a gateway owned the profile."""
    import os
    import subprocess
    import sys
    import time

    from gateway.run_bootstrap import _reserve_waiting_out_browse_reconcile
    from gateway.runtime_ownership import OwnershipConflict, ProfileOwnership

    home = _profile_home(tmp_path)
    holder = subprocess.Popen([sys.executable, "-c", _RAW_FLOCK_HOLDER, str(home), "1.0"],
                              env=os.environ, stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    booting = ProfileOwnership()
    try:
        assert (home / "gateway.lock").read_text() == ""
        started = time.monotonic()
        _reserve_waiting_out_browse_reconcile(booting, [home], wait_s=10)
        assert booting.owns(home)
        assert 0.5 < time.monotonic() - started < 9
    finally:
        booting.close()
        holder.kill()
        holder.wait()

    # Still bounded: an unpublished holder that never lets go is refused once the wait expires.
    holder = subprocess.Popen([sys.executable, "-c", _RAW_FLOCK_HOLDER, str(home), "30"],
                              env=os.environ, stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    contender = ProfileOwnership()
    try:
        started = time.monotonic()
        with pytest.raises(OwnershipConflict):
            _reserve_waiting_out_browse_reconcile(contender, [home], wait_s=0.6)
        assert 0.5 < time.monotonic() - started < 3
    finally:
        contender.close()
        holder.kill()
        holder.wait()


@pytest.mark.platforms("linux", "macos")
def test_boot_refuses_at_once_when_another_home_has_a_real_owner(tmp_path):
    """pastels m2: a multiplexer boot reserving profile A (owned by a standalone gateway) and profile B
    (mid-browse) must refuse at once on A, not wait out B's browse first. Homes the boot did take
    while deciding are released again (all-or-nothing)."""
    import time

    from gateway.run_bootstrap import _reserve_waiting_out_browse_reconcile
    from gateway.runtime_ownership import OwnershipConflict, ProfileOwnership
    from hermes_state_reconcile import BROWSE_RECONCILE_PURPOSE

    owned_home = _profile_home(tmp_path, "alpha")
    browsed_home = _profile_home(tmp_path, "beta")
    free_home = _profile_home(tmp_path, "gamma")
    gateway = _hold(owned_home, 30)
    browse = _hold(browsed_home, 30, BROWSE_RECONCILE_PURPOSE)
    booting = ProfileOwnership()
    try:
        started = time.monotonic()
        with pytest.raises(OwnershipConflict):
            _reserve_waiting_out_browse_reconcile(booting, [free_home, owned_home, browsed_home], wait_s=10)
        assert time.monotonic() - started < 2, "refusal waited for an unrelated browse holder"
        assert booting.homes == [] or not any(booting.owns(h) for h in (free_home, owned_home, browsed_home))
    finally:
        booting.close()
        for proc in (gateway, browse):
            proc.kill()
            proc.wait()


def test_every_raw_schema_catch_up_handles_an_old_indexed_column(tmp_path):
    """alt-glitch S1: three hand-ordered catch-ups (reconcile_state_schema, the repair snapshot and the
    browse DDL) each encoded the columns -> SCHEMA_SQL -> columns order. One apply_declared_schema
    now owns it; every raw caller catches an old store (indexed column missing) up the same way."""
    from hermes_state_schema import reconcile_state_schema

    path = _lagging_store_with_history(_profile_home(tmp_path), rows=5)
    with closing(sqlite3.connect(path)) as conn:
        reconcile_state_schema(conn)
        assert not conn.in_transaction
    assert "last_read_at" in _session_columns(path)


def _file_state(path):
    """Main file plus WAL content (an empty sidecar a reader creates is not a write)."""
    wal = path.with_name(path.name + "-wal")
    return path.read_bytes(), wal.read_bytes() if wal.exists() else b""


def test_equal_cardinality_corrupt_index_plus_drift_is_not_written(tmp_path):
    """R2-3: quick_check does not compare index keys with the table, so two equal-size indexes
    with swapped root pages pass it. Browse must still refuse as corrupt and write nothing."""
    path = _lagging_store_with_history(_profile_home(tmp_path), rows=10)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE probe_other (v TEXT)")
        conn.execute("CREATE INDEX probe_other_index ON probe_other(v)")
        conn.execute("INSERT INTO probe_other(v) VALUES ('wrong-source')")
        conn.commit()
        target = conn.execute("SELECT rootpage FROM sqlite_master WHERE name='idx_sessions_source'").fetchone()[0]
        other = conn.execute("SELECT rootpage FROM sqlite_master WHERE name='probe_other_index'").fetchone()[0]
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET rootpage=? WHERE name='idx_sessions_source'", (other,))
        conn.execute("UPDATE sqlite_master SET rootpage=? WHERE name='probe_other_index'", (target,))
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
    before = _file_state(path)

    from hermes_state_reconcile import CorruptStoreError
    with pytest.raises(CorruptStoreError):
        reconcile_drift_for_browse(path)
    assert _file_state(path) == before
    assert "last_read_at" not in _session_columns(path)


def test_failed_column_reconcile_rolls_back_every_addition(tmp_path):
    """R2-4: the shared column reconciler logs and continues on a non-lock error. A populated
    table missing a NOT NULL column cannot be caught up additively; browse must refuse without
    committing the other columns it could add (no partial upgrade without a backup)."""
    path = _lagging_store_with_history(_profile_home(tmp_path), rows=10)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO system_prompts(hash, prompt) VALUES ('abc', 'retained prompt')")
        conn.execute("ALTER TABLE system_prompts DROP COLUMN prompt")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = _file_state(path)

    from hermes_state_reconcile import DeclarativeDriftError
    with pytest.raises(DeclarativeDriftError) as caught:
        reconcile_drift_for_browse(path)
    assert "system_prompts.prompt" in str(caught.value)
    assert "nothing was changed" in str(caught.value)
    assert _file_state(path) == before
    assert "last_read_at" not in _session_columns(path)


def test_integrity_scan_runs_before_the_lock_is_taken(tmp_path, monkeypatch):
    """alt-glitch C1 / pastels m3: the full integrity scan is read-only and scales with file size.
    It must run BEFORE gateway.lock is reserved, so a booting gateway never waits on it."""
    import hermes_state_reconcile as reconcile
    from gateway.runtime_ownership import OwnershipConflict, ProfileOwnership

    home = _profile_home(tmp_path)
    path = _lagging_store_with_history(home, rows=10)
    original = reconcile._integrity_problem
    lock_free_during_scan = []

    def spy(p, budget):
        other = ProfileOwnership()  # its own open file description: conflicts with any holder
        try:
            other.reserve([home])
            lock_free_during_scan.append(True)
        except OwnershipConflict:
            lock_free_during_scan.append(False)
        finally:
            other.close()
        return original(p, budget)

    monkeypatch.setattr(reconcile, "_integrity_problem", spy)
    reconcile_drift_for_browse(path)
    assert lock_free_during_scan == [True]
    assert "last_read_at" in _session_columns(path)
