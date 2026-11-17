"""Local terminal settings and fail-closed handling of retired selections."""

import pytest

from hermes_cli.config import apply_terminal_config_to_env


@pytest.mark.parametrize("backend", ["docker", "ssh", "vercel_sandbox", "some_plugin"])
def test_old_backend_is_preserved_for_execution_refusal(backend, monkeypatch):
    monkeypatch.setattr("hermes_cli.config.read_raw_config", dict)
    config = {"terminal": {"backend": backend, "cwd": "~/work", "ssh_host": "private"}}
    env = apply_terminal_config_to_env(env={}, config=config)
    assert env["TERMINAL_ENV"] == backend
    assert env["TERMINAL_CWD"].endswith("/work")
    assert "TERMINAL_SSH_HOST" not in env
    from tools.terminal_tool_config import require_local_environment
    with pytest.raises(ValueError, match="Only 'local' is supported"):
        require_local_environment(env["TERMINAL_ENV"])
    assert config["terminal"]["backend"] == backend


def test_local_bridge_preserves_timeout_and_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_cli.config.read_raw_config", dict)
    config = {"terminal": {"backend": "local", "cwd": str(tmp_path), "timeout": 37}}
    env = apply_terminal_config_to_env(env={}, config=config)
    assert env["TERMINAL_ENV"] == "local"
    assert env["TERMINAL_CWD"] == str(tmp_path)
    assert env["TERMINAL_TIMEOUT"] == "37"
