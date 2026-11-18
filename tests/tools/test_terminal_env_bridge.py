"""Terminal configuration bridging must preserve explicit local policy and fail closed."""

import os

import pytest

from tools import terminal_tool
from hermes_constants import get_hermes_home


@pytest.fixture(autouse=True)
def reset_bridge(monkeypatch):
    monkeypatch.setattr(terminal_tool, "_terminal_config_bridge_attempted", False)
    for key in ("TERMINAL_ENV", "TERMINAL_CWD", "TERMINAL_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)


def write_config(text):
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(text)


def test_local_settings_bridge_from_config(tmp_path):
    write_config(f"terminal:\n  backend: local\n  timeout: 77\n  cwd: {tmp_path}\n")
    config = terminal_tool._get_env_config()
    assert config["env_type"] == "local"
    assert config["timeout"] == 77
    assert config["cwd"] == str(tmp_path)
    assert os.environ["TERMINAL_ENV"] == "local"


def test_explicit_config_overrides_stale_environment(monkeypatch):
    write_config("terminal:\n  backend: local\n  timeout: 77\n")
    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    monkeypatch.setenv("TERMINAL_TIMEOUT", "88")
    assert terminal_tool._get_env_config()["timeout"] == 77
    assert os.environ["TERMINAL_ENV"] == "local"


def test_omitted_settings_keep_explicit_environment(monkeypatch):
    write_config("terminal:\n  backend: local\n")
    monkeypatch.setenv("TERMINAL_TIMEOUT", "88")
    assert terminal_tool._get_env_config()["timeout"] == 88


@pytest.mark.parametrize("backend", ["docker", "ssh", "vercel_sandbox", "plugin-sandbox"])
def test_retired_config_does_not_silently_run_locally(monkeypatch, backend):
    write_config(f"terminal:\n  backend: {backend}\n")
    monkeypatch.setenv("TERMINAL_ENV", "local")
    for _ in range(2):
        with pytest.raises(ValueError, match="Only 'local'"):
            terminal_tool._get_env_config()
    assert not terminal_tool._terminal_config_bridge_attempted


def test_retired_environment_without_terminal_config_is_refused(monkeypatch):
    write_config("{}\n")
    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    with pytest.raises(ValueError, match="Only 'local'"):
        terminal_tool._get_env_config()


def test_unresolved_config_does_not_default_to_host(monkeypatch):
    from hermes_cli import config
    monkeypatch.setattr(config, "read_raw_config", lambda: (_ for _ in ()).throw(RuntimeError("unreadable")))
    with pytest.raises(RuntimeError, match="unreadable"):
        terminal_tool._get_env_config()


def test_secondary_home_does_not_latch_ambient_environment(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    launch = tmp_path / "launch"
    secondary = tmp_path / "secondary"
    launch.mkdir()
    secondary.mkdir()
    (launch / "config.yaml").write_text("terminal:\n  backend: local\n")
    (secondary / "config.yaml").write_text("terminal:\n  backend: ssh\n")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    token = set_hermes_home_override(str(secondary))
    try:
        terminal_tool._ensure_terminal_env_bridged()
    finally:
        reset_hermes_home_override(token)
    assert "TERMINAL_ENV" not in os.environ
    assert not terminal_tool._terminal_config_bridge_attempted
    assert terminal_tool._get_env_config()["env_type"] == "local"
