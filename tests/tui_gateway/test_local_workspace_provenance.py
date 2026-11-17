"""Local working-directory and workspace provenance regressions."""

import threading

import pytest

from tui_gateway import server


@pytest.fixture
def launch_dir(tmp_path, monkeypatch):
    """The gateway's launch directory: an existing host dir nobody picked, with no configured terminal.cwd."""
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(server, "_hermes_home", home)
    for name in ("TERMINAL_CWD",):
        monkeypatch.delenv(name, raising=False)
    launch = tmp_path / "opt-data"
    launch.mkdir()
    monkeypatch.chdir(launch)
    return launch


def test_local_backend_keeps_the_launch_dir(launch_dir, monkeypatch):
    explicit, cwd = server._resolve_create_cwd({}, "tui", None)
    session = {"session_key": "local", "cwd": cwd, "explicit_cwd": explicit, "profile_home": None, "source": "tui"}

    assert server._terminal_task_cwd_with_source(session) == (str(launch_dir), "session")
    assert server._persisted_session_cwd(session) == str(launch_dir)


def test_compute_host_carries_workspace_provenance(launch_dir, monkeypatch, tmp_path):
    """An isolated turn's frame carries ``explicit_cwd``, so the compute host's rebuilt session keeps a picked
    local workspace as its terminal cwd."""
    import io
    import types

    from tui_gateway.compute_host import ComputeHost

    project = tmp_path / "project"
    project.mkdir()
    parent = {"session_key": "picked", "cwd": str(project), "explicit_cwd": True, "history": [],
              "history_lock": threading.Lock(), "profile_home": None, "source": "desktop"}
    frame = server._compute_host_turn_frame("rid", "child", parent, "hi")
    assert frame["explicit_cwd"] is True

    captured = {}

    def fake_init_session(sid, key, agent, history, **kwargs):
        captured.update(kwargs)
        monkeypatch.setitem(server._sessions, sid, {"agent": agent, "session_key": key, "cwd": kwargs.get("cwd"),
                                                    "explicit_cwd": kwargs.get("explicit_cwd", False),
                                                    "profile_home": None})

    monkeypatch.setattr(server, "_make_agent", lambda sid, key, **kw: types.SimpleNamespace(session_id=key))
    monkeypatch.setattr(server, "_transfer_db_to_agent", lambda agent, db: False)
    monkeypatch.setattr(server, "_init_session", fake_init_session)
    server._sessions.pop("child", None)
    child = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)._build_server_session(
        server, {**frame, "sid": "child", "history": []}, "child")

    assert captured["explicit_cwd"] is True
    assert server._terminal_task_cwd_with_source(child) == (str(project), "session")


def test_compute_host_minimal_record_keeps_workspace_provenance(launch_dir, monkeypatch, tmp_path):
    """When ``_init_session`` fails, the compute host's minimal record still carries the picked workspace."""
    import io
    import types

    from tui_gateway.compute_host import ComputeHost

    project = tmp_path / "project"
    project.mkdir()

    def failing_init_session(*_a, **_k):
        raise RuntimeError("side machinery unavailable")

    monkeypatch.setattr(server, "_make_agent", lambda sid, key, **kw: types.SimpleNamespace(session_id=key))
    monkeypatch.setattr(server, "_transfer_db_to_agent", lambda agent, db: False)
    monkeypatch.setattr(server, "_init_session", failing_init_session)
    server._sessions.pop("child-min", None)
    frame = {"sid": "child-min", "session_key": "picked-min", "cwd": str(project), "explicit_cwd": True,
             "history": [], "source": "desktop"}
    try:
        child = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)._build_server_session(server, frame, "child-min")
        assert child["explicit_cwd"] is True and child["source"] == "desktop"
        assert server._terminal_task_cwd_with_source(child) == (str(project), "session")
    finally:
        server._sessions.pop("child-min", None)


