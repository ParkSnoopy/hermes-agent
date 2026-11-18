"""Local terminal policy isolation for multiplexed profiles."""

import os

import pytest

from tools.terminal_scope import (
    TerminalPolicyRefusal, TerminalPolicyUnavailable, get_terminal_scope,
    install_profile_terminal_scope, reset_terminal_scope, set_terminal_scope, terminal_env,
)


@pytest.fixture(autouse=True)
def _launch_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", "/home/launch-user/private")
    monkeypatch.setenv("TERMINAL_TIMEOUT", "999")
    monkeypatch.setenv("TERMINAL_LOCAL_PERSISTENT", "false")
    monkeypatch.setattr("agent.secret_scope.build_profile_secret_scope", lambda _h: {})
    monkeypatch.setattr("hermes_cli.env_loader.hydrate_profile_secret_sources", lambda _h: None)
    import tools.terminal_tool as tt
    monkeypatch.setattr(tt, "_terminal_config_bridge_attempted", True)


def _profile(tmp_path, name, config_yaml="", dotenv=""):
    home = tmp_path / "profiles" / name
    home.mkdir(parents=True)
    if config_yaml:
        (home / "config.yaml").write_text(config_yaml, encoding="utf-8")
    if dotenv:
        (home / ".env").write_text(dotenv, encoding="utf-8")
    return home


def test_no_scope_reads_launch_policy():
    assert terminal_env("TERMINAL_TIMEOUT") == "999"
    assert terminal_env("TERMINAL_CWD") == "/home/launch-user/private"


def test_scoped_read_never_falls_through_to_process_env():
    token = set_terminal_scope({"TERMINAL_ENV": "local"})
    try:
        assert terminal_env("TERMINAL_TIMEOUT", "180") == "180"
        assert terminal_env("TERMINAL_LOCAL_PERSISTENT", "true") == "true"
        assert os.environ["TERMINAL_TIMEOUT"] == "999"
    finally:
        reset_terminal_scope(token)


@pytest.mark.parametrize("config_yaml,dotenv", [
    ("terminal:\n  backend: local\n  cwd: {cwd}\n  timeout: 37\n", ""),
    ("", "TERMINAL_ENV=local\nTERMINAL_CWD={cwd}\nTERMINAL_TIMEOUT=37\n"),
])
def test_routed_profile_owns_cwd_timeout_and_task_identity(tmp_path, config_yaml, dotenv):
    import gateway.run as gw
    import tools.terminal_tool as tt
    from agent import runtime_cwd
    from tools import file_tools_paths

    cwd = tmp_path / "work"
    cwd.mkdir()
    home = _profile(tmp_path, "bee", config_yaml.format(cwd=cwd), dotenv.format(cwd=cwd))
    with gw._profile_runtime_scope(home):
        config = tt._get_env_config()
        assert config["env_type"] == "local"
        assert config["cwd"] == str(cwd)
        assert config["timeout"] == 37
        assert config["local_persistent"] is True
        assert tt._resolve_task_id("one") != tt._resolve_task_id("two")
        assert file_tools_paths._configured_terminal_cwd() == str(cwd)
        assert runtime_cwd.resolve_agent_cwd() == cwd
    assert get_terminal_scope() is None
    assert os.environ["TERMINAL_TIMEOUT"] == "999"


def test_profile_omitting_keys_gets_defaults_not_launch_values(tmp_path):
    import gateway.run as gw
    import tools.terminal_tool as tt
    home = _profile(tmp_path, "bee", "terminal:\n  backend: local\n")
    with gw._profile_runtime_scope(home):
        config = tt._get_env_config()
        assert config["timeout"] == 180
        assert config["local_persistent"] is True
        assert config["cwd"] != "/home/launch-user/private"
    assert os.environ["TERMINAL_TIMEOUT"] == "999"


def test_malformed_profile_config_refuses_execution(tmp_path):
    from tools.terminal_tool import _get_env_config
    home = _profile(tmp_path, "broken", "terminal: [unclosed\n")
    token = install_profile_terminal_scope(home)
    try:
        assert isinstance(get_terminal_scope(), TerminalPolicyRefusal)
        with pytest.raises(TerminalPolicyUnavailable):
            _get_env_config()
    finally:
        reset_terminal_scope(token)


@pytest.mark.parametrize("backend", ["ssh", "docker", "vercel_sandbox", "plugin"])
def test_retired_profile_backend_is_rejected_not_host_fallback(tmp_path, backend):
    from tools.terminal_tool import _get_env_config
    home = _profile(tmp_path, "retired", f"terminal:\n  backend: {backend}\n")
    token = install_profile_terminal_scope(home)
    try:
        with pytest.raises((ValueError, TerminalPolicyUnavailable)):
            _get_env_config()
    finally:
        reset_terminal_scope(token)


@pytest.mark.parametrize("backend", ["ssh", "docker", "vercel_sandbox", "plugin"])
def test_retired_dotenv_backend_is_rejected(tmp_path, backend):
    from tools.terminal_tool import _get_env_config
    home = _profile(tmp_path, "retired-env", dotenv=f"TERMINAL_ENV={backend}\n")
    token = install_profile_terminal_scope(home)
    try:
        with pytest.raises((ValueError, TerminalPolicyUnavailable)):
            _get_env_config()
    finally:
        reset_terminal_scope(token)


def test_retired_env_keys_are_not_forwarded(tmp_path):
    home = _profile(tmp_path, "old-env", dotenv="TERMINAL_ENV=local\nTERMINAL_SSH_HOST=example.invalid\n")
    token = install_profile_terminal_scope(home, env_overlay={"TERMINAL_DOCKER_IMAGE": "old"})
    try:
        scope = get_terminal_scope()
        assert scope is not None
        assert "TERMINAL_SSH_HOST" not in scope
        assert "TERMINAL_DOCKER_IMAGE" not in scope
    finally:
        reset_terminal_scope(token)


def test_gateway_runtime_scope_resets_on_error(tmp_path):
    import gateway.run as gw
    home = _profile(tmp_path, "qa", "terminal:\n  backend: local\n  timeout: 61\n")
    with pytest.raises(RuntimeError):
        with gw._profile_runtime_scope(home):
            assert terminal_env("TERMINAL_TIMEOUT") == "61"
            raise RuntimeError("turn blew up")
    assert get_terminal_scope() is None


def test_tui_and_cron_boundaries_bind_and_reset(tmp_path):
    from tui_gateway import server
    from tools.terminal_scope import install_and_reset_profile_terminal_scope
    home = _profile(tmp_path, "dash", "terminal:\n  backend: local\n  timeout: 61\n")
    with server._session_profile_runtime_scope({"profile_home": str(home)}):
        assert terminal_env("TERMINAL_TIMEOUT") == "61"
    assert get_terminal_scope() is None
    with install_and_reset_profile_terminal_scope(home):
        assert terminal_env("TERMINAL_TIMEOUT") == "61"
    assert get_terminal_scope() is None


def test_launch_scope_does_not_inherit_another_profiles_values(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("terminal:\n  backend: local\n  timeout: 41\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    token = install_profile_terminal_scope(home)
    try:
        assert terminal_env("TERMINAL_TIMEOUT") == "41"
        assert os.environ["TERMINAL_TIMEOUT"] == "999"
    finally:
        reset_terminal_scope(token)
    assert get_terminal_scope() is None
