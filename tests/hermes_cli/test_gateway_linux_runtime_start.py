"""Linux runtime starts preserve service and profile ownership."""
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace


def test_detached_gateway_pins_profile_and_drops_launch_policy(tmp_path, monkeypatch):
    from hermes_cli import gateway_runtime_start as start

    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    monkeypatch.setenv("INVOCATION_ID", "terminal-unit")
    local = ModuleType("tools.environments.local")
    monkeypatch.setattr(local, "served_profile_child_env",
                        lambda *, base, target_home, inherit_credentials: base, raising=False)
    policy = ModuleType("tools.environments.local_env_policy")
    monkeypatch.setattr(policy, "carry_gateway_profile_pin",
                        lambda env: env.update(HERMES_GATEWAY_PROFILE_PIN="1"), raising=False)
    monkeypatch.setitem(sys.modules, local.__name__, local)
    monkeypatch.setitem(sys.modules, policy.__name__, policy)
    captured = {}
    child = SimpleNamespace(pid=123)

    def spawn(command, **kwargs):
        captured.update(command=command, **kwargs)
        return child

    monkeypatch.setattr(subprocess, "Popen", spawn)
    assert start.spawn_unmanaged_gateway(tmp_path, deadline=time.monotonic() + 5) is child
    assert captured["env"]["HERMES_HOME"] == str(tmp_path.resolve())
    assert "HERMES_SAFE_MODE" not in captured["env"]
    assert "INVOCATION_ID" not in captured["env"]
    assert captured["start_new_session"] is True
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["command"][-3:] == ["gateway", "run", "--quiet"]
