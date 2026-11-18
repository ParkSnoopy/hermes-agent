"""Runtime identity contract for TUI gateway machine protocol fields."""

from hermes_cli.version_info import VersionInfo
from tui_gateway import server


def test_session_info_advertises_base_version_and_full_commit(monkeypatch):
    commit = "abcdef0" + "1" * 33
    monkeypatch.setattr(
        "hermes_cli.version_info.get_version_info",
        lambda: VersionInfo("1.2.3", "1.2.3+4.gabcdef0", 4, commit, None, "git"),
    )

    info = server._session_info(None, {})

    assert info["version"] == "1.2.3"
    assert info["version_commit"] == commit
