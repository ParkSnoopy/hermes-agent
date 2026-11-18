"""A restart watcher carrying future gateway argv is not itself a gateway."""

from gateway.status import (
    _gateway_command_subcommand,
    looks_like_gateway_command_line,
    looks_like_gateway_runtime_command_line,
)
from hermes_cli import gateway as gateway_mod
from hermes_cli.process_identity import _hermes_holder_subcommand


def test_spawned_restart_watcher_is_not_identified_as_a_gateway(monkeypatch):
    captured = []
    monkeypatch.setattr(gateway_mod.subprocess, "Popen", lambda argv, **kw: captured.append(argv))
    monkeypatch.setattr(gateway_mod, "_host_gateway_watcher_env", dict)
    assert gateway_mod.launch_detached_gateway_restart_by_cmdline(
        14980, ["python", "-m", "hermes_cli.main", "gateway", "run"]
    )
    cmdline = " ".join(captured[0])
    assert "hermes_cli.main gateway run" in cmdline
    assert _gateway_command_subcommand(cmdline) is None
    assert looks_like_gateway_command_line(cmdline) is False
    assert looks_like_gateway_runtime_command_line(cmdline) is False
    assert _hermes_holder_subcommand(cmdline) is None