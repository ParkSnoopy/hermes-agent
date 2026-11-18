"""Legacy session owner backfill preserves existing owners."""

from hermes_state import SessionDB


def test_backfill_stamps_only_legacy_rows_and_is_idempotent(tmp_path):
    with SessionDB(db_path=tmp_path / "state.db") as db:
        for sid, owner in [("null", None), ("blank", None), ("owned", "researcher")]:
            db.create_session(sid, source="cli", profile_name=owner)
            db.append_message(sid, role="user", content=f"hello from {sid}")
        db._conn.execute("UPDATE sessions SET profile_name = NULL WHERE id = 'null'")
        db._conn.execute("UPDATE sessions SET profile_name = '  ' WHERE id = 'blank'")
        db._conn.commit()
        assert db.backfill_null_session_profiles("default") == 2
        assert db.backfill_null_session_profiles("default") == 0
        owners = {row[0]: row[1] for row in db._conn.execute("SELECT id, profile_name FROM sessions")}
        assert owners == {"null": "default", "blank": "default", "owned": "researcher"}
        assert db.get_messages("null")[0]["content"] == "hello from null"


def test_backfill_rejects_empty_owner(tmp_path):
    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.create_session("legacy", source="cli")
        db._conn.execute("UPDATE sessions SET profile_name = NULL WHERE id = 'legacy'")
        db._conn.commit()
        assert db.backfill_null_session_profiles("  ") == 0
        assert db._conn.execute("SELECT profile_name FROM sessions WHERE id = 'legacy'").fetchone()[0] is None
