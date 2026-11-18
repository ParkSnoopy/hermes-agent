"""Repair connections preserve autocommit and existing session data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes_state import repair_state_db_schema
from hermes_state_repair import _connect_repair_durable


@pytest.fixture(autouse=True)
def _moa_caches_isolated():
    pass


@pytest.fixture(autouse=True)
def _isolate_computer_use_approval_state():
    pass


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO messages (body) VALUES ('seed')")
    conn.commit()
    conn.close()
    return db


# ── Repair-path write durability ────────────────────────────────────────


def test_connect_repair_durable_is_autocommit(tmp_path: Path) -> None:
    """Must preserve isolation_level=None — repair runs DDL and VACUUM."""
    db = _make_db(tmp_path)
    conn = _connect_repair_durable(db)
    try:
        assert conn.isolation_level is None
        # VACUUM is only legal outside an implicit transaction.
        conn.execute("VACUUM")
    finally:
        conn.close()




def test_repair_still_works_through_durable_connection(tmp_path: Path) -> None:
    """Routing every strategy through the helper must not break the path.

    The helper is entered once per strategy, so a plumbing fault (recursion,
    a leaked connection, a refused pragma) surfaces as an exception rather
    than a report. Whether this fixture's minimal schema is *repairable* is
    beside the point — the assertion is that the path runs to completion.
    """
    db = _make_db(tmp_path)
    report = repair_state_db_schema(db, backup=False)
    assert isinstance(report, dict)
    assert set(report) >= {"repaired", "strategy", "backup_path"}
    # The file must still open afterwards — repair may fail, but it must not
    # leave the database less usable than it found it.
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    finally:
        conn.close()
