"""Tests for Linux stale-dashboard cleanup during update."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hermes_cli.main_dashboard import _find_stale_dashboard_pids, _restart_managed_dashboard_service
from hermes_cli.dashboard_procs import _kill_stale_dashboard_processes
from hermes_cli import dashboard_procs
from hermes_cli import main_dashboard
from hermes_cli import update_cmd_maint

_real_service_for_pid = main_dashboard._get_systemd_service_for_pid
_real_cmdline_for_pid = main_dashboard._dashboard_cmdline_for_pid


@pytest.fixture(autouse=True)
def _mock_lifecycle_boundaries(monkeypatch):
    """Keep update tests away from live process and service state."""
    import gateway.status
    import hermes_cli.process_identity as identity

    monkeypatch.setattr(gateway.status, "get_process_start_time", lambda pid: pid * 10)
    monkeypatch.setattr(dashboard_procs, "_hermes_home_for_pid", lambda pid, **kw: "/home/u/.hermes")
    monkeypatch.setattr(dashboard_procs, "_caller_ancestor_pids", set)
    monkeypatch.setattr(dashboard_procs, "_lock_owned_serve_pids", set)
    monkeypatch.setattr(identity, "ledger_entries", list)
    monkeypatch.delenv("HERMES_DESKTOP_CHILD_PID", raising=False)
    monkeypatch.setattr(main_dashboard, "_get_systemd_service_for_pid", lambda pid: None)
    monkeypatch.setattr(main_dashboard, "_get_pid_cgroup_path", lambda pid: None)
    monkeypatch.setattr(main_dashboard, "_dashboard_cmdline_for_pid", lambda pid: None)
    def stop(pids, killed, failed, *, expected_start_times):
        assert expected_start_times == {pid: pid * 10 for pid in pids}
        killed.extend(pids)
    monkeypatch.setattr(dashboard_procs, "_kill_pids_posix", stop)


def _ps_line(pid: int, cmd: str) -> str:
    """Format a line as it would appear in ``ps -A -o pid=,command=`` output."""
    return f"{pid:>7} {cmd}"


class TestFindStaleDashboardPids:
    """Unit tests for the ps-based detection step."""



    @pytest.mark.platforms("linux")
    def test_self_pid_excluded(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n".join([
                    _ps_line(os.getpid(), "python3 -m hermes_cli.main dashboard"),
                    _ps_line(12345, "hermes dashboard --port 9119"),
                ]) + "\n",
                stderr="",
            )
            pids = _find_stale_dashboard_pids()
        assert os.getpid() not in pids
        assert 12345 in pids


    def _assert_ps_timeout_returns_empty(self):
        import subprocess as sp
        with patch("subprocess.run", side_effect=sp.TimeoutExpired("ps", 10)):
            assert _find_stale_dashboard_pids() == []

    @pytest.mark.platforms("linux")
    def test_ps_timeout_returns_empty_linux(self):
        self._assert_ps_timeout_returns_empty()





@pytest.mark.platforms("linux")
class TestKillStaleDashboardPosix:
    """Linux lifecycle admission and managed-service scope."""


    def test_admitted_processes_are_stopped_with_captured_identity(self, monkeypatch):
        calls = []
        def stop(pids, killed, failed, *, expected_start_times):
            calls.append((pids, expected_start_times))
            killed.extend(pids)
        monkeypatch.setattr(dashboard_procs, "_kill_pids_posix", stop)
        monkeypatch.setattr(main_dashboard, "_find_stale_dashboard_pids", lambda **kw: [12345, 12346])
        result = _kill_stale_dashboard_processes()
        assert calls == [([12345], {12345: 123450}), ([12346], {12346: 123460})]
        assert result == {"matched": [12345, 12346], "killed": [12345, 12346],
                          "failed": [], "unrecovered": [12345, 12346]}





    def test_user_scope_restart_never_falls_back_to_system_or_sudo(self, capsys):
        """A user unit is discovered and restarted through ``systemctl --user``.

        Nothing here may touch the system scope or sudo.
        """
        calls: list[list[str]] = []

        def fake_run(args, *a, **kw):
            calls.append(list(args))
            if args == ["systemctl", "--user", "list-unit-files", "hermes-dashboard.service", "--no-legend", "--no-pager"]:
                return MagicMock(returncode=0, stdout="hermes-dashboard.service enabled enabled\n", stderr="")
            if args == ["systemctl", "--user", "is-active", "hermes-dashboard.service"]:
                return MagicMock(returncode=0, stdout="active\n", stderr="")
            if args == ["systemctl", "--user", "is-enabled", "hermes-dashboard.service"]:
                return MagicMock(returncode=0, stdout="enabled\n", stderr="")
            if args == ["systemctl", "--user", "restart", "hermes-dashboard.service"]:
                return MagicMock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected subprocess.run call: {args}")

        with patch("subprocess.run", side_effect=fake_run), \
             patch("hermes_cli.main_dashboard._find_stale_dashboard_pids", return_value=[]), \
             patch("os.kill") as kill:
            _restart_managed_dashboard_service("update")

        assert ["systemctl", "--user", "restart", "hermes-dashboard.service"] in calls
        assert all(call[:1] != ["sudo"] and call[:2] == ["systemctl", "--user"] for call in calls)
        kill.assert_not_called()








class TestDashboardUpdateCleanup:
    """The updater reports dashboard cleanup accurately."""

    def test_all_failed_stops_do_not_claim_the_dashboard_was_stopped(self, capsys, monkeypatch, tmp_path):
        own_home = tmp_path / "profiles" / "work"
        monkeypatch.setenv("HERMES_HOME", str(own_home))
        with patch(
            "hermes_cli.dashboard_procs._kill_stale_dashboard_processes",
            return_value={"matched": [12345], "killed": [], "failed": [(12345, "denied")],
                          "unrecovered": []},
        ) as kill:
            update_cmd_maint._refresh_dashboard_after_update()

        # The sweep only touches this home's backends (#113978).
        assert kill.call_args.kwargs["scope_home"] == str(own_home)
        assert "stopped during update" not in capsys.readouterr().out




@pytest.mark.platforms("linux")
class TestSupervisedBackendRestart:
    """After the kill, systemd-supervised PIDs get their owning unit
    restarted (#68934) — SIGTERM reads as a clean stop to systemd, so
    Restart=on-failure never fires on its own."""

    def _live(self):
        return main_dashboard

    def test_supervised_pid_restarts_owning_unit(self, capsys):
        """A killed PID whose cgroup names a custom unit → systemctl restart."""
        live = self._live()

        with patch.object(main_dashboard, "_restart_managed_dashboard_service", return_value=False), \
             patch.object(live, "_find_stale_dashboard_pids", return_value=[4321]), \
             patch.object(main_dashboard, "_get_pid_cgroup_path",
                          return_value="/system.slice/hermes-serve.service"), \
             patch.object(main_dashboard, "_get_systemd_service_for_pid",
                          return_value="hermes-serve.service"), \
             patch.object(main_dashboard, "_try_restart_systemd_service", return_value=True) as restart, \
             patch("time.sleep"):
            _kill_stale_dashboard_processes(restart_managed=True, scope_home="/home/u/.hermes")

        restart.assert_called_once_with(
            "hermes-serve.service", "/system.slice/hermes-serve.service"
        )
        # Supervised restart succeeded — no manual hint.
        assert "when you're ready" not in capsys.readouterr().out

    def test_already_restarted_unit_is_left_untouched(self):
        """Review on #83595: hermes update's systemd fleet-restart loop may
        already have restarted this PID's owning unit directly (e.g. a
        Serve-only install). Passing it via already_restarted_units must
        skip killing/restarting it again here."""
        live = self._live()

        with patch.object(main_dashboard, "_restart_managed_dashboard_service", return_value=False), \
             patch.object(live, "_find_stale_dashboard_pids", return_value=[4321]), \
             patch.object(main_dashboard, "_get_pid_cgroup_path",
                          return_value="/system.slice/hermes-serve.service"), \
             patch.object(main_dashboard, "_get_systemd_service_for_pid",
                          return_value="hermes-serve.service"), \
             patch.object(main_dashboard, "_try_restart_systemd_service") as restart, \
             patch.object(dashboard_procs, "_kill_pids_posix") as kill, \
             patch("time.sleep"):
            result = _kill_stale_dashboard_processes(
                restart_managed=True, already_restarted_units={"hermes-serve"}
            )

        kill.assert_not_called()
        restart.assert_not_called()
        assert result == {"matched": [], "killed": [], "failed": [], "unrecovered": []}

    @pytest.mark.parametrize("main_pid, restarted", [("991", False), ("4321", True)],
                             ids=["foreign-unit-cgroup", "unit-main-process"])
    def test_only_the_unit_whose_main_process_is_the_backend_is_restarted(self, main_pid, restarted):
        """A dashboard started by hand from a shell inside some unit (CI runner agent, cron, tmux,
        the gateway's terminal tool) sits in that unit's cgroup. Only a unit whose MainPID IS the
        backend supervises it; any other unit is not restarted and the backend is respawned from
        its argv instead."""
        live = self._live()
        argv = ["hermes", "dashboard", "--port", "8300"]
        unit_cgroup = "/system.slice/hosted-compute-agent.service"
        probes: list[list[str]] = []

        def fake_probe(cmd, *, timeout):
            probes.append(list(cmd))
            out = main_pid if cmd[-2:] == ["--property=MainPID", "--value"] else ""
            return MagicMock(returncode=0, stdout=out, stderr="")

        with patch.object(main_dashboard, "_restart_managed_dashboard_service", return_value=False), \
             patch.object(live, "_find_stale_dashboard_pids", return_value=[4321]), \
             patch.object(main_dashboard, "_get_systemd_service_for_pid", _real_service_for_pid), \
             patch.object(main_dashboard, "_pid_unified_cgroup_entries", lambda pid: iter([unit_cgroup])), \
             patch.object(main_dashboard, "_run_probe", side_effect=fake_probe), \
             patch.object(main_dashboard, "_dashboard_cmdline_for_pid", return_value=argv), \
             patch("hermes_cli.dashboard_procs._hermes_home_for_pid", return_value="/home/u/.hermes"), \
             patch.object(live, "_respawn_dashboard_processes", return_value=[]) as respawn, \
             patch("time.sleep"):
            result = _kill_stale_dashboard_processes(restart_managed=True, scope_home="/home/u/.hermes")

        restarts = [c for c in probes if "restart" in c]
        if restarted:
            assert restarts == [["systemctl", "restart", "hosted-compute-agent.service"]]
            respawn.assert_not_called()
        else:
            assert restarts == [], f"restarted a unit that does not supervise the dashboard: {restarts}"
            respawn.assert_called_once_with([argv])
        assert result["unrecovered"] == []


class TestManualBackendRespawn:
    """Manually-started dashboards/serves have their argv captured before the
    kill and are respawned detached after the update (#40449)."""

    def _live(self):
        return main_dashboard


    @pytest.mark.platforms("linux")
    def test_argv_capture_failure_falls_back_to_hint(self, capsys):
        live = self._live()

        with patch.object(main_dashboard, "_restart_managed_dashboard_service", return_value=False), \
             patch.object(live, "_find_stale_dashboard_pids", return_value=[5555]), \
             patch.object(main_dashboard, "_get_pid_cgroup_path", return_value=None), \
             patch.object(main_dashboard, "_get_systemd_service_for_pid", return_value=None), \
             patch.object(main_dashboard, "_dashboard_cmdline_for_pid", return_value=None), \
             patch.object(live, "_respawn_dashboard_processes") as respawn, \
             patch("time.sleep"):
            result = _kill_stale_dashboard_processes(restart_managed=True, scope_home="/home/u/.hermes")

        respawn.assert_not_called()
        assert result["unrecovered"] == [5555]

    @pytest.mark.platforms("linux")
    def test_non_orphan_fixed_port_still_respawns(self, capsys):
        """A supervised-by-shell dashboard with a fixed port is still restarted."""
        live = self._live()
        argv = ["hermes", "dashboard", "--port", "8300"]

        with patch.object(main_dashboard, "_restart_managed_dashboard_service", return_value=False), \
             patch.object(live, "_find_stale_dashboard_pids", return_value=[6001]), \
             patch.object(main_dashboard, "_get_pid_cgroup_path", return_value=None), \
             patch.object(main_dashboard, "_get_systemd_service_for_pid", return_value=None), \
             patch.object(main_dashboard, "_dashboard_cmdline_for_pid", return_value=argv), \
             patch("hermes_cli.dashboard_procs._hermes_home_for_pid", return_value="/home/u/.hermes"), \
             patch.object(live, "_respawn_dashboard_processes", return_value=[]) as respawn, \
             patch("time.sleep"):
            _kill_stale_dashboard_processes(restart_managed=True, scope_home="/home/u/.hermes")

        respawn.assert_called_once_with([argv])
        assert "when you're ready" not in capsys.readouterr().out

    @pytest.mark.platforms("linux")
    def test_port_zero_serves_killed_without_respawn(self, capsys):
        """``serve --port 0`` backends are stopped but not resurrected (#78821)."""
        live = self._live()
        argv = [
            "python", "-m", "hermes_cli.main",
            "serve", "--host", "127.0.0.1", "--port", "0",
        ]

        with patch.object(main_dashboard, "_restart_managed_dashboard_service", return_value=False), \
             patch.object(live, "_find_stale_dashboard_pids",
                          return_value=[7001, 7002, 7003]), \
             patch.object(main_dashboard, "_get_pid_cgroup_path", return_value=None), \
             patch.object(main_dashboard, "_get_systemd_service_for_pid", return_value=None), \
             patch.object(main_dashboard, "_dashboard_cmdline_for_pid", return_value=argv), \
             patch("hermes_cli.dashboard_procs._hermes_home_for_pid", return_value="/home/u/.hermes"), \
             patch.object(live, "_respawn_dashboard_processes") as respawn, \
             patch("time.sleep"):
            result = _kill_stale_dashboard_processes(restart_managed=True, scope_home="/home/u/.hermes")

        respawn.assert_not_called()
        assert sorted(result["killed"]) == [7001, 7002, 7003]
        # Stopped processes intentionally excluded from respawn remain unrecovered.
        assert sorted(result["unrecovered"]) == [7001, 7002, 7003]
        assert "when you're ready" not in capsys.readouterr().out


    def test_respawn_adds_no_open_to_dashboard_commands(self, tmp_path, monkeypatch):
        """Respawned `dashboard` argv gains --no-open; `serve` argv untouched."""
        live = self._live()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        spawned: list[list[str]] = []

        class _FakePopen:
            def __init__(self, cmd, **kwargs):
                spawned.append(list(cmd))

            def poll(self):
                return None

        with patch.object(live.subprocess, "Popen", _FakePopen), \
             patch.object(live.time, "sleep"):
            failed = live._respawn_dashboard_processes([
                ["hermes", "dashboard", "--port", "8300"],
                ["hermes", "serve", "--host", "0.0.0.0"],
            ])

        assert failed == []
        assert spawned[0] == ["hermes", "dashboard", "--port", "8300", "--no-open"]
        assert spawned[1] == ["hermes", "serve", "--host", "0.0.0.0"]

    def test_respawn_failure_returned(self, tmp_path, monkeypatch, capsys):
        live = self._live()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

        with patch.object(live.subprocess, "Popen", side_effect=OSError("no such file")), \
             patch.object(live.time, "sleep"):
            failed = live._respawn_dashboard_processes([["hermes", "serve"]])

        assert failed == [["hermes", "serve"]]

    def test_pre_takeover_interpreter_launcher_argv_is_rebuilt(self, tmp_path, monkeypatch):
        """A kernel-captured ``[old venv python, launcher, ...]`` argv is respawned through
        this install's launcher command instead of being replayed verbatim after the PM
        takeover rewrote the launcher into a shell shim (#124778)."""
        live = self._live()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        spawned: list[list[str]] = []

        class _FakePopen:
            def __init__(self, cmd, **kwargs):
                spawned.append(list(cmd))

            def poll(self):
                return None

        captured = [
            "/old/venv/bin/python", "/home/u/.local/bin/hermes",
            "dashboard", "--no-open", "--host", "127.0.0.1", "--port", "34553",
        ]
        from hermes_cli import _launchers
        from hermes_cli._launchers import runtime_command

        with patch.object(live.subprocess, "Popen", _FakePopen), \
             patch.object(live.time, "sleep"), \
             patch.object(_launchers, "resolve_store_python", return_value=None):
            failed = live._respawn_dashboard_processes([captured])
            expected = runtime_command(Path(live.__file__).resolve().parents[1], captured[2:])

        assert failed == []
        assert spawned == [expected]

    def test_child_exiting_within_the_grace_window_is_a_reported_failure(
            self, tmp_path, monkeypatch, capsys):
        """A respawn that dies at once (parse error on a stale argv, port already bound)
        must surface as a failure, never as ``✓ restarted`` (#124778), reported with the
        caller's argv so it matches the stopped PID's cmdline (#109290)."""
        live = self._live()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

        class _DyingPopen:
            returncode = 1

            def __init__(self, cmd, **kwargs):
                pass

            def poll(self):
                return 1

        with patch.object(live.subprocess, "Popen", _DyingPopen), \
             patch.object(live.time, "sleep"):
            failed = live._respawn_dashboard_processes([["hermes", "dashboard", "--port", "8300"]])

        out = capsys.readouterr().out
        assert failed == [["hermes", "dashboard", "--port", "8300"]]
        assert "✓ restarted" not in out
        assert "✗ failed to restart" in out


class TestFilterDashboardRespawnCandidates:
    """Unit tests for respawn filtering / dedupe / orphan skip (#78821)."""

    def test_skips_serve_port_zero(self):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        argv = [
            "python", "-m", "hermes_cli.main",
            "--profile", "mini-cat",
            "serve", "--host", "127.0.0.1", "--port", "0",
        ]
        assert _filter_dashboard_respawn_candidates([
            (42, argv, "/home/u/.hermes/profiles/mini-cat"),
        ], own_home="/home/u/.hermes/profiles/mini-cat") == []

    def test_skips_legacy_dashboard_port_zero(self):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        argv = [
            "hermes", "--profile", "coder",
            "dashboard", "--no-open", "--host", "127.0.0.1", "--port", "0",
        ]
        assert _filter_dashboard_respawn_candidates([(7, argv, None)]) == []

    def test_skips_serve_port_equals_zero(self):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        argv = ["hermes", "serve", "--port=0"]
        assert _filter_dashboard_respawn_candidates([(1, argv, None)]) == []


    def test_dedupes_identical_normalized_cmdlines(self):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        a = ["/usr/bin/python3", "-m", "hermes_cli.main", "dashboard", "--port", "8300"]
        b = ["/other/python", "-m", "hermes_cli.main", "dashboard", "--port", "8300"]
        out = _filter_dashboard_respawn_candidates([
            (1, a, None),
            (2, b, None),
        ])
        assert out == [a]

    def test_caps_one_per_profile(self):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        a = ["hermes", "--profile", "coder", "dashboard", "--port", "8300"]
        b = ["hermes", "--profile", "coder", "dashboard", "--port", "8301"]
        c = ["hermes", "--profile", "writer", "dashboard", "--port", "8302"]
        out = _filter_dashboard_respawn_candidates([
            (1, a, None),
            (2, b, None),
            (3, c, None),
        ])
        assert out == [a, c]

    def test_caps_one_per_hermes_home(self):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        home = "/tmp/hermes-home-a"
        a = ["hermes", "dashboard", "--port", "8300"]
        b = ["hermes", "dashboard", "--port", "8301"]
        out = _filter_dashboard_respawn_candidates(
            [
                (1, a, home),
                (2, b, home),
            ],
            own_home=home,
        )
        assert out == [a]

    def test_profile_home_shares_cap_for_explicit_profile(self):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        a = ["hermes", "--profile", "coder", "dashboard", "--port", "8300"]
        b = ["hermes", "dashboard", "--port", "8301"]
        out = _filter_dashboard_respawn_candidates([
            (1, a, "/home/u/.hermes/profiles/coder"),
            (2, b, "/home/u/.hermes/profiles/coder"),
        ], own_home="/home/u/.hermes/profiles/coder")
        assert out == [a]

    def test_default_profile_same_root_home_caps(self):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        a = ["hermes", "--profile", "default", "dashboard", "--port", "8300"]
        b = ["hermes", "dashboard", "--port", "8301"]
        home = "/home/u/.hermes"
        out = _filter_dashboard_respawn_candidates(
            [
                (1, a, home),
                (2, b, home),
            ],
            own_home=home,
        )
        assert out == [a]

    def test_foreign_home_backend_is_not_replayed(self):
        """A backend from another HERMES_HOME is never respawned (#94030).

        Its supervisor/user owns its lifecycle; an argv-only replay would
        run on the updating install's home and steal the foreign install's
        fixed port.  Supersedes the old "distinct homes don't share a cap"
        pin — a foreign home is no longer replayed at all.
        """
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        a = ["hermes", "dashboard", "--port", "8300"]
        b = ["hermes", "dashboard", "--port", "8301"]
        out = _filter_dashboard_respawn_candidates(
            [
                (1, a, "/home/u/.hermes"),
                (2, b, "/work/project/.hermes"),
            ],
            own_home="/home/u/.hermes",
        )
        assert out == [a]

    def test_skips_sidecar_fixed_port_serve_on_foreign_home(self):
        """A sidecar on a foreign home is not replayed."""
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        argv = [
            "/Users/u/.hermes-sidecar/hermes-agent/venv/bin/python",
            "-m", "hermes_cli.main",
            "serve", "--host", "127.0.0.1", "--port", "9118", "--skip-build",
        ]
        out = _filter_dashboard_respawn_candidates(
            [(15364, argv, "/Users/u/.hermes-lifeos")],
            own_home="/Users/u/.hermes",
        )
        assert out == []

    def test_matching_hermes_home_is_kept(self):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        argv = ["hermes", "serve", "--host", "127.0.0.1", "--port", "9118"]
        out = _filter_dashboard_respawn_candidates(
            [(15364, argv, "/Users/u/.hermes")],
            own_home="/Users/u/.hermes",
        )
        assert out == [argv]

    def test_symlinked_hermes_home_compares_equal(self, tmp_path):
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        real = tmp_path / "real-home"
        real.mkdir()
        link = tmp_path / "linked-home"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform")

        argv = ["hermes", "serve", "--port", "9118"]
        out = _filter_dashboard_respawn_candidates(
            [(15364, argv, str(real))],
            own_home=str(link),
        )
        assert out == [argv]

    def test_unknown_home_stays_eligible(self):
        """Unreadable HERMES_HOME (env probe failed) keeps pre-#94030 behaviour."""
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        argv = ["hermes", "dashboard", "--port", "8300"]
        out = _filter_dashboard_respawn_candidates(
            [(1, argv, None)],
            own_home="/home/u/.hermes",
        )
        assert out == [argv]

    def test_own_home_defaults_to_get_hermes_home(self, monkeypatch):
        from pathlib import Path

        import hermes_constants
        from hermes_cli.dashboard_procs import _filter_dashboard_respawn_candidates

        monkeypatch.setattr(
            hermes_constants, "get_hermes_home", lambda: Path("/home/u/.hermes")
        )
        argv = ["hermes", "serve", "--port", "9118"]
        foreign = _filter_dashboard_respawn_candidates([
            (1, argv, "/Users/u/.hermes-lifeos"),
        ])
        assert foreign == []
        own = _filter_dashboard_respawn_candidates([
            (1, argv, "/home/u/.hermes"),
        ])
        assert own == [argv]




class TestCmdlineCapture:
    """_dashboard_cmdline_for_pid reads /proc on Linux, ps on macOS."""

    def _live(self):
        return main_dashboard

    @pytest.mark.platforms("linux")
    def test_reads_proc_cmdline_when_available(self, tmp_path, monkeypatch):
        live = self._live()
        proc_file = tmp_path / "cmdline"
        proc_file.write_bytes(b"/usr/bin/python3\x00-m\x00hermes_cli.main\x00serve\x00")

        real_exists = os.path.exists

        def fake_exists(path):
            if path == "/proc/777/cmdline":
                return True
            return real_exists(path)

        real_open = open

        def fake_open(path, *a, **kw):
            if path == "/proc/777/cmdline":
                return real_open(proc_file, *a, **kw)
            return real_open(path, *a, **kw)

        with patch.object(live, "_dashboard_cmdline_for_pid", _real_cmdline_for_pid), \
             patch.object(live.os.path, "exists", fake_exists), \
             patch("builtins.open", fake_open):
            argv = main_dashboard._dashboard_cmdline_for_pid(777)

        assert argv == ["/usr/bin/python3", "-m", "hermes_cli.main", "serve"]

    @pytest.mark.platforms("linux")
    def test_falls_back_to_ps_without_proc(self, monkeypatch):
        live = self._live()

        def fake_run(args, *a, **kw):
            assert args == ["ps", "-p", "888", "-o", "command="]
            return MagicMock(returncode=0, stdout="hermes serve --port 8300\n", stderr="")

        with patch.object(live, "_dashboard_cmdline_for_pid", _real_cmdline_for_pid), \
             patch.object(live.os.path, "exists", return_value=False), \
             patch("subprocess.run", side_effect=fake_run):
            argv = main_dashboard._dashboard_cmdline_for_pid(888)

        assert argv == ["hermes", "serve", "--port", "8300"]



class TestPostUpdateDashboardCleanupIsolation:
    """Post-update dashboard cleanup is one isolated step among the others."""

    def test_cleanup_failure_is_isolated_and_recorded(self, capsys):
        """A failure inside the dashboard scan (#112604) must not abort the fleet-verification
        tail (matrix, reconciliation, inner receipt finalize): contained, visible, recorded as
        a failed step on the open receipt."""
        import hermes_cli.update_receipt as ur

        ur._current.set(None)
        try:
            ur.begin_update_receipt()
            with patch(
                "hermes_cli.dashboard_procs._kill_stale_dashboard_processes",
                side_effect=OSError("dashboard scan failed"),
            ):
                update_cmd_maint._refresh_dashboard_after_update()  # must not raise

            steps = {s["name"]: s for s in ur._current.get().data["steps"]}
        finally:
            ur._current.set(None)

        assert steps["dashboard_cleanup"]["ok"] is False
        assert "dashboard scan failed" in steps["dashboard_cleanup"]["detail"]
        assert "dashboard scan failed" in capsys.readouterr().out
