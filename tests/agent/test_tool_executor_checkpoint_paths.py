"""Local checkpoint paths and refusal of retired terminal selections."""

import json
import os
import threading
from types import SimpleNamespace

import pytest

from agent.tool_executor import _ToolCallRef, _begin_tool_execution, _ensure_file_checkpoint
from agent.turn_explainers import TurnExplainersMixin
from tools.checkpoint_manager import CheckpointManager



@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", tmp_path / "checkpoints")
    return CheckpointManager(enabled=True)


@pytest.fixture(autouse=True)
def local_terminal(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr("tools.terminal_tool._terminal_config_bridge_attempted", True)


def test_relative_file_checkpoint_uses_task_workspace(tmp_path, monkeypatch):
    """Checkpoint lookup must use the same cwd as a relative file mutation."""
    process_cwd = tmp_path / "opt" / "hermes"
    workspace_cwd = tmp_path / "opt" / "data" / "workspace"
    process_cwd.mkdir(parents=True)
    workspace_cwd.mkdir(parents=True)

    # Both directories contain content so checkpointing the wrong one would
    # still succeed and remain observable.
    (process_cwd / "pyproject.toml").write_text("[project]\nname = 'hermes'\n")
    (workspace_cwd / "pyproject.toml").write_text("[project]\nname = 'workspace'\n")
    (workspace_cwd / "existing.txt").write_text("before\n")

    monkeypatch.chdir(process_cwd)
    monkeypatch.setenv("TERMINAL_CWD", str(workspace_cwd))
    monkeypatch.setattr(
        "tools.checkpoint_manager.CHECKPOINT_BASE",
        tmp_path / "checkpoints",
    )

    manager = CheckpointManager(enabled=True)
    agent = SimpleNamespace(_checkpoint_mgr=manager)

    _ensure_file_checkpoint(
        agent,
        "write_file",
        {"path": "test_permissions2.txt"},
        "gateway-session",
    )

    assert manager.list_checkpoints(str(workspace_cwd))
    assert manager.list_checkpoints(str(process_cwd)) == []


@pytest.mark.parametrize("backend", ["docker", "ssh", "vercel_sandbox", "plugin"])
def test_retired_backend_leaves_host_store_untouched(tmp_path, monkeypatch, manager, backend):
    """Unsupported selections cannot snapshot or record host files."""
    monkeypatch.setenv("TERMINAL_ENV", backend)
    host_file = tmp_path / "workspace" / "project" / "a.txt"
    host_file.parent.mkdir(parents=True)
    host_file.write_text("host content\n", encoding="utf-8")
    path = str(host_file)
    class Agent(TurnExplainersMixin, SimpleNamespace):
        pass

    agent = Agent(
        _checkpoint_mgr=manager, _turn_failed_file_mutations={}, _turn_file_mutation_paths=set(),
        quiet_mode=True, tool_progress_callback=None, tool_start_callback=None, _touch_activity=lambda *_: None,
    )

    with pytest.raises(ValueError, match="Only 'local'"):
        _ensure_file_checkpoint(agent, "write_file", {"path": path}, "retired-task")
    ref = _ToolCallRef("terminal", {"command": f"rm -rf {host_file.parent}", "workdir": str(host_file.parent)}, "retired-task", "call-1", [])
    _begin_tool_execution(agent, ref, None)
    with pytest.raises(ValueError, match="Only 'local'"):
        TurnExplainersMixin._record_file_mutation_result(
            agent, "write_file", {"path": path, "content": "x"},
            json.dumps({"bytes_written": 1, "resolved_path": path}), False, task_id="retired-task",
        )
    assert manager.list_checkpoints(str(host_file.parent)) == []
    assert not (tmp_path / "checkpoints").exists()

    monkeypatch.setenv("TERMINAL_ENV", "local")
    _ensure_file_checkpoint(agent, "write_file", {"path": path}, "local-task")
    assert len(manager.list_checkpoints(str(host_file.parent))) == 1
    TurnExplainersMixin._record_file_mutation_result(
        agent, "write_file", {"path": path},
        json.dumps({"bytes_written": 1, "resolved_path": path}), False, task_id="local-task",
    )


@pytest.mark.asyncio
async def test_retired_selection_refuses_host_rollback_on_every_surface(tmp_path, monkeypatch, manager, capsys):
    """CLI, gateway and RPC refuse retired selections before restoring or diffing files."""
    from gateway import run as gateway_run
    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.session import SessionSource
    from hermes_cli.cli_commands_mixin import CLICommandsMixin
    from tui_gateway import server

    host_dir = tmp_path / "workspace" / "project"
    host_dir.mkdir(parents=True)
    (host_dir / "a.txt").write_text("before\n", encoding="utf-8")
    manager.ensure_checkpoint(str(host_dir), "earlier local session")
    monkeypatch.setenv("TERMINAL_ENV", "docker")  # the configured backend, as the product bridges it
    monkeypatch.setenv("TERMINAL_CWD", str(host_dir))

    def refuse(*_args, **_kwargs):
        raise AssertionError("host checkpoint operation reached from a container-backed session")

    monkeypatch.setattr(manager, "restore", refuse)
    monkeypatch.setattr(manager, "diff", refuse)
    monkeypatch.setattr(manager, "session_diff", refuse)

    cli = SimpleNamespace(
        _checkpoint_manager=lambda _lines: manager,
        _resolve_checkpoint_ref=lambda ref, cps: cps[int(ref) - 1]["hash"],
        _rollback_restore=refuse, _rollback_diff=refuse,
    )
    for command in ("/rollback 1 --all", "/rollback diff 1"):
        CLICommandsMixin._handle_rollback_command(cli, command)
        assert "docker" in "".join(capsys.readouterr())
    CLICommandsMixin._print_session_diff(cli, str(host_dir), False)
    assert "docker" in "".join(capsys.readouterr())

    runner = object.__new__(gateway_run.GatewayRunner)
    runner._checkpoint_manager = lambda source=None: manager
    source = SessionSource(platform=Platform.API_SERVER, user_id="u", chat_id="c", user_name="t", chat_type="dm")
    for text in ("/rollback 1", "/rollback 1 --all"):
        assert "docker" in await runner._handle_rollback_command(MessageEvent(text=text, source=source))
    assert "docker" in await runner._handle_diff_command(MessageEvent(text="/diff session", source=source))
    listing = await runner._handle_rollback_command(MessageEvent(text="/rollback", source=source))
    assert "docker" in listing and "earlier local session" in listing  # listing stays visible

    session = {
        "agent": SimpleNamespace(_checkpoint_mgr=manager), "cwd": str(host_dir), "running": False,
        "session_key": "container-key", "history": [], "history_lock": threading.Lock(), "history_version": 0,
    }
    monkeypatch.setitem(server._sessions, "container-sid", session)
    resp = server.handle_request(
        {"id": "1", "method": "rollback.restore", "params": {"session_id": "container-sid", "hash": "1"}}
    )
    assert resp["result"]["success"] is False and "docker" in resp["result"]["error"]

    monkeypatch.setenv("TERMINAL_ENV", "local")  # control: a local session still dispatches
    monkeypatch.setattr(manager, "restore", lambda *a, **k: {"success": True, "restored_to": "x", "reason": "r"})
    resp = server.handle_request(
        {"id": "2", "method": "rollback.restore", "params": {"session_id": "container-sid", "hash": "1"}}
    )
    assert resp["result"]["success"] is True
    assert (host_dir / "a.txt").read_text(encoding="utf-8") == "before\n"
    assert os.environ["TERMINAL_ENV"] == "local"
