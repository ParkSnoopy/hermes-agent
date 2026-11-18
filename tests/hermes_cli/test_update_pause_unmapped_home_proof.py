"""Linux bare-gateway discovery requires and preserves proven home scope."""

from gateway import status
from hermes_cli import gateway, update_cmd_fleet, update_cmd_posix_pause as pause, update_fleet_scope


def test_bare_discovery_keeps_proven_home_and_rejects_unknown(monkeypatch, tmp_path):
    home = tmp_path / "profile"
    argv = ["python", "-m", "hermes_cli.main", "gateway", "run"]
    monkeypatch.setattr(gateway, "_get_service_pids", lambda **kw: [])
    monkeypatch.setattr(gateway, "find_gateway_pids", lambda **kw: [101, 102])
    monkeypatch.setattr(update_cmd_fleet, "_scoped_manual_gateway_pids", lambda pids: pids)
    monkeypatch.setattr(pause, "_supervisor_markers", lambda pid: None)
    monkeypatch.setattr(gateway, "_capture_gateway_argv", lambda pid: argv)
    monkeypatch.setattr(update_fleet_scope, "gateway_pid_home", lambda pid: home if pid == 101 else None)
    monkeypatch.setattr(status, "get_process_start_time", lambda pid: 1.0)

    entries, notices = pause._discover_bare(set())
    assert entries == [{"pid": 101, "argv": argv, "home": str(home), "ct": 1.0}]
    assert len(notices) == 1 and "102" in notices[0] and "could not be proved" in notices[0]