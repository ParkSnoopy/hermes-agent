"""``hermes dump`` gateway line for a supervised gateway with no scannable PID (#125390)."""

from __future__ import annotations

from hermes_cli.dump import _gateway_status
from hermes_cli.gateway import GatewayRuntimeSnapshot


def test_supervised_gateway_without_pid_is_running_not_unknown(monkeypatch):
    """A running service with no scanned PID is not reported as unknown."""
    snapshot = GatewayRuntimeSnapshot(
        manager="systemd (user)", service_installed=True, service_running=True, gateway_pids=())
    monkeypatch.setattr("hermes_cli.gateway.get_gateway_runtime_snapshot", lambda: snapshot)
    assert _gateway_status() == "running (systemd (user))"
