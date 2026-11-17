"""Google Workspace declares credentials checked against the local profile home."""

from __future__ import annotations

from pathlib import Path


SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "skills/productivity/google-workspace/SKILL.md"
)

_EXPECTED_PATHS = {"google_token.json", "google_client_secret.json"}


def _parse_frontmatter(content: str) -> dict:
    from agent.skill_utils import parse_frontmatter

    fm, _ = parse_frontmatter(content)
    return fm


class TestGoogleWorkspaceCredentialFiles:

    def test_entries_are_available_when_files_exist(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        (hermes_home / "google_token.json").write_text("{}")
        (hermes_home / "google_client_secret.json").write_text("{}")

        from tools.skills_tool import _missing_skill_credential_files

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        content = SKILL_MD.read_text(encoding="utf-8")
        entries = _parse_frontmatter(content).get("required_credential_files", [])
        assert {entry["path"] for entry in entries} == _EXPECTED_PATHS
        assert _missing_skill_credential_files(entries) == []
        (hermes_home / "google_token.json").unlink()
        assert _missing_skill_credential_files(entries) == ["google_token.json"]

