"""Workspace changes update only the selected task's live local shell."""

import pytest

import tools.terminal_tool as tt


class _FakeEnv:
    def __init__(self, cwd):
        self.env_type = "local"
        self.cwd = cwd

    def execute(self, *a, **k):
        return {"output": "", "returncode": 0}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(tt, "_session_cwd", {})
    monkeypatch.setattr(tt, "_task_env_overrides", {})
    monkeypatch.setattr(tt, "_active_environments", {})
    monkeypatch.setattr(tt, "_creation_locks", {})



WIN_WS = r"C:\Users\rashi\ai_workspace"


@pytest.mark.parametrize("override", [WIN_WS, "/proj/two"])
def test_live_local_workspace_override_stays_task_scoped(override):
    env = _FakeEnv("/original")
    other = _FakeEnv("/other")
    tt._active_environments[tt._resolve_task_id("sess-abc")] = env
    tt._active_environments[tt._resolve_task_id("sess-other")] = other
    tt.register_task_env_overrides("sess-abc", {"cwd": override})
    assert env.cwd == override
    assert other.cwd == "/other"
    assert tt.get_session_cwd("sess-abc") == override


def test_local_builder_preserves_workspace(tmp_path):
    from tools.terminal_tool_backends import _create_environment
    env = _create_environment("local", cwd=str(tmp_path), timeout=5)
    try:
        assert env.env_type == "local"
        assert env.cwd == str(tmp_path)
    finally:
        env.cleanup()
