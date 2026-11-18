"""The updater hands proven-stale gateway survivors to the restart path."""

import io
import contextlib
import os
import signal
import subprocess
import sys


import pytest


@pytest.fixture
def gateway_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    for name in list(sys.modules):
        if name.split(".")[0] in ("gateway", "hermes_cli", "hermes_constants"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    return home / ".hermes"








@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="POSIX drain-first restart signal")
def test_update_signals_proven_stale_gateway_survivor(gateway_home, tmp_path):
    """A `stale` fleet-matrix row gets SIGUSR1 (request_restart); a `current` row is untouched."""
    from hermes_cli.update_cmd_fleet import _GatewayRestartOutcome
    from hermes_cli.update_cmd_stale_survivors import signal_stale_fleet_survivors

    marker = tmp_path / "got_sigusr1"
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import signal, sys, time\n"
         f"signal.signal(signal.SIGUSR1, lambda *_: (open({str(marker)!r}, 'w').write('x'), sys.exit(75)))\n"
         "print('ready', flush=True)\n"
         "time.sleep(120)"],
        stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        restart = _GatewayRestartOutcome(
            incomplete=True, phase_errors=[], pre_restart_gateway_pids=[child.pid], restarted_services=[],
            failed_or_stale_units=[], relaunched_profiles=[], externally_supervised_profiles=[], killed_pids=set(),
        )
        fleet = [
            {"profile": "default", "pid": child.pid, "state": "stale", "code_sha": "a" * 40},
            {"profile": "fresh", "pid": os.getpid(), "state": "current", "code_sha": "b" * 40},
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            signalled = signal_stale_fleet_survivors(fleet, restart, 10.0)
        assert child.wait(timeout=15) == 75
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()

    assert signalled == [child.pid]
    assert marker.exists()
    assert restart.killed_pids == {child.pid}


def test_verify_fleet_hands_stale_rows_to_survivor_signalling(monkeypatch):
    """The wiring: a stale fleet matrix reaches signal_stale_fleet_survivors; the restart stays owed."""
    import hermes_cli.update_cmd_fleet as fleet_mod
    import hermes_cli.update_cmd_fleet_verify as fleet_verify
    import hermes_cli.update_cmd_stale_survivors as surv
    from hermes_cli import update_cmd
    from hermes_cli.update_cmd_fleet import _GatewayRestartOutcome

    stale_fleet = [{"profile": "default", "pid": 4242, "state": "stale", "code_sha": "a" * 40}]
    seen = {}
    monkeypatch.setattr(fleet_verify, "_print_legacy_units_warning", lambda: None)
    monkeypatch.setattr(update_cmd, "_finish_dashboard_update_cleanup", lambda *a, **k: None)
    # The live post-update dashboard pass restarts a managed hermes-dashboard unit.
    monkeypatch.setattr("hermes_cli.update_cmd_maint._refresh_dashboard_after_update", lambda **k: set())
    monkeypatch.setattr(update_cmd, "_surviving_pre_update_serve_runtimes", lambda plan: [])
    monkeypatch.setattr(fleet_verify, "_collect_fleet_snapshot", lambda restart, expected: stale_fleet)
    monkeypatch.setattr(surv, "signal_stale_fleet_survivors",
                        lambda fleet, restart, budget: seen.setdefault("fleet", fleet) and [])
    restart = _GatewayRestartOutcome(
        incomplete=False, phase_errors=[], pre_restart_gateway_pids=[4242], restarted_services=[],
        failed_or_stale_units=[], relaunched_profiles=[], externally_supervised_profiles=[], killed_pids=set(),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        fleet_verify._verify_fleet_after_update(
            restart, _pre_update_plan=None, _windows_gateway_resume=None, update_complete=True,
        )
    # Contract C3: was SystemExit(1); now flagged incomplete (owed restart, marker kept).
    assert restart.incomplete
    assert seen["fleet"] == stale_fleet
