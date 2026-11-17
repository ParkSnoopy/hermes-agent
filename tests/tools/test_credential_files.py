"""Skill-declared local credential availability and its security boundary."""

import pytest

from tools.skills_tool import _missing_skill_credential_files


@pytest.mark.parametrize("entry", [".env", "auth.json", ".anthropic_oauth.json", "webhook_subscriptions.json", "cache/bws_cache.json", "mcp-tokens/srv.json"])
def test_master_stores_are_refused(tmp_path, monkeypatch, entry):
    home = tmp_path / "home"
    path = home / entry
    path.parent.mkdir(parents=True)
    path.write_text("sensitive")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert _missing_skill_credential_files([entry]) == [entry]


def test_skill_files_containment_and_mixed_entries(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "creds").mkdir(parents=True)
    (home / "creds" / "token.json").write_text("{}")
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (home / "link").symlink_to(outside)
    monkeypatch.setenv("HERMES_HOME", str(home))
    entries = [{"path": "creds/token.json", "name": "missing"}, "../outside.json", str(outside), "link", "missing"]
    assert _missing_skill_credential_files(entries) == ["../outside.json", str(outside), "link", "missing"]


def test_guard_exception_fails_closed(tmp_path, monkeypatch, caplog):
    from agent import file_safety
    home = tmp_path / "home"
    home.mkdir()
    (home / "token.json").write_text("{}")
    monkeypatch.setenv("HERMES_HOME", str(home))
    def fail(_path):
        raise RuntimeError("guard exploded")
    monkeypatch.setattr(file_safety, "get_read_block_error", fail)
    assert _missing_skill_credential_files(["token.json"]) == ["token.json"]
    assert any(record.exc_info for record in caplog.records)


def test_unavailable_guard_fails_closed(tmp_path, monkeypatch, caplog):
    from agent import file_safety
    home = tmp_path / "home"
    home.mkdir()
    (home / "token.json").write_text("{}")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(file_safety, "get_read_block_error", None)
    assert _missing_skill_credential_files(["token.json"]) == ["token.json"]
    assert any(record.exc_info for record in caplog.records)
