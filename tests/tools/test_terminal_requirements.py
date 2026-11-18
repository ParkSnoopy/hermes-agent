"""Only local terminal settings may enable or create an execution environment."""

import json
from unittest.mock import Mock

import pytest

from tools import terminal_tool as terminal
from tools import terminal_tool_backends as backends
from tools.terminal_scope import install_and_reset_profile_terminal_scope


@pytest.fixture(autouse=True)
def clean_terminal(monkeypatch):
    monkeypatch.setattr(terminal, "_terminal_config_bridge_attempted", True)
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(backends, "_last_unavailable_reason", None)


def test_local_terminal_requirements():
    assert terminal.check_terminal_requirements()
    assert terminal.terminal_backend_unavailable_reason() is None


@pytest.mark.parametrize("backend", ["docker", "ssh", "vercel_sandbox", "modal", "daytona", "managed_modal", "singularity", "plugin-sandbox", "unknown", ""])
def test_nonlocal_selection_never_constructs_or_executes_on_host(monkeypatch, backend):
    factory = Mock()
    monkeypatch.setattr(backends, "LocalEnvironment", factory)
    monkeypatch.setenv("TERMINAL_ENV", backend)
    assert not terminal.check_terminal_requirements()
    assert "Only 'local'" in terminal.terminal_backend_unavailable_reason()
    result = json.loads(terminal.terminal_tool("echo must-not-run", force=True))
    assert result["exit_code"] == -1
    assert "Only 'local'" in result["error"]
    with pytest.raises(ValueError, match="Only 'local'"):
        backends._create_environment(backend, "/workspace", 10)
    factory.assert_not_called()


def test_local_factory_preserves_cwd_timeout_and_persistence(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(backends, "LocalEnvironment", factory)
    assert backends._create_environment("local", "/work", 42, {"persistent": False}) is factory.return_value
    factory.assert_called_once_with(cwd="/work", timeout=42, persistent=False)


def test_task_override_refuses_nonlocal_without_replacing_workspace(monkeypatch):
    monkeypatch.setattr(terminal, "_task_env_overrides", {})
    monkeypatch.setattr(terminal, "_session_cwd", {})
    terminal.register_task_env_overrides("task", {"cwd": "/original"})
    with pytest.raises(ValueError, match="Only 'local'"):
        terminal.register_task_env_overrides("task", {"env_type": "ssh", "cwd": "/other"})
    assert terminal.resolve_task_overrides("task")["cwd"] == "/original"
    assert terminal.get_session_cwd("task") == "/original"


def test_retired_selector_in_turn_scope_cannot_borrow_local_process(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(backends, "LocalEnvironment", factory)
    with install_and_reset_profile_terminal_scope(None, prepared={"TERMINAL_ENV": "docker"}):
        result = json.loads(terminal.terminal_tool("echo must-not-run"))
    assert result["exit_code"] == -1
    assert "Only 'local'" in result["error"]
    factory.assert_not_called()


def test_secondary_profile_lifecycle_never_borrows_or_closes_launch_shell(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools import terminal_tool_lifecycle as lifecycle

    launch_shell = Mock()
    secondary_shell = Mock()
    monkeypatch.setattr(terminal, "_active_environments", {"shared-task": launch_shell})
    monkeypatch.setattr(terminal, "_last_activity", {})
    monkeypatch.setattr(terminal, "_creation_locks", {})
    monkeypatch.setattr(lifecycle, "_clear_file_ops_cache", lambda _: None)
    token = set_hermes_home_override(str(tmp_path / "secondary"))
    try:
        assert lifecycle.get_active_env("shared-task") is None
        lifecycle._evict_environment_for_task("shared-task")
        launch_shell.cleanup.assert_not_called()
        key = terminal._resolve_task_id("shared-task")
        terminal._active_environments[key] = secondary_shell
        lifecycle.cleanup_vm("shared-task")
        assert key not in terminal._active_environments
        secondary_shell.cleanup.assert_called_once_with()
        assert terminal._active_environments["shared-task"] is launch_shell
        launch_shell.cleanup.assert_not_called()
    finally:
        reset_hermes_home_override(token)
