"""Mocked Linux lifecycle contracts for retained dashboard update recovery."""
from types import SimpleNamespace

import pytest

from hermes_cli import dashboard_procs as dp, main_dashboard as dash


def test_respawn_candidates_refuse_foreign_and_ephemeral():
    rows = [(1, ["hermes", "serve", "--port", "9119"], "/own"),
            (2, ["hermes", "serve", "--port", "0"], "/own"),
            (3, ["hermes", "serve", "--port", "9120"], "/foreign"),
            (4, ["hermes", "serve", "--port", "9119"], "/own")]
    assert dp._filter_dashboard_respawn_candidates(rows, own_home="/own") == [rows[0][1]]


def test_update_restart_uses_owned_unit_and_manual_argv(monkeypatch):
    import gateway.status
    monkeypatch.setattr(dp, "_lock_owned_serve_pids", lambda: {3})
    def find_candidates(**kw):
        assert kw["exclude_pids"] == {3}
        assert kw["scope_home"] == "/own"
        return [1, 2]
    monkeypatch.setattr(dash, "_find_stale_dashboard_pids", find_candidates)
    monkeypatch.setattr(dp, "_hermes_home_for_pid", lambda pid, **kw: "/own")
    monkeypatch.setattr(gateway.status, "get_process_start_time", lambda pid: pid * 10)
    monkeypatch.setattr(dash, "_get_systemd_service_for_pid", lambda pid: "a.service" if pid == 1 else None)
    monkeypatch.setattr(dash, "_get_pid_cgroup_path", lambda pid: "/user.slice/a.service")
    monkeypatch.setattr(dash, "_dashboard_cmdline_for_pid", lambda pid: ["hermes", "serve", "--port", "9119"])
    calls = []
    monkeypatch.setattr(dp, "_kill_pids_posix", lambda pids, killed, failed, **kw: killed.extend(pids))
    monkeypatch.setattr(dash, "_try_restart_systemd_service", lambda unit, cg: calls.append((unit, cg)) or True)
    monkeypatch.setattr(dash, "_respawn_dashboard_processes", lambda commands: calls.append(commands) or [])
    result = dp._kill_stale_dashboard_processes(restart_managed=True, scope_home="/own")
    assert result["killed"] == [1, 2] and result["unrecovered"] == []
    assert calls == [("a.service", "/user.slice/a.service"), [["hermes", "serve", "--port", "9119"]]]


def test_changed_process_identity_is_not_signaled(monkeypatch):
    import gateway.status
    starts = iter([10, 11])
    monkeypatch.setattr(dp, "_lock_owned_serve_pids", set)
    monkeypatch.setattr(dash, "_find_stale_dashboard_pids", lambda **kw: [1])
    monkeypatch.setattr(dp, "_hermes_home_for_pid", lambda pid, **kw: "/own")
    monkeypatch.setattr(gateway.status, "get_process_start_time", lambda pid: next(starts))
    monkeypatch.setattr(dash, "_get_systemd_service_for_pid", lambda pid: None)
    monkeypatch.setattr(dash, "_get_pid_cgroup_path", lambda pid: None)
    monkeypatch.setattr(dash, "_dashboard_cmdline_for_pid", lambda pid: ["hermes", "serve"])
    monkeypatch.setattr(dp, "_kill_pids_posix", lambda *a: (_ for _ in ()).throw(AssertionError("signal")))
    result = dp._kill_stale_dashboard_processes(restart_managed=True, scope_home="/own")
    assert result["killed"] == [] and result["failed"] == [(1, "process identity changed")]


def test_respawn_is_detached_and_failure_is_reported(monkeypatch, tmp_path):
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(dash.time, "sleep", lambda _: None)
    calls = []
    def spawn(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(dash.subprocess, "Popen", spawn)
    assert dash._respawn_dashboard_processes([["hermes", "dashboard"]]) == []
    assert calls[0][0] == ["hermes", "dashboard", "--no-open"]
    assert calls[0][1]["start_new_session"] is True and calls[0][1]["close_fds"] is True


def test_historical_recovery_acknowledges_mocked_respawn(monkeypatch, tmp_path):
    import json
    from hermes_cli import update_serve_resume
    import pm.environments
    monkeypatch.setattr(pm.environments, "activate_dependencies", lambda root: None)
    calls = []
    monkeypatch.setattr(dash, "_respawn_dashboard_processes", lambda commands: calls.extend(commands) or [])
    context, result = tmp_path / "context.json", tmp_path / "result.json"
    context.write_text(json.dumps({"root": str(tmp_path), "home": "/own", "stopped_serves": {
        "pending": True, "entries": [{"pid": 42, "purpose": "serve", "port": 9119,
                                      "host": "127.0.0.1", "hermes_home": "/own"}]}}))
    assert update_serve_resume.main(context, result) == 0
    assert json.loads(result.read_text()) == {"serves_handled": True}
    assert len(calls) == 1 and calls[0][-5:] == ["serve", "--host", "127.0.0.1", "--port", "9119"]


@pytest.mark.parametrize("own_group", [True, False])
def test_termination_cleans_owned_group_but_never_caller_group(monkeypatch, own_group):
    import psutil
    import signal
    import gateway.status

    calls = []
    root = SimpleNamespace(pid=32100, create_time=lambda: 1.0, alive=True)
    child = SimpleNamespace(pid=32101, create_time=lambda: 2.0, alive=True, children=list)
    root.children = lambda: [child]
    root.is_running = lambda: root.alive
    child.is_running = lambda: child.alive
    def wait(**kw):
        root.alive = False
    def send(proc, sig):
        calls.append((proc.pid, sig))
        if proc is child:
            child.alive = False
    root.wait = wait
    root.send_signal = lambda sig: send(root, sig)
    child.send_signal = lambda sig: send(child, sig)
    def killpg(group, sig):
        calls.append(("group", group, sig))
        child.alive = False
    monkeypatch.setattr(dp, "_caller_ancestor_pids", set)
    monkeypatch.setattr(gateway.status, "get_process_start_time", lambda pid: 10)
    monkeypatch.setattr(psutil, "Process", lambda pid: root if pid == root.pid else child)
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, **kw: (list(procs), []))
    monkeypatch.setattr(dp.os, "getsid", lambda pid: 42)
    monkeypatch.setattr(dp.os, "getpgrp", lambda: 77)
    monkeypatch.setattr(dp.os, "getpgid", lambda pid: root.pid if own_group else 77)
    monkeypatch.setattr(dp.os, "killpg", killpg)
    killed, failed = [], []
    dp._kill_pids_posix([root.pid], killed, failed, expected_start_times={root.pid: 10})
    assert killed == [root.pid] and failed == []
    assert calls[0] == (root.pid, signal.SIGTERM)
    assert (("group", root.pid, signal.SIGKILL) in calls) is own_group


@pytest.mark.parametrize("starts", [(11,), (10, 11)])
def test_identity_change_between_admission_and_signal_refuses_signal(monkeypatch, starts):
    import gateway.status
    import psutil

    monkeypatch.setattr(dp, "_caller_ancestor_pids", set)
    values = iter(starts)
    monkeypatch.setattr(gateway.status, "get_process_start_time", lambda pid: next(values))
    root = SimpleNamespace(create_time=lambda: 1.0, children=list,
                           send_signal=lambda sig: pytest.fail("replacement process signalled"))
    monkeypatch.setattr(psutil, "Process", lambda pid: root)
    killed, failed = [], []
    dp._kill_pids_posix([32100], killed, failed, expected_start_times={32100: 10})
    assert killed == [] and len(failed) == 1 and failed[0][0] == 32100
    assert failed[0][1].startswith("process identity changed")
