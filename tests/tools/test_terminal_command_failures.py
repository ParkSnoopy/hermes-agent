"""Local command failures remain ordinary terminal results."""

import json

import pytest


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Isolated HERMES_HOME + a clean environment cache for terminal_tool."""
    import tools.terminal_tool as tt

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    # The one-shot config bridge would overwrite our TERMINAL_* test vars
    # from the developer's real config.yaml; mark it as already attempted.
    monkeypatch.setattr(tt, "_terminal_config_bridge_attempted", True)

    def _clear():
        with tt._env_lock:
            tt._active_environments.clear()
            tt._last_activity.clear()

    _clear()
    yield tt
    _clear()





class TestNonInfrastructureFailuresUntouched:
    def test_nonzero_exit_is_not_degraded(self, isolated_env, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "local")
        r = json.loads(isolated_env.terminal_tool("exit 3", task_id="t-degraded-exit3"))
        assert r["exit_code"] == 3
        assert r.get("status") != "degraded"

    def test_command_not_found_is_not_degraded(self, isolated_env, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "local")
        r = json.loads(isolated_env.terminal_tool(
            "definitely_not_a_real_command_zzz_42", task_id="t-degraded-notfound"))
        assert r["exit_code"] != 0
        assert r.get("status") != "degraded"

