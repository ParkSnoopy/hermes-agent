"""Local file and media selectors reject retired terminal backends before I/O."""
import asyncio
import json

import pytest

from tools import file_tools, image_source, terminal_tool
from tools.file_operations import ShellFileOperations


def test_missing_cwd_reports_local_guidance():
    class Env:
        cwd = "/missing"

        def execute(self, command, cwd):
            return {"returncode": 126, "output": "cd: no such directory"}

    result = ShellFileOperations(Env())._exec("unused")
    assert result.exit_code == 126
    assert "check terminal.cwd or the session cwd" in result.cwd_error
    assert "container" not in result.cwd_error


@pytest.mark.parametrize("backend", ["docker", "ssh", "modal", "unknown"])
def test_retired_selection_refuses_file_tools_before_guards(monkeypatch, tmp_path, backend):
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": backend})
    monkeypatch.setattr(terminal_tool, "resolve_task_overrides", lambda task: {})
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *args: pytest.fail("file operations started"))
    path = str(tmp_path / "data.txt")
    calls = [
        lambda: file_tools.read_file_tool(path),
        lambda: file_tools.write_file_tool(path, "new"),
        lambda: file_tools.patch_tool(path=path, old_string="a", new_string="b"),
        lambda: file_tools.search_tool("a", path=path),
    ]
    for call in calls:
        assert "Unsupported terminal environment" in json.loads(call())["error"]
    assert not (tmp_path / "data.txt").exists()


def test_task_override_is_validated_before_media_access(monkeypatch, tmp_path):
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": "local"})
    monkeypatch.setattr(terminal_tool, "resolve_task_overrides", lambda task: {"env_type": "ssh"})
    with pytest.raises(image_source.SourceUnsafe, match="Unsupported terminal environment"):
        asyncio.run(image_source.resolve_image_source(
            str(tmp_path / "missing.png"), image_source.ResolveContext(task_id="session")))


def test_file_environment_creation_uses_session_cwd(monkeypatch, tmp_path):
    config = {"env_type": "local", "cwd": "/unused", "timeout": 8}
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: config)
    monkeypatch.setattr(terminal_tool, "resolve_task_overrides", lambda task: {})
    monkeypatch.setattr(terminal_tool, "get_session_cwd", lambda task: str(tmp_path))
    captured = {}

    def create(settings, backend, *, cwd, timeout):
        captured.update(settings=settings, backend=backend, cwd=cwd, timeout=timeout)
        return object()

    monkeypatch.setattr(terminal_tool, "_create_configured_env", create)
    backend, env = file_tools._create_terminal_env_for_file_ops("session", "session")
    assert backend == "local"
    assert env is not None
    assert captured == {"settings": config, "backend": "local", "cwd": str(tmp_path), "timeout": 8}


def test_file_environment_creation_preserves_task_cwd_override(monkeypatch, tmp_path):
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {
        "env_type": "local", "cwd": "/unused", "timeout": 8})
    monkeypatch.setattr(terminal_tool, "resolve_task_overrides", lambda task: {"cwd": str(tmp_path)})
    monkeypatch.setattr(terminal_tool, "get_session_cwd", lambda task: None)
    captured = {}
    monkeypatch.setattr(terminal_tool, "_create_configured_env",
                        lambda config, backend, **kwargs: captured.update(kwargs))
    file_tools._create_terminal_env_for_file_ops("session", "session")
    assert captured["cwd"] == str(tmp_path)


def test_local_media_uses_session_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": "local"})
    monkeypatch.setattr(terminal_tool, "resolve_task_overrides", lambda task: {})
    monkeypatch.setattr(terminal_tool, "get_session_cwd", lambda task: str(tmp_path))
    (tmp_path / "sample.svg").write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    result = asyncio.run(image_source.resolve_image_source(
        "sample.svg", image_source.ResolveContext(task_id="session")))
    assert result.mime == "image/svg+xml"
    assert result.origin == "file"
