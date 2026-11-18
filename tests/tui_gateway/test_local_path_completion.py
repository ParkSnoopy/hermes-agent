"""Local path completion uses the session workspace, not a terminal subprocess."""

from tui_gateway import server


def test_path_completion_lists_session_workspace(tmp_path):
    (tmp_path / "notes.txt").write_text("notes")
    (tmp_path / "north").mkdir()
    items = server._path_completion_items("no", str(tmp_path))
    assert {item["text"] for item in items} == {"notes.txt", "north/"}


def test_unsupported_profile_is_not_classified_local(tmp_path):
    (tmp_path / "config.yaml").write_text("terminal:\n  backend: ssh\n")
    assert not server._session_is_local_backend({"profile_home": str(tmp_path)})
