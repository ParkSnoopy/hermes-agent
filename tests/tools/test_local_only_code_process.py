"""Local-only execution boundaries and retained process output routing."""

import json
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("env_type", ["docker", "ssh", "modal", "vercel_sandbox", "unknown"])
def test_execute_code_rejects_retired_backend_before_kernel(monkeypatch, env_type):
    from tools import code_execution_tool, code_kernel, terminal_tool

    spawn = Mock()
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": env_type})
    monkeypatch.setattr(code_kernel, "execute_in_session_kernel", spawn)
    with pytest.raises(ValueError):
        code_execution_tool.execute_code("print(1)")
    spawn.assert_not_called()


@pytest.mark.parametrize("env_type", ["docker", "ssh", "modal", "vercel_sandbox", "unknown"])
def test_background_rejects_retired_backend_before_spawn(env_type):
    from tools.terminal_tool_background import _spawn

    registry = Mock()
    with pytest.raises(ValueError):
        _spawn(registry, env=Mock(), env_type=env_type, command="echo hi", cwd=".",
               effective_task_id="task", task_id="task", session_key="session", effective_pty=False)
    registry.spawn_local.assert_not_called()


@pytest.mark.parametrize("env_type", ["local", "docker", "ssh", "vercel_sandbox"])
def test_retired_backend_names_cannot_bypass_approval_floor(monkeypatch, env_type):
    from tools import approval

    refusal = {"approved": False, "message": "floor"}
    monkeypatch.setattr(approval, "_floor_block", lambda *args, **kwargs: refusal)
    assert approval.check_all_command_guards("rm -rf /", env_type) is refusal
    assert approval.check_dangerous_command("rm -rf /", env_type) is refusal


def test_background_output_uses_local_egress(monkeypatch):
    from tools.process_registry import transform_process_output
    from tools import terminal_tool_result

    hook = Mock(return_value="rewritten")
    monkeypatch.setattr(terminal_tool_result, "_apply_output_transform_hook", hook)
    assert transform_process_output("output", command="echo hi", returncode=0, task_id="task") == "rewritten"
    hook.assert_called_once_with("echo hi", "output", 0, "task", "local")


@pytest.mark.parametrize("use_pty", [False, True])
def test_local_background_tracks_output_and_exit(tmp_path, monkeypatch, use_pty):
    from tools.process_registry import ProcessRegistry

    registry = ProcessRegistry()
    monkeypatch.setattr(registry, "_owns_job", lambda *_: False)
    monkeypatch.setattr(registry, "_write_checkpoint", lambda *args, **kwargs: None)
    session = registry.spawn_local("printf local-ok", cwd=str(tmp_path), task_id="task", use_pty=use_pty)
    assert not session.pty_fallback
    result = registry.wait(session.id, timeout=10)
    assert result["exit_code"] == 0
    assert "local-ok" in result["output"]


def test_local_kernel_persists_state_without_starting_supervisor(monkeypatch):
    from tools import code_execution_tool, code_kernel

    monkeypatch.setattr("hermes_cli.process_identity._update_supervised_pgids", lambda *args: None)
    monkeypatch.setattr(code_execution_tool, "_load_config", lambda: {"mode": "strict", "timeout": 30})
    try:
        first = json.loads(code_execution_tool.execute_code("x = 41", task_id="test-kernel"))
        second = json.loads(code_execution_tool.execute_code("print(x + 1)", task_id="test-kernel"))
        assert first["status"] == second["status"] == "success"
        assert second["kernel"]["reused"] is True
        assert "42" in second["output"]
    finally:
        code_kernel.shutdown_all_kernels()
