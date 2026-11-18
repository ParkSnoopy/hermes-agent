"""Tests for gateway service management helpers."""

import json
import os

import subprocess

from pathlib import Path
from types import SimpleNamespace

import pytest
import hermes_constants

pwd = pytest.importorskip("pwd")
grp = pytest.importorskip("grp")

import hermes_cli.gateway as gateway_cli

from gateway import status
from gateway.restart import (
    DEFAULT_GATEWAY_API_DRAIN_TIMEOUT,
    DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT,
    GATEWAY_FATAL_CONFIG_EXIT_CODE,
    GATEWAY_SERVICE_RESTART_EXIT_CODE,
    resolve_systemd_timeout_stop_sec,
)




class TestUserSystemdPrivateSocketPreflight:
    def test_preflight_accepts_private_socket_without_dbus_bus(self, monkeypatch):
        monkeypatch.setattr(gateway_cli, "_ensure_user_systemd_env", lambda: None)
        monkeypatch.setattr(gateway_cli, "_user_dbus_socket_path", lambda: Path("/tmp/missing-bus"))
        monkeypatch.setattr(gateway_cli, "_user_systemd_private_socket_path", lambda: Path("/tmp/private-socket"))
        monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/tmp/private-socket")

        gateway_cli._preflight_user_systemd(auto_enable_linger=False)

    def test_wait_for_user_dbus_socket_accepts_private_socket(self, monkeypatch):
        calls = []
        monkeypatch.setattr(gateway_cli, "_ensure_user_systemd_env", lambda: calls.append("env"))
        monkeypatch.setattr(gateway_cli, "_user_dbus_socket_path", lambda: Path("/tmp/missing-bus"))
        monkeypatch.setattr(gateway_cli, "_user_systemd_private_socket_path", lambda: Path("/tmp/private-socket"))
        monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/tmp/private-socket")

        assert gateway_cli._wait_for_user_dbus_socket(timeout=0.1) is True
        assert calls == ["env"]


class TestSystemdServiceRefresh:

    def test_systemd_restart_timeout_prints_status_guidance(self, monkeypatch, capsys):
        """`hermes gateway restart` must not surface a raw TimeoutExpired traceback.

        The dashboard spawns `hermes gateway restart` in the background; when a
        wedged adapter websocket pushes drain past the 90s CLI timeout, the
        dashboard would previously show a Python traceback (issue #19937
        follow-up: the same failure mode applies to restart, not just stop).
        """
        monkeypatch.setattr(gateway_cli, "_select_systemd_scope", lambda system=False: False)
        monkeypatch.setattr(gateway_cli, "_require_service_installed", lambda action, system=False: None)
        monkeypatch.setattr(gateway_cli, "_preflight_user_systemd", lambda: None)
        monkeypatch.setattr(gateway_cli, "refresh_systemd_unit_if_needed", lambda system=False: None)
        monkeypatch.setattr(status, "get_running_pid", lambda cleanup_stale=True: None)
        monkeypatch.setattr(gateway_cli, "_systemd_main_pid", lambda system=False: None)
        monkeypatch.setattr(
            gateway_cli,
            "_recover_pending_systemd_restart",
            lambda system=False, previous_pid=None: False,
        )
        monkeypatch.setattr(
            gateway_cli,
            "_systemd_service_is_start_limited",
            lambda system=False: False,
        )

        def fake_run_systemctl(args, **kwargs):
            # reset-failed is a pre-step (check=False, 30s) — let it pass.
            if args and args[0] == "reset-failed":
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))

        monkeypatch.setattr(gateway_cli, "_run_systemctl", fake_run_systemctl)

        gateway_cli.systemd_restart()

        output = capsys.readouterr().out
        assert "still restarting after 90s" in output
        assert "hermes gateway status" in output

    def test_refresh_refuses_to_bake_pytest_tmpdir_into_real_user_unit(
        self, tmp_path, monkeypatch
    ):
        """Defense in depth: ``refresh_systemd_unit_if_needed()`` runs every
        time ``run_gateway()`` starts. The user-scope unit path resolves
        under ``Path.home()`` (NOT sandboxed by conftest), and
        ``generate_systemd_unit()`` bakes ``HERMES_HOME`` into the unit's
        ``Environment=`` line. Without this guard, any test that drives
        ``run_gateway()`` end-to-end on a real Linux dev box silently
        rewrites the developer's installed gateway unit with a
        ``/tmp/pytest-of-.../hermes_test`` HERMES_HOME — silently breaking
        their gateway on the next boot. The guard sniffs the generated
        unit body for tmpdir markers and refuses the write. Tests that
        legitimately exercise the refresh flow patch
        ``generate_systemd_unit`` to return synthetic content that doesn't
        carry those markers.
        """
        unit_path = tmp_path / "hermes-gateway.service"
        unit_path.write_text("old unit\n", encoding="utf-8")

        monkeypatch.setattr(
            gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path
        )
        # Realistic generated unit referencing a pytest tmpdir HERMES_HOME
        polluted_unit = (
            "[Service]\n"
            'Environment="HERMES_HOME=/tmp/pytest-of-alice/pytest-42/'
            'popen-gw0/test_x/hermes_test"\n'
        )
        monkeypatch.setattr(
            gateway_cli,
            "generate_systemd_unit",
            lambda system=False, run_as_user=None: polluted_unit,
        )

        # If the guard fails, daemon-reload would be called — record it.
        ran = []

        def fake_run(cmd, check=True, **kwargs):
            ran.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(gateway_cli.subprocess, "run", fake_run)

        result = gateway_cli.refresh_systemd_unit_if_needed(system=False)

        assert result is False, "refresh should refuse to write a polluted unit"
        assert (
            unit_path.read_text(encoding="utf-8") == "old unit\n"
        ), "installed unit must be left untouched"
        assert not any(
            "daemon-reload" in str(c) for c in ran
        ), "daemon-reload must not run when write was refused"


class TestRequireServiceInstalled:
    def test_exits_with_install_hint_when_unit_missing(self, tmp_path, monkeypatch, capsys):
        unit_path = tmp_path / "hermes-gateway.service"
        monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path)

        with pytest.raises(SystemExit) as exc_info:
            gateway_cli._require_service_installed("start")

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "not installed" in out
        assert "hermes gateway install" in out

    def test_passes_when_unit_exists(self, tmp_path, monkeypatch):
        unit_path = tmp_path / "hermes-gateway.service"
        unit_path.write_text("[Unit]\n", encoding="utf-8")
        monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path)

        gateway_cli._require_service_installed("start")


class TestServiceIdentityForForeignHome:
    """A HERMES_HOME that is neither ``~/.hermes`` nor ``~/.hermes/profiles/<name>`` must never resolve to
    the default profile's ``hermes-gateway`` unit (a temp-home harness uninstalled the production gateway)."""

    @pytest.fixture
    def machine_home(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(hermes_constants, "_account_home", lambda: home)  # names services
        monkeypatch.setenv("HOME", str(home))  # the user unit dir follows it (#98699)
        monkeypatch.delenv("HERMES_REAL_HOME", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        return home

    def test_foreign_home_gets_its_own_unit(self, machine_home, tmp_path, monkeypatch):
        foreign = tmp_path / "elsewhere"
        foreign.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(foreign))

        default_unit = machine_home / ".config" / "systemd" / "user" / "hermes-gateway.service"
        assert gateway_cli.get_service_name() != "hermes-gateway"
        assert gateway_cli.get_systemd_unit_path() != default_unit
        assert gateway_cli.get_systemd_unit_path().parent == default_unit.parent

    def test_default_and_named_profile_homes_keep_their_names(self, machine_home, monkeypatch):
        default_home = machine_home / ".hermes"
        (default_home / "profiles" / "alpha").mkdir(parents=True)

        monkeypatch.setenv("HERMES_HOME", str(default_home))
        assert gateway_cli.get_service_name() == "hermes-gateway"

        monkeypatch.setenv("HERMES_HOME", str(default_home / "profiles" / "alpha"))
        assert gateway_cli.get_service_name() == "hermes-gateway-alpha"

    def test_sudo_user_default_home_keeps_bare_service_name(self, machine_home, tmp_path, monkeypatch):
        sudo_home = tmp_path / "alice"
        sudo_default = sudo_home / ".hermes"
        sudo_default.mkdir(parents=True)
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setenv("SUDO_USER", "alice")
        monkeypatch.setattr(pwd, "getpwnam", lambda user: SimpleNamespace(pw_dir=str(sudo_home)))

        # Before unit sync, sudo resolves the root process's native home.
        monkeypatch.delenv("HERMES_HOME", raising=False)
        assert gateway_cli.get_service_name() == "hermes-gateway"

        # After unit sync, HERMES_HOME points at the invoking user's native home.
        monkeypatch.setenv("HERMES_HOME", str(sudo_default))
        assert gateway_cli.get_service_name() == "hermes-gateway"


class TestUninstallRefusesForeignUnit:
    """systemd_uninstall must not stop/disable/unlink a unit pinned to another HERMES_HOME."""

    def test_unit_for_other_home_is_left_alone(self, tmp_path, monkeypatch, capsys):
        unit_path = tmp_path / "hermes-gateway.service"
        unit_path.write_text('[Service]\nEnvironment="HERMES_HOME=/somewhere/else"\n', encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "mine"))
        monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path)
        monkeypatch.setattr(gateway_cli, "_systemd_scope_preamble", lambda *a, **k: False)
        calls = []
        monkeypatch.setattr(gateway_cli, "_run_systemctl", lambda args, **k: calls.append(args))

        gateway_cli.systemd_uninstall(system=False)

        assert unit_path.exists()
        assert calls == []
        assert "/somewhere/else" in capsys.readouterr().out



class TestGeneratedSystemdUnits:
    def _expected_timeout_stop_sec(self) -> str:
        timeout = resolve_systemd_timeout_stop_sec(
            DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT,
            DEFAULT_GATEWAY_API_DRAIN_TIMEOUT,
        )
        return f"TimeoutStopSec={timeout}"

    def test_timeout_stop_sec_covers_default_cron_drain_floor(self, monkeypatch):
        """#94759: default restart_drain_timeout=0 still leaves a 30s cron
        floor plus cleanup reserve. The old max(60, drain+30)=60 unit
        SIGKILLed that in-budget drain."""
        monkeypatch.delenv("HERMES_RESTART_DRAIN_TIMEOUT", raising=False)
        monkeypatch.delenv("HERMES_CRON_DRAIN_TIMEOUT", raising=False)
        monkeypatch.setattr(gateway_cli, "_get_restart_drain_timeout", lambda: 0.0)


        unit = gateway_cli.generate_systemd_unit(system=False)
        expected = resolve_systemd_timeout_stop_sec(
            0.0, DEFAULT_GATEWAY_API_DRAIN_TIMEOUT
        )
        assert f"TimeoutStopSec={expected}" in unit
        assert expected > 60
        assert self._expected_timeout_stop_sec() in unit



    def test_restart_exit_code_is_also_declared_a_success_status(self):
        """#104251: a planned restart (gateway/restart.py's exit 75) is force-restarted
        via RestartForceExitStatus, but without SuccessExitStatus=75 too, systemd still
        classifies the exit as a failure -- the unit flips to ``failed``/``Result=exit-code``
        and any OnFailure= alert unit fires on every routine restart (hermes update,
        hermes gateway restart, the in-app restart). SuccessExitStatus=75 keeps the same
        force-restart behavior while letting the unit land back in ``active``/``success``,
        so OnFailure= stays reserved for actual failures."""
        unit = gateway_cli.generate_systemd_unit(system=False)
        assert f"SuccessExitStatus={GATEWAY_SERVICE_RESTART_EXIT_CODE}" in unit
        # Must still force an actual restart on that exit code -- SuccessExitStatus alone
        # would otherwise let the process stay stopped instead of being relaunched.
        assert f"RestartForceExitStatus={GATEWAY_SERVICE_RESTART_EXIT_CODE}" in unit
        assert f"RestartPreventExitStatus={GATEWAY_FATAL_CONFIG_EXIT_CODE}" in unit

    def test_user_unit_carries_ld_library_path_escaped_for_systemd(self, monkeypatch, tmp_path):
        """#14613: glibc reads LD_LIBRARY_PATH only at process start, so the unit file is the
        only place it can reach CUDA-backed tools; quotes/backslashes must survive systemd quoting."""
        # The absent-env branch falls back to the installed unit: keep the host's real one out.
        monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda system=False: tmp_path / "hermes-gateway.service")
        monkeypatch.setenv("LD_LIBRARY_PATH", '/opt/cu"da/lib64:/opt/back\\slash/lib')

        unit = gateway_cli.generate_systemd_unit(system=False)
        assert 'Environment="LD_LIBRARY_PATH=/opt/cu\\"da/lib64:/opt/back\\\\slash/lib"' in unit

        monkeypatch.setenv("LD_LIBRARY_PATH", "")
        assert "LD_LIBRARY_PATH" not in gateway_cli.generate_systemd_unit(system=False)
        monkeypatch.delenv("LD_LIBRARY_PATH")
        assert "LD_LIBRARY_PATH" not in gateway_cli.generate_systemd_unit(system=False)





class TestWslInteropPaths:
    """_build_wsl_interop_paths() — only Windows-interop tool dirs belong in the unit's PATH.

    #73163: scraping every ``/mnt/`` entry from the shell PATH persisted heavy
    Desktop-app/git/node dirs into the gateway unit's Environment=PATH, and the
    Plan 9 interop (9p) connections those dirs force at gateway start can
    exhaust the 9p server connection limit. which()-resolved tool dirs and the
    hardcoded System32 set already cover interop.
    """

    def test_arbitrary_mount_entries_are_not_scraped_from_shell_path(self, monkeypatch):
        monkeypatch.setattr(gateway_cli, "is_wsl", lambda: True)
        monkeypatch.setenv(
            "PATH",
            os.pathsep.join(
                [
                    "/usr/local/bin",
                    "/mnt/d/heavy-app/bin",
                    "/mnt/d/tools/git/cmd",
                    "/mnt/c/Users/me/AppData/Local/Programs/desktop/bin",
                ]
            ),
        )
        monkeypatch.setattr(gateway_cli.shutil, "which", lambda name: None)
        # The hardcoded candidates (System32…) don't exist on this macOS host, so the
        # expected result is empty — no /mnt/ entry survives.
        monkeypatch.setattr(Path, "exists", lambda self: False)

        result = gateway_cli._build_wsl_interop_paths([])

        assert result == []

    def test_which_resolved_tool_dirs_and_system32_set_are_included(self, monkeypatch):
        monkeypatch.setattr(gateway_cli, "is_wsl", lambda: True)
        monkeypatch.setenv(
            "PATH",
            "/usr/local/bin:/mnt/d/heavy-app/bin:/mnt/c/WINDOWS/system32",
        )

        def fake_which(name):
            resolved = {
                "powershell.exe": "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
                "cmd.exe": "/mnt/c/WINDOWS/system32/cmd.exe",
                "explorer.exe": "/mnt/c/WINDOWS/explorer.exe",
                "wsl.exe": "/mnt/c/Windows/System32/wsl.exe",
            }
            return resolved.get(name)

        monkeypatch.setattr(gateway_cli.shutil, "which", fake_which)
        monkeypatch.setattr(Path, "exists", lambda self: True)

        result = gateway_cli._build_wsl_interop_paths(["/mnt/d/heavy-app/bin"])

        # which()-resolved dirs land even when absent from the caller's PATH entry list.
        assert "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0" in result
        assert "/mnt/c/WINDOWS/system32" in result
        # Hardcoded System32 family, gated on existence like on a real install.
        assert "/mnt/c/WINDOWS" in result
        assert "/mnt/c/WINDOWS/System32/Wbem" in result
        # Heavy shell-PATH /mnt/ entries never enter, and dedupe keeps out the
        # entry the caller already has.
        assert "/mnt/d/heavy-app/bin" not in result

    def test_outside_wsl_nothing_is_added(self, monkeypatch):
        monkeypatch.setattr(gateway_cli, "is_wsl", lambda: False)
        monkeypatch.setenv("PATH", "/usr/local/bin:/mnt/c/WINDOWS/system32")

        assert gateway_cli._build_wsl_interop_paths([]) == []


class TestGatewayStopCleanup:
    @pytest.mark.platforms("linux")
    def test_stop_only_kills_current_profile_by_default(self, tmp_path, monkeypatch):
        """Without --all, stop uses systemd (if available) and does NOT call
        the global kill_gateway_processes().

        Linux-gated: the routing under test is the systemd arm, and it is only
        reached when the host really isn't macOS/Windows (the old
        ``is_macos → False`` stub is gone).
        """
        unit_path = tmp_path / "hermes-gateway.service"
        unit_path.write_text("unit\n", encoding="utf-8")

        monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: True)
        monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path)

        service_calls = []
        kill_calls = []

        monkeypatch.setattr(gateway_cli, "systemd_stop", lambda system=False: service_calls.append("stop"))
        monkeypatch.setattr(
            gateway_cli,
            "kill_gateway_processes",
            lambda force=False, all_profiles=False: kill_calls.append(force) or 2,
        )

        gateway_cli.gateway_command(SimpleNamespace(gateway_command="stop"))

        assert service_calls == ["stop"]
        # Global kill should NOT be called without --all
        assert kill_calls == []







class TestGatewayServiceDetection:
    def test_supports_systemd_services_requires_systemctl_binary(self, monkeypatch):
        monkeypatch.setattr(gateway_cli, "is_linux", lambda: True)
        monkeypatch.setattr(gateway_cli.shutil, "which", lambda name: None)

        assert gateway_cli.supports_systemd_services() is False

    def test_supports_systemd_services_returns_true_when_systemctl_present(self, monkeypatch):
        monkeypatch.setattr(gateway_cli, "is_linux", lambda: True)
        monkeypatch.setattr(gateway_cli, "is_wsl", lambda: False)
        monkeypatch.setattr(gateway_cli.shutil, "which", lambda name: "/usr/bin/systemctl")

        assert gateway_cli.supports_systemd_services() is True

    def test_is_service_running_checks_system_scope_when_user_scope_is_inactive(self, monkeypatch):
        user_unit = SimpleNamespace(exists=lambda: True)
        system_unit = SimpleNamespace(exists=lambda: True)

        monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: True)
        monkeypatch.setattr(
            gateway_cli,
            "get_systemd_unit_path",
            lambda system=False: system_unit if system else user_unit,
        )

        def fake_run(cmd, capture_output=True, text=True, **kwargs):
            if cmd == ["systemctl", "--user", "is-active", gateway_cli.get_service_name()]:
                return SimpleNamespace(returncode=0, stdout="inactive\n", stderr="")
            if cmd == ["systemctl", "is-active", gateway_cli.get_service_name()]:
                return SimpleNamespace(returncode=0, stdout="active\n", stderr="")
            raise AssertionError(f"Unexpected command: {cmd}")

        monkeypatch.setattr(gateway_cli.subprocess, "run", fake_run)

        assert gateway_cli._is_service_running() is True


class TestGatewaySystemServiceRouting:
    def test_systemd_restart_gracefully_restarts_running_service_and_waits(self, monkeypatch, capsys):
        calls = []

        monkeypatch.setattr(gateway_cli, "_select_systemd_scope", lambda system=False: False)
        monkeypatch.setattr(gateway_cli, "_require_service_installed", lambda action, system=False: None)
        monkeypatch.setattr(gateway_cli, "_preflight_user_systemd", lambda **kwargs: None)
        monkeypatch.setattr(gateway_cli, "refresh_systemd_unit_if_needed", lambda system=False: calls.append(("refresh", system)))
        # Wait budget covers after-turn deferral + drain + headroom (#77184).
        monkeypatch.setattr(gateway_cli, "_get_restart_exit_wait_budget", lambda: 27.0)
        monkeypatch.setattr(
            "gateway.status.get_running_pid",
            lambda: 654,
        )
        monkeypatch.setattr(
            gateway_cli,
            "_graceful_restart_via_sigusr1",
            lambda pid, timeout, **_: calls.append(("graceful", pid, timeout)) or True,
        )

        # Once SIGUSR1 makes the gateway exit with the planned restart code,
        # systemd is the only restart owner.  The CLI must only observe the
        # replacement instead of issuing a second stop/start transition.
        def fake_subprocess_run(cmd, **kwargs):
            raise AssertionError(f"Unexpected systemctl call: {cmd}")

        monkeypatch.setattr(gateway_cli.subprocess, "run", fake_subprocess_run)
        monkeypatch.setattr(
            gateway_cli,
            "_wait_for_systemd_service_restart",
            lambda system=False, previous_pid=None, replacement_observed=None: calls.append(
                ("wait", system, previous_pid)
            )
            or True,
        )

        gateway_cli.systemd_restart()

        assert ("graceful", 654, 27.0) in calls
        assert ("wait", False, 654) in calls
        out = capsys.readouterr().out.lower()
        assert "restarting gracefully" in out
        assert "21627" not in out  # must use the mocked budget, not live defaults
        assert "27" in out

    def test_systemd_restart_forces_recovery_only_when_handoff_has_no_replacement(
        self, monkeypatch, capsys
    ):
        calls = []

        monkeypatch.setattr(gateway_cli, "_select_systemd_scope", lambda system=False: False)
        monkeypatch.setattr(gateway_cli, "_require_service_installed", lambda action, system=False: None)
        monkeypatch.setattr(gateway_cli, "_preflight_user_systemd", lambda **kwargs: None)
        monkeypatch.setattr(gateway_cli, "refresh_systemd_unit_if_needed", lambda system=False: None)
        monkeypatch.setattr(gateway_cli, "_get_restart_exit_wait_budget", lambda: 27.0)
        monkeypatch.setattr("gateway.status.get_running_pid", lambda: 654)
        monkeypatch.setattr(gateway_cli, "_graceful_restart_via_sigusr1", lambda pid, timeout, **_: True)
        waits = iter((False, True))
        monkeypatch.setattr(
            gateway_cli,
            "_wait_for_systemd_service_restart",
            lambda system=False, previous_pid=None, replacement_observed=None: next(waits),
        )
        monkeypatch.setattr(gateway_cli, "_systemd_service_is_start_limited", lambda system=False: False)
        monkeypatch.setattr(
            gateway_cli,
            "_read_systemd_unit_properties",
            lambda system=False, properties=None: {"ActiveState": "inactive", "MainPID": "0"},
        )
        monkeypatch.setattr(
            gateway_cli,
            "_run_systemctl",
            lambda args, **kwargs: calls.append((args, kwargs))
            or SimpleNamespace(returncode=0, stdout="", stderr=""),
        )

        gateway_cli.systemd_restart()

        assert [call[0][0] for call in calls] == ["reset-failed", "start"]
        assert "did not relaunch" in capsys.readouterr().out

    def test_systemd_restart_does_not_force_an_unready_replacement(self, monkeypatch):
        calls = []

        monkeypatch.setattr(gateway_cli, "_select_systemd_scope", lambda system=False: False)
        monkeypatch.setattr(gateway_cli, "_require_service_installed", lambda action, system=False: None)
        monkeypatch.setattr(gateway_cli, "_preflight_user_systemd", lambda **kwargs: None)
        monkeypatch.setattr(gateway_cli, "refresh_systemd_unit_if_needed", lambda system=False: None)
        monkeypatch.setattr(gateway_cli, "_get_restart_exit_wait_budget", lambda: 27.0)
        monkeypatch.setattr("gateway.status.get_running_pid", lambda: 654)
        monkeypatch.setattr(gateway_cli, "_graceful_restart_via_sigusr1", lambda pid, timeout, **_: True)
        monkeypatch.setattr(
            gateway_cli,
            "_wait_for_systemd_service_restart",
            lambda system=False, previous_pid=None, replacement_observed=None: False,
        )
        monkeypatch.setattr(gateway_cli, "_systemd_service_is_start_limited", lambda system=False: False)
        monkeypatch.setattr(
            gateway_cli,
            "_read_systemd_unit_properties",
            lambda system=False, properties=None: {"ActiveState": "active", "MainPID": "777"},
        )
        monkeypatch.setattr(gateway_cli, "_run_systemctl", lambda args, **kwargs: calls.append(args))

        gateway_cli.systemd_restart()

        assert calls == []

    def test_systemd_restart_does_not_recover_a_failed_replacement(self, monkeypatch):
        calls = []

        monkeypatch.setattr(gateway_cli, "_select_systemd_scope", lambda system=False: False)
        monkeypatch.setattr(gateway_cli, "_require_service_installed", lambda action, system=False: None)
        monkeypatch.setattr(gateway_cli, "_preflight_user_systemd", lambda **kwargs: None)
        monkeypatch.setattr(gateway_cli, "refresh_systemd_unit_if_needed", lambda system=False: None)
        monkeypatch.setattr(gateway_cli, "_get_restart_exit_wait_budget", lambda: 27.0)
        monkeypatch.setattr("gateway.status.get_running_pid", lambda: 654)
        monkeypatch.setattr(gateway_cli, "_graceful_restart_via_sigusr1", lambda pid, timeout, **_: True)

        def failed_replacement_wait(
            system=False, previous_pid=None, replacement_observed=None
        ):
            replacement_observed.append(True)
            return False

        monkeypatch.setattr(
            gateway_cli,
            "_wait_for_systemd_service_restart",
            failed_replacement_wait,
        )
        monkeypatch.setattr(gateway_cli, "_run_systemctl", lambda args, **kwargs: calls.append(args))

        gateway_cli.systemd_restart()

        assert calls == []

    def test_systemd_restart_does_not_recover_when_handoff_state_is_unknown(
        self, monkeypatch
    ):
        calls = []

        monkeypatch.setattr(gateway_cli, "_select_systemd_scope", lambda system=False: False)
        monkeypatch.setattr(gateway_cli, "_require_service_installed", lambda action, system=False: None)
        monkeypatch.setattr(gateway_cli, "_preflight_user_systemd", lambda **kwargs: None)
        monkeypatch.setattr(gateway_cli, "refresh_systemd_unit_if_needed", lambda system=False: None)
        monkeypatch.setattr(gateway_cli, "_get_restart_exit_wait_budget", lambda: 27.0)
        monkeypatch.setattr("gateway.status.get_running_pid", lambda: 654)
        monkeypatch.setattr(gateway_cli, "_graceful_restart_via_sigusr1", lambda pid, timeout, **_: True)
        monkeypatch.setattr(
            gateway_cli,
            "_wait_for_systemd_service_restart",
            lambda system=False, previous_pid=None, replacement_observed=None: False,
        )
        monkeypatch.setattr(gateway_cli, "_systemd_service_is_start_limited", lambda system=False: False)
        monkeypatch.setattr(
            gateway_cli,
            "_read_systemd_unit_properties",
            lambda system=False, properties=None: {},
        )
        monkeypatch.setattr(gateway_cli, "_run_systemctl", lambda args, **kwargs: calls.append(args))

        gateway_cli.systemd_restart()

        assert calls == []

    def test_systemd_restart_wait_timeout_includes_supervisor_budgets(self, monkeypatch):
        monkeypatch.setattr(
            gateway_cli,
            "_read_systemd_unit_properties",
            lambda system=False, properties=None: {
                "RestartUSec": "5s",
                "TimeoutStartUSec": "1min 30s",
            },
        )

        assert gateway_cli._systemd_restart_wait_timeout() == 155.0

    def test_wait_records_a_short_lived_failed_replacement(self, monkeypatch):
        ticks = iter((0.0, 0.0, 2.0))
        monkeypatch.setattr(gateway_cli.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(gateway_cli.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(
            gateway_cli,
            "_read_systemd_unit_properties",
            lambda system=False, properties=None: {
                "ActiveState": "failed",
                "MainPID": "0",
            },
        )
        monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)
        monkeypatch.setattr(
            gateway_cli,
            "_read_gateway_runtime_status",
            lambda: {"pid": 777, "gateway_state": "startup_failed"},
        )
        replacement_observed = []

        result = gateway_cli._wait_for_systemd_service_restart(
            previous_pid=654,
            timeout=1.0,
            replacement_observed=replacement_observed,
        )

        assert result is False
        assert replacement_observed == [True]

    def test_wait_accepts_a_degraded_replacement_as_restarted(self, monkeypatch, capsys):
        """A replacement serving with a parked platform stamps ``degraded`` for its whole life; the
        restart verifier must report a restart (with a warning), not wait out the timeout (#91547)."""
        monkeypatch.setattr(gateway_cli.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(
            gateway_cli,
            "_read_systemd_unit_properties",
            lambda system=False, properties=None: {"ActiveState": "active", "MainPID": "777"},
        )
        monkeypatch.setattr("gateway.status.get_running_pid", lambda: 777)
        monkeypatch.setattr(
            gateway_cli,
            "_read_gateway_runtime_status",
            lambda: {"pid": 777, "gateway_state": "degraded"},
        )

        result = gateway_cli._wait_for_systemd_service_restart(previous_pid=654, timeout=1.0)

        assert result is True
        assert "DEGRADED" in capsys.readouterr().out





def _seed_pm_environment(tmp_path, monkeypatch, with_venv_fact=True):
    """Commit a pm environment the way ``pm sync`` records it: an install-keyed
    facts.json whose venv fact names a committed environment generation under
    the install state (pyvenv.cfg included). Returns
    ``(project_root, environment_dir)``."""
    from pm.environments import install_state_dir

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))  # installs_root() under the test root
    project_root = tmp_path / "payload" / "hermes-agent"
    project_root.mkdir(parents=True)
    state = install_state_dir(project_root)
    environment = state / "environments" / "gen-1"
    environment.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("", encoding="utf-8")
    packages = (
        {"venv": {"stamp": "abc", "extras": [], "environment": str(environment)}}
        if with_venv_fact
        else {}
    )
    (state / "facts.json").write_text(
        json.dumps({"schema": 1, "packages": packages}), encoding="utf-8"
    )
    return project_root, environment


class TestServicePathDirsPmVenv:
    """Service PATH cannot retain a garbage-collectable dependency generation."""

    def test_does_not_persist_disposable_generation_bin(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.prefix", "/usr")
        monkeypatch.setattr("sys.base_prefix", "/usr")
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.delenv("HERMES_RUNTIME_DIR", raising=False)

        project_root, environment = _seed_pm_environment(tmp_path, monkeypatch)
        venv_bin = environment / "bin"
        venv_bin.mkdir()
        monkeypatch.setattr(gateway_cli, "PROJECT_ROOT", project_root)

        dirs = gateway_cli._build_service_path_dirs(project_root=project_root)

        assert str(venv_bin) not in dirs


def _seed_pm_node_facts(hermes_root):
    """Write a pm installed-state file recording node/npm store entries.

    _append_node_dir_for_service() resolves managed Node through pm's
    installed-state (facts.json env PATH entries with ``{{store}}`` templates
    resolved against the target home's store), so tests seed the record the
    way a real `pm install` writes it — via the same Facts schema, read back
    through Facts.env_for().
    """
    store_root = hermes_root / "tools"
    node_dir = store_root / "node-v22.0.0"
    npm_dir = store_root / "npm-9.0.0" / "bin"
    node_dir.mkdir(parents=True)
    npm_dir.mkdir(parents=True)
    facts = {
        "schema": 1,
        "packages": {
            "node": {
                "entry": "node-v22.0.0",
                "version": "22.0.0",
                "env": {"PATH": ["{{store}}/node-v22.0.0"]},
            },
            "npm": {
                "entry": "npm-9.0.0",
                "version": "9.0.0",
                "env": {"PATH": ["{{store}}/npm-9.0.0/bin"]},
            },
        },
    }
    import json as _json

    (store_root / "facts.json").write_text(_json.dumps(facts), encoding="utf-8")
    return [str(npm_dir), str(node_dir)]


class TestSystemUnitHermesHome:
    """HERMES_HOME in system units must reference the target user, not root."""

    def test_no_pm_node_never_bakes_the_invokers_path_node(
        self, monkeypatch, tmp_path
    ):
        """Hermes runs only its PM-managed Node: a PATH node is never written into a unit."""
        (tmp_path / ".hermes" / "tools").mkdir(parents=True)
        monkeypatch.setattr(
            gateway_cli.shutil, "which", lambda name: "/opt/external-node/bin/node"
        )
        entries: list[str] = []

        gateway_cli._append_node_dir_for_service(entries, tmp_path / ".hermes")

        assert entries == []

    def test_stale_pm_facts_without_dirs_contribute_nothing(
        self, monkeypatch, tmp_path
    ):
        """Recorded entries whose store dirs are gone contribute nothing, and no PATH node replaces them."""
        import shutil as _shutil

        hermes_root = tmp_path / ".hermes"
        for entry in _seed_pm_node_facts(hermes_root):
            _shutil.rmtree(entry)
        monkeypatch.setattr(
            gateway_cli.shutil, "which", lambda name: "/opt/external-node/bin/node"
        )
        entries: list[str] = []

        gateway_cli._append_node_dir_for_service(entries, hermes_root)

        assert entries == []

    def test_managed_node_makes_system_unit_independent_of_callers_path(
        self, monkeypatch, tmp_path
    ):
        """A target-managed Node must suppress caller-specific PATH fallbacks."""
        target_home = tmp_path / "home" / "alice"
        target_hermes = target_home / ".hermes"
        root_home = tmp_path / "root"
        root_hermes = root_home / ".hermes"
        managed_dirs = _seed_pm_node_facts(target_hermes)
        root_hermes.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", staticmethod(lambda: root_home))
        monkeypatch.setenv("HERMES_HOME", str(root_hermes))
        monkeypatch.setattr(
            gateway_cli,
            "_system_service_identity",
            lambda run_as_user=None: ("alice", "alice", str(target_home), 1001),
        )
        monkeypatch.setattr(gateway_cli, "get_hermes_home", lambda: root_hermes)
        monkeypatch.setattr(gateway_cli, "_build_service_path_dirs", list)

        monkeypatch.setattr(gateway_cli.shutil, "which", lambda name: "/root/bin/node")
        root_unit = gateway_cli.generate_systemd_unit(system=True, run_as_user="alice")

        monkeypatch.setattr(gateway_cli.shutil, "which", lambda name: "/home/alice/.local/bin/node")
        user_unit = gateway_cli.generate_systemd_unit(system=True, run_as_user="alice")

        assert root_unit == user_unit
        for managed_dir in managed_dirs:
            assert managed_dir in root_unit
        assert "/root/bin" not in root_unit

    def test_system_unit_orders_after_target_user_manager(self, monkeypatch, tmp_path):
        """#104893: restart-safe workers need user@<uid>.service; the system unit must not race it at boot."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        monkeypatch.setattr(
            gateway_cli, "_system_service_identity",
            lambda run_as_user=None: ("alice", "alice", str(tmp_path), 1001),
        )
        monkeypatch.setattr(gateway_cli, "_build_service_path_dirs", list)

        system_unit = gateway_cli.generate_systemd_unit(system=True, run_as_user="alice")
        user_unit = gateway_cli.generate_systemd_unit(system=False)

        unit_section = system_unit.split("[Service]")[0]
        assert "After=user@1001.service" in unit_section
        assert "Wants=user@1001.service" in unit_section
        assert "user@" not in user_unit

    def test_installed_unit_keeps_ld_library_path_when_the_shell_lacks_it(self, monkeypatch, tmp_path):
        """The unit is regenerated and compared on every start/restart/status; a later shell without
        the export (ssh, cron, sudo) must see the installed unit as current, not "repair" the line away."""
        unit_path = tmp_path / "hermes-gateway.service"
        monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/cuda/lib64:/opt/pct%dir/lib")
        unit_path.write_text(gateway_cli.generate_systemd_unit(system=False), encoding="utf-8")
        assert 'Environment="LD_LIBRARY_PATH=/opt/cuda/lib64:/opt/pct%%dir/lib"' in unit_path.read_text()

        monkeypatch.delenv("LD_LIBRARY_PATH")

        assert gateway_cli.systemd_unit_is_current(system=False)
        assert 'LD_LIBRARY_PATH=/opt/cuda/lib64:/opt/pct%%dir/lib' in gateway_cli.generate_systemd_unit(system=False)

    def test_system_unit_remaps_caller_home_ld_library_path_components(self, monkeypatch):
        """#14613: under sudo the caller's /root/... library dirs are unreadable to the target
        user, so each colon-separated component is remapped like the PATH entries are."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/root")))
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setattr(
            gateway_cli, "_system_service_identity",
            lambda run_as_user=None: ("alice", "alice", "/home/alice", 1001),
        )
        monkeypatch.setattr(gateway_cli, "_build_service_path_dirs", list)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/root/cuda/lib:/opt/cuda/lib64")

        unit = gateway_cli.generate_systemd_unit(system=True, run_as_user="alice")

        assert 'Environment="LD_LIBRARY_PATH=/home/alice/cuda/lib:/opt/cuda/lib64"' in unit

    def test_system_unit_uses_target_user_home_not_calling_user(self, monkeypatch, tmp_path):
        caller_home = tmp_path / "root"
        target_home = tmp_path / "alice"
        caller_home.mkdir()
        target_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: caller_home))
        monkeypatch.setenv("HERMES_HOME", str(caller_home / ".hermes"))
        monkeypatch.setattr(
            gateway_cli, "_system_service_identity",
            lambda run_as_user=None: ("alice", "alice", str(target_home), 1001),
        )
        monkeypatch.setattr(
            gateway_cli, "_build_user_local_paths",
            lambda home, existing: [],
        )

        unit = gateway_cli.generate_systemd_unit(system=True, run_as_user="alice")

        assert f'HERMES_HOME={target_home / ".hermes"}' in unit
        assert str(caller_home / ".hermes") not in unit

    def test_user_unit_unaffected_by_change(self):
        # User-scope units should still use the calling user's HERMES_HOME
        unit = gateway_cli.generate_systemd_unit(system=False)

        hermes_home = str(gateway_cli.get_hermes_home().resolve())
        assert f'HERMES_HOME={hermes_home}' in unit


class TestSystemUnitRefreshSyncsHermesHome:
    """sudo system refresh must not flip TimeoutStopSec via /root/.hermes."""

    def test_refresh_adopts_unit_hermes_home_before_rewriting(self, tmp_path, monkeypatch):
        root_home = tmp_path / "root"
        alice_home = tmp_path / "alice"
        root_hermes = root_home / ".hermes"
        alice_hermes = alice_home / ".hermes"
        root_hermes.mkdir(parents=True)
        alice_hermes.mkdir(parents=True)
        (root_hermes / "config.yaml").write_text(
            "agent:\n  restart_drain_timeout: 60\n", encoding="utf-8"
        )
        (alice_hermes / "config.yaml").write_text(
            "agent:\n  restart_drain_timeout: 180\n", encoding="utf-8"
        )

        unit_path = tmp_path / "hermes-gateway.service"
        monkeypatch.setattr(Path, "home", staticmethod(lambda: root_home))
        monkeypatch.setattr(
            gateway_cli,
            "_system_service_identity",
            lambda run_as_user=None: ("alice", "alice", str(alice_home), 1001),
        )
        monkeypatch.setattr(
            gateway_cli, "_build_user_local_paths", lambda home, existing: []
        )
        monkeypatch.setattr(gateway_cli.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path)
        monkeypatch.setattr(gateway_cli, "_run_systemctl", lambda *a, **k: None)
        monkeypatch.delenv("HERMES_RESTART_DRAIN_TIMEOUT", raising=False)

        # Correct installed unit (operator's HERMES_HOME + drain timeout).
        monkeypatch.setenv("HERMES_HOME", str(alice_hermes))
        good_unit = gateway_cli.generate_systemd_unit(system=True, run_as_user="alice")
        assert f"TimeoutStopSec={resolve_systemd_timeout_stop_sec(180.0, DEFAULT_GATEWAY_API_DRAIN_TIMEOUT)}" in good_unit
        unit_path.write_text(good_unit, encoding="utf-8")

        # Simulate sudo without inherited HERMES_HOME (falls back to root).
        monkeypatch.setenv("HERMES_HOME", str(root_hermes))
        assert gateway_cli.refresh_systemd_unit_if_needed(system=True) is False
        assert unit_path.read_text(encoding="utf-8") == good_unit
        assert os.environ["HERMES_HOME"] == str(alice_hermes)
        assert gateway_cli.systemd_unit_is_current(system=True) is True




class TestHermesHomeForTargetUser:
    """Unit tests for _hermes_home_for_target_user()."""

    def test_remaps_default_home(self, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/root")))
        monkeypatch.delenv("HERMES_HOME", raising=False)

        result = gateway_cli._hermes_home_for_target_user("/home/alice")
        assert result == "/home/alice/.hermes"


class TestGeneratedUnitIncludesLocalBin:
    """~/.local/bin must be in PATH so uvx/pipx tools are discoverable."""

    def test_system_unit_includes_local_bin_in_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gateway_cli, "_system_service_identity",
                            lambda run_as_user=None: ("alice", "alice", str(tmp_path), 1001))
        monkeypatch.setattr(
            gateway_cli,
            "_build_user_local_paths",
            lambda home_path, existing: [str(home_path / ".local" / "bin")],
        )
        unit = gateway_cli.generate_systemd_unit(system=True)
        # System unit uses the resolved home dir from _system_service_identity
        assert "/.local/bin" in unit


class TestSystemServiceIdentityRootHandling:
    """Root user handling in _system_service_identity()."""

    def test_auto_detected_root_is_rejected(self, monkeypatch):
        """When root is auto-detected (not explicitly requested), raise."""

        monkeypatch.delenv("SUDO_USER", raising=False)
        monkeypatch.setenv("USER", "root")
        monkeypatch.setenv("LOGNAME", "root")

        with pytest.raises(ValueError, match="pass --run-as-user root to override"):
            gateway_cli._system_service_identity(run_as_user=None)

    def test_explicit_root_is_allowed(self, monkeypatch):
        """When root is explicitly passed via --run-as-user root, allow it."""

        root_info = pwd.getpwnam("root")

        username, _group, home, _uid = gateway_cli._system_service_identity(run_as_user="root")
        assert username == "root"
        assert home == root_info.pw_dir



class TestEnsureUserSystemdEnv:
    """Tests for _ensure_user_systemd_env() D-Bus session bus auto-detection."""

    def test_sets_dbus_address_when_bus_socket_exists(self, tmp_path, monkeypatch):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        bus_socket = runtime / "bus"
        bus_socket.touch()  # simulate the socket file

        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        monkeypatch.setattr(os, "getuid", lambda: 99)

        gateway_cli._ensure_user_systemd_env()

        assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={bus_socket}"



class TestPreflightUserSystemd:
    """Tests for _preflight_user_systemd() — D-Bus reachability before systemctl --user.

    Covers issue #5130 / Rick's RHEL 9.6 SSH scenario: setup tries to start the
    gateway via ``systemctl --user start`` in a shell with no user D-Bus session,
    which previously failed with a raw ``CalledProcessError`` and no remediation.
    """

    def test_raises_when_linger_disabled_and_loginctl_denied(self, monkeypatch):
        """Rick's scenario: no D-Bus, no linger, non-root SSH → clear error."""
        monkeypatch.setattr(
            gateway_cli, "_user_dbus_socket_path",
            lambda: type("P", (), {"exists": lambda self: False})(),
        )
        monkeypatch.setattr(
            gateway_cli, "_user_systemd_private_socket_path",
            lambda: type("P", (), {"exists": lambda self: False})(),
        )
        monkeypatch.setattr(
            gateway_cli, "get_systemd_linger_status", lambda username=None: (False, ""),
        )
        monkeypatch.setattr(gateway_cli.shutil, "which", lambda _: "/usr/bin/loginctl")

        class _Result:
            returncode = 1
            stdout = ""
            stderr = "Interactive authentication required."

        monkeypatch.setattr(
            gateway_cli.subprocess, "run", lambda *a, **kw: _Result(),
        )

        with pytest.raises(gateway_cli.UserSystemdUnavailableError) as exc_info:
            gateway_cli._preflight_user_systemd()

        msg = str(exc_info.value)
        assert "sudo loginctl enable-linger" in msg
        assert "hermes gateway run" in msg  # foreground fallback mentioned
        assert "Interactive authentication required" in msg

    def test_enable_linger_succeeds_and_socket_appears(self, monkeypatch, capsys):
        """Happy remediation path: polkit allows enable-linger, socket spawns."""
        monkeypatch.setattr(
            gateway_cli, "_user_dbus_socket_path",
            lambda: type("P", (), {"exists": lambda self: False})(),
        )
        monkeypatch.setattr(
            gateway_cli, "_user_systemd_private_socket_path",
            lambda: type("P", (), {"exists": lambda self: False})(),
        )
        monkeypatch.setattr(
            gateway_cli, "get_systemd_linger_status", lambda username=None: (False, ""),
        )
        monkeypatch.setattr(gateway_cli.shutil, "which", lambda _: "/usr/bin/loginctl")

        class _OkResult:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(
            gateway_cli.subprocess, "run", lambda *a, **kw: _OkResult(),
        )
        monkeypatch.setattr(
            gateway_cli, "_wait_for_user_dbus_socket",
            lambda timeout=5.0: True,
        )

        # Should not raise.
        gateway_cli._preflight_user_systemd()
        out = capsys.readouterr().out
        assert "Enabled linger" in out


class TestProfileArg:
    """Tests for _profile_arg — returns '--profile <name>' for named profiles."""

    def test_systemd_unit_for_target_user_includes_named_profile(self, tmp_path, monkeypatch):
        """sudo system install must keep the target user's named profile in ExecStart."""
        root_home = tmp_path / "root"
        target_home = tmp_path / "home" / "alice"
        root_profile = root_home / ".hermes" / "profiles" / "mybot"
        root_profile.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: root_home)
        monkeypatch.setenv("HERMES_HOME", str(root_profile))
        monkeypatch.setattr(gateway_cli, "get_hermes_home", lambda: root_profile)
        monkeypatch.setattr(
            gateway_cli,
            "_system_service_identity",
            lambda run_as_user=None: ("alice", "alice", str(target_home), 1001),
        )

        unit = gateway_cli.generate_systemd_unit(system=True, run_as_user="alice")

        assert "ExecStart=" in unit
        import shlex
        command = shlex.split(next(line.split("=", 1)[1] for line in unit.splitlines()
                                   if line.startswith("ExecStart=")))
        assert command[-4:] == ["--profile", "mybot", "gateway", "run"]
        assert f'HERMES_HOME={target_home / ".hermes" / "profiles" / "mybot"}' in unit









    def test_installed_service_kind_refuses_foreign_unit(self, tmp_path, monkeypatch):
        from hermes_cli import gateway_service_owner

        unit = tmp_path / "hermes-gateway.service"
        unit.write_text("[Service]\n", encoding="utf-8")
        monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: True)
        monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda system=False: unit)
        monkeypatch.setattr(gateway_service_owner, "foreign_systemd_unit_home", lambda system: "/foreign")
        assert gateway_cli._installed_service_kind() is None
        monkeypatch.setattr(gateway_service_owner, "foreign_systemd_unit_home", lambda system: None)
        assert gateway_cli._installed_service_kind() == "systemd"


class TestRemapPathForUser:
    """Unit tests for _remap_path_for_user()."""

    def test_remaps_path_under_current_home(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "root")
        (tmp_path / "root").mkdir()
        result = gateway_cli._remap_path_for_user(
            str(tmp_path / "root" / ".hermes" / "hermes-agent"),
            str(tmp_path / "alice"),
        )
        assert result == str(tmp_path / "alice" / ".hermes" / "hermes-agent")


class TestSystemUnitPathRemapping:
    """System units must remap ALL paths from the caller's home to the target user."""

    def test_system_unit_has_no_root_paths(self, monkeypatch, tmp_path):
        root_home = tmp_path / "root"
        root_home.mkdir()
        project = root_home / ".hermes" / "hermes-agent"
        project.mkdir(parents=True)
        venv_bin = project / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("")

        target_home = "/home/alice"

        monkeypatch.setattr(Path, "home", lambda: root_home)
        monkeypatch.setenv("HERMES_HOME", str(root_home / ".hermes"))
        monkeypatch.setattr(gateway_cli, "get_hermes_home", lambda: root_home / ".hermes")
        monkeypatch.setattr(gateway_cli, "PROJECT_ROOT", project)

        monkeypatch.setattr(gateway_cli, "get_python_path", lambda: str(venv_bin / "python"))
        monkeypatch.setattr(
            gateway_cli, "_system_service_identity",
            lambda run_as_user=None: ("alice", "alice", target_home, 1001),
        )

        unit = gateway_cli.generate_systemd_unit(system=True)

        # No root paths should leak into the unit
        assert str(root_home) not in unit
        # Target user paths should be present
        assert "/home/alice" in unit
        # WorkingDirectory is anchored at the target user's HERMES_HOME (stable,
        # always exists) — NOT the source checkout under it. Pinning cwd to the
        # checkout is the rot bug fixed alongside this: a relocated/removed
        # checkout would crash-loop the unit on CHDIR (status=200).
        assert "WorkingDirectory=/home/alice/.hermes" in unit
        assert "WorkingDirectory=/home/alice/.hermes/hermes-agent" not in unit


class TestDockerAwareGateway:
    """Tests for Docker container awareness in gateway commands."""

    def test_run_systemctl_raises_runtimeerror_when_missing(self, monkeypatch):
        """_run_systemctl raises RuntimeError with container guidance when systemctl is absent."""
        import pytest

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("systemctl")

        monkeypatch.setattr(gateway_cli.subprocess, "run", fake_run)

        from hermes_cli.gateway_command_errors import SystemctlUnavailableError

        with pytest.raises(SystemctlUnavailableError):
            gateway_cli._run_systemctl(["start", "hermes-gateway"])


    def test_install_in_container_prints_docker_guidance(self, monkeypatch, capsys):
        """'hermes gateway install' inside Docker exits 0 with container guidance."""
        import pytest

        monkeypatch.setattr(gateway_cli, "is_managed", lambda: False)
        monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: False)
        monkeypatch.setattr(gateway_cli, "is_wsl", lambda: False)
        monkeypatch.setattr(gateway_cli, "is_container", lambda: True)

        args = SimpleNamespace(gateway_command="install", force=False, system=False, run_as_user=None)
        with pytest.raises(SystemExit) as exc_info:
            gateway_cli.gateway_command(args)

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Docker" in out or "docker" in out
        assert "restart" in out.lower()

    def test_install_in_systemd_container_refuses_user_scope(self, monkeypatch, capsys):
        """A bind-mounted home must not receive a host-visible user unit."""
        monkeypatch.setattr(gateway_cli, "is_managed", lambda: False)
        monkeypatch.setattr(gateway_cli, "is_termux", lambda: False)
        monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: True)
        monkeypatch.setattr(gateway_cli, "is_container", lambda: True)
        monkeypatch.setattr(
            gateway_cli,
            "_install_systemd_from_cli",
            lambda *args, **kwargs: pytest.fail("must not install a user unit in a container"),
        )

        args = SimpleNamespace(gateway_command="install", force=False, system=False, run_as_user=None)
        with pytest.raises(SystemExit) as exc_info:
            gateway_cli.gateway_command(args)

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "--system" in out
        assert "user-scope" in out

    def test_install_in_systemd_container_keeps_explicit_system_scope(self, monkeypatch):
        """Explicit system installs stay available for systemd-managed containers."""
        monkeypatch.setattr(gateway_cli, "is_managed", lambda: False)
        monkeypatch.setattr(gateway_cli, "is_termux", lambda: False)
        monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: True)
        monkeypatch.setattr(gateway_cli, "is_container", lambda: True)
        calls = []
        monkeypatch.setattr(
            gateway_cli,
            "_install_systemd_from_cli",
            lambda *args, **kwargs: calls.append(kwargs),
        )

        args = SimpleNamespace(gateway_command="install", force=False, system=True, run_as_user=None)
        gateway_cli.gateway_command(args)

        assert calls == [{"force": False, "system": True, "run_as_user": None}]

    def test_setup_wizard_user_scope_in_container_skips_install(self, monkeypatch, capsys):
        """The wizard's default "user service" choice is the same host-visible unit (#112323):
        inside a container it prints the guidance and reports no install instead of writing it."""
        monkeypatch.setattr(gateway_cli, "is_container", lambda: True)
        monkeypatch.setattr(gateway_cli, "prompt_linux_gateway_install_scope", lambda: "user")
        monkeypatch.setattr(
            gateway_cli, "systemd_install",
            lambda **kwargs: pytest.fail("must not install a user unit in a container"),
        )

        assert gateway_cli.install_linux_gateway_from_setup(force=False, enable_on_startup=True) == ("user", False)
        assert "--system" in capsys.readouterr().out


class TestLegacyHermesUnitDetection:
    """Tests for _find_legacy_hermes_units / has_legacy_hermes_units.

    These guard against the scenario that tripped Luis in April 2026: an
    older install left a ``hermes.service`` unit behind when the service was
    renamed to ``hermes-gateway.service``. After PR #5646 (signal recovery
    via systemd), the two services began SIGTERM-flapping over the same
    Telegram bot token in a 30-second cycle.

    The detector must flag ``hermes.service`` ONLY when it actually runs our
    gateway, and must NEVER flag profile units
    (``hermes-gateway-<profile>.service``) or unrelated third-party services.
    """

    # Minimal ExecStart that looks like our gateway
    _OUR_UNIT_TEXT = (
        "[Unit]\nDescription=Hermes Gateway\n[Service]\n"
        "ExecStart=/usr/bin/python -m hermes_cli.main gateway run --replace\n"
    )

    @staticmethod
    def _setup_search_paths(tmp_path, monkeypatch):
        """Redirect the legacy search to user_dir + system_dir under tmp_path."""
        user_dir = tmp_path / "user"
        system_dir = tmp_path / "system"
        user_dir.mkdir()
        system_dir.mkdir()
        monkeypatch.setattr(
            gateway_cli,
            "_legacy_unit_search_paths",
            lambda: [(False, user_dir), (True, system_dir)],
        )
        return user_dir, system_dir

    def test_detects_both_scopes_simultaneously(self, tmp_path, monkeypatch):
        """When a user has BOTH user-scope and system-scope legacy units,
        both are reported so the migration step can remove them together."""
        user_dir, system_dir = self._setup_search_paths(tmp_path, monkeypatch)
        (user_dir / "hermes.service").write_text(self._OUR_UNIT_TEXT, encoding="utf-8")
        (system_dir / "hermes.service").write_text(self._OUR_UNIT_TEXT, encoding="utf-8")

        results = gateway_cli._find_legacy_hermes_units()

        scopes = sorted(is_system for _, _, is_system in results)
        assert scopes == [False, True]

    def test_accepts_alternate_execstart_formats(self, tmp_path, monkeypatch):
        """Older installs may have used different python invocations.

        ExecStart variants we've seen in the wild:
          - python -m hermes_cli.main gateway run
          - python path/to/hermes_cli/main.py gateway run
          - hermes gateway run   (direct binary)
          - python path/to/gateway/run.py
        """
        user_dir, _ = self._setup_search_paths(tmp_path, monkeypatch)
        variants = [
            "ExecStart=/venv/bin/python -m hermes_cli.main gateway run --replace",
            "ExecStart=/venv/bin/python /opt/hermes/hermes_cli/main.py gateway run",
            "ExecStart=/usr/local/bin/hermes gateway run --replace",
            "ExecStart=/venv/bin/python /opt/hermes/gateway/run.py",
        ]
        for i, execstart in enumerate(variants):
            # Test each variant fresh (every variant ships the same unit name)
            (user_dir / "hermes.service").write_text(
                f"[Unit]\nDescription=Old Hermes\n[Service]\n{execstart}\n",
                encoding="utf-8",
            )
            results = gateway_cli._find_legacy_hermes_units()
            assert len(results) == 1, f"Variant {i} not detected: {execstart!r}"



class TestRemoveLegacyHermesUnits:
    """Tests for remove_legacy_hermes_units (the migration action)."""

    _OUR_UNIT_TEXT = (
        "[Unit]\nDescription=Hermes Gateway\n[Service]\n"
        "ExecStart=/usr/bin/python -m hermes_cli.main gateway run --replace\n"
    )

    @staticmethod
    def _setup(tmp_path, monkeypatch, as_root=False):
        user_dir = tmp_path / "user"
        system_dir = tmp_path / "system"
        user_dir.mkdir()
        system_dir.mkdir()
        monkeypatch.setattr(
            gateway_cli,
            "_legacy_unit_search_paths",
            lambda: [(False, user_dir), (True, system_dir)],
        )
        # Mock systemctl — return success for everything
        systemctl_calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            systemctl_calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(gateway_cli.subprocess, "run", fake_run)
        monkeypatch.setattr(gateway_cli.os, "geteuid", lambda: 0 if as_root else 1000)
        return user_dir, system_dir, systemctl_calls

    def test_removes_user_scope_legacy_unit(self, tmp_path, monkeypatch, capsys):
        user_dir, _, calls = self._setup(tmp_path, monkeypatch)
        legacy = user_dir / "hermes.service"
        legacy.write_text(self._OUR_UNIT_TEXT, encoding="utf-8")

        removed, remaining = gateway_cli.remove_legacy_hermes_units(interactive=False)

        assert removed == 1
        assert remaining == []
        assert not legacy.exists()
        # Must have invoked stop → disable → daemon-reload on user scope
        cmds_joined = [" ".join(c) for c in calls]
        assert any("--user stop hermes.service" in c for c in cmds_joined)
        assert any("--user disable hermes.service" in c for c in cmds_joined)
        assert any("--user daemon-reload" in c for c in cmds_joined)

    def test_does_not_touch_profile_units_during_migration(
        self, tmp_path, monkeypatch, capsys
    ):
        """Teknium's constraint: profile units (hermes-gateway-coder.service)
        must survive a migration call, even if we somehow include them in the
        search dir."""
        user_dir, _, _ = self._setup(tmp_path, monkeypatch, as_root=True)
        profile_unit = user_dir / "hermes-gateway-coder.service"
        profile_unit.write_text(self._OUR_UNIT_TEXT, encoding="utf-8")
        default_unit = user_dir / "hermes-gateway.service"
        default_unit.write_text(self._OUR_UNIT_TEXT, encoding="utf-8")

        removed, remaining = gateway_cli.remove_legacy_hermes_units(interactive=False)

        assert removed == 0
        assert remaining == []
        # Both the profile unit and the current default unit must survive
        assert profile_unit.exists()
        assert default_unit.exists()


class TestMigrateLegacyCommand:
    """Tests for the `hermes gateway migrate-legacy` subcommand dispatch."""


    def test_gateway_command_migrate_legacy_dispatches(
        self, tmp_path, monkeypatch, capsys
    ):
        """gateway_command(args) with subcmd='migrate-legacy' calls the helper."""
        called = {}

        def fake_remove(interactive=True, dry_run=False):
            called["interactive"] = interactive
            called["dry_run"] = dry_run
            return 0, []

        monkeypatch.setattr(gateway_cli, "remove_legacy_hermes_units", fake_remove)
        monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: True)

        args = SimpleNamespace(
            gateway_command="migrate-legacy", dry_run=False, yes=True
        )
        gateway_cli.gateway_command(args)

        assert called == {"interactive": False, "dry_run": False}


class TestSystemdInstallOffersLegacyRemoval:
    """Verify that systemd_install prompts to remove legacy units first."""

    def test_install_offers_removal_when_legacy_detected(
        self, tmp_path, monkeypatch, capsys
    ):
        """When legacy units exist, install flow should call the removal
        helper before writing the new unit."""
        remove_called = {}

        def fake_remove(interactive=True, dry_run=False):
            remove_called["invoked"] = True
            remove_called["interactive"] = interactive
            return 1, []

        # has_legacy_hermes_units must return True
        monkeypatch.setattr(gateway_cli, "has_legacy_hermes_units", lambda: True)
        monkeypatch.setattr(gateway_cli, "remove_legacy_hermes_units", fake_remove)
        monkeypatch.setattr(gateway_cli, "print_legacy_unit_warning", lambda: None)
        # Answer "yes" to the legacy-removal prompt
        monkeypatch.setattr(gateway_cli, "prompt_yes_no", lambda *a, **k: True)

        # Mock the rest of the install flow
        unit_path = tmp_path / "hermes-gateway.service"
        monkeypatch.setattr(
            gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path
        )
        monkeypatch.setattr(
            gateway_cli,
            "generate_systemd_unit",
            lambda system=False, run_as_user=None: "unit text\n",
        )
        monkeypatch.setattr(
            gateway_cli.subprocess,
            "run",
            lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        monkeypatch.setattr(gateway_cli, "_ensure_linger_enabled", lambda: None)

        gateway_cli.systemd_install()

        assert remove_called.get("invoked") is True
        assert remove_called.get("interactive") is False  # prompted elsewhere

    def test_install_declines_legacy_removal_when_user_says_no(
        self, tmp_path, monkeypatch
    ):
        """When legacy units exist and user declines, install still proceeds
        but doesn't touch them."""
        remove_called = {"invoked": False}

        def fake_remove(interactive=True, dry_run=False):
            remove_called["invoked"] = True
            return 0, []

        monkeypatch.setattr(gateway_cli, "has_legacy_hermes_units", lambda: True)
        monkeypatch.setattr(gateway_cli, "remove_legacy_hermes_units", fake_remove)
        monkeypatch.setattr(gateway_cli, "print_legacy_unit_warning", lambda: None)
        monkeypatch.setattr(gateway_cli, "prompt_yes_no", lambda *a, **k: False)

        unit_path = tmp_path / "hermes-gateway.service"
        monkeypatch.setattr(
            gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path
        )
        monkeypatch.setattr(
            gateway_cli,
            "generate_systemd_unit",
            lambda system=False, run_as_user=None: "unit text\n",
        )
        monkeypatch.setattr(
            gateway_cli.subprocess,
            "run",
            lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        monkeypatch.setattr(gateway_cli, "_ensure_linger_enabled", lambda: None)

        gateway_cli.systemd_install()

        # Helper must NOT have been called
        assert remove_called["invoked"] is False
        # New unit should still have been written
        assert unit_path.exists()
        assert unit_path.read_text() == "unit text\n"

    def test_install_skips_legacy_check_when_none_present(
        self, tmp_path, monkeypatch
    ):
        """No legacy → no prompt, no helper call."""
        prompt_called = {"count": 0}

        def counting_prompt(*a, **k):
            prompt_called["count"] += 1
            return True

        remove_called = {"invoked": False}

        def fake_remove(interactive=True, dry_run=False):
            remove_called["invoked"] = True
            return 0, []

        monkeypatch.setattr(gateway_cli, "has_legacy_hermes_units", lambda: False)
        monkeypatch.setattr(gateway_cli, "remove_legacy_hermes_units", fake_remove)
        monkeypatch.setattr(gateway_cli, "prompt_yes_no", counting_prompt)

        unit_path = tmp_path / "hermes-gateway.service"
        monkeypatch.setattr(
            gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path
        )
        monkeypatch.setattr(
            gateway_cli,
            "generate_systemd_unit",
            lambda system=False, run_as_user=None: "unit text\n",
        )
        monkeypatch.setattr(
            gateway_cli.subprocess,
            "run",
            lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        monkeypatch.setattr(gateway_cli, "_ensure_linger_enabled", lambda: None)

        gateway_cli.systemd_install()

        assert prompt_called["count"] == 0
        assert remove_called["invoked"] is False


class TestSystemScopeRequiresRootError:
    """Tests for the SystemScopeRequiresRootError replacement of sys.exit(1).

    Before this change, ``_require_root_for_system_service`` called
    ``sys.exit(1)`` when non-root code tried a system-scope systemd
    operation. The wizard's ``except Exception`` guards don't catch
    ``SystemExit`` (it's a ``BaseException`` subclass), so the user was
    dumped at a bare shell prompt mid-setup. The fix raises a typed
    exception instead, which the wizard intercepts and handles with
    actionable remediation.
    """

    def test_require_root_raises_when_non_root(self, monkeypatch):
        monkeypatch.setattr(gateway_cli.os, "geteuid", lambda: 1000)

        with pytest.raises(gateway_cli.SystemScopeRequiresRootError) as excinfo:
            gateway_cli._require_root_for_system_service("start")

        assert "start" in excinfo.value.args[0]
        assert excinfo.value.args[1] == "start"
        # str(e) renders only the message, not the tuple repr, so that
        # wizard format strings like f"Failed: {e}" print cleanly.
        assert str(excinfo.value) == excinfo.value.args[0]

    def test_error_is_runtime_error_subclass(self):
        """Wizards use ``except Exception`` guards — the error must be a
        ``RuntimeError`` (catchable by ``Exception``), NOT a ``SystemExit``
        (``BaseException``), so the wizard can recover from it.
        """
        err = gateway_cli.SystemScopeRequiresRootError("msg", "start")
        assert isinstance(err, RuntimeError)
        assert isinstance(err, Exception)
        assert not isinstance(err, SystemExit)


class TestSystemScopeWizardPreCheck:
    """Tests for _system_scope_wizard_would_need_root — the guard the
    wizard uses to detect the dead-end BEFORE prompting the user to start
    a service that will fail without sudo.
    """

    @staticmethod
    def _setup_units(tmp_path, monkeypatch, system_present: bool, user_present: bool):
        sys_dir = tmp_path / "sys"
        usr_dir = tmp_path / "usr"
        sys_dir.mkdir()
        usr_dir.mkdir()
        if system_present:
            (sys_dir / "hermes-gateway.service").write_text("[Unit]\n")
        if user_present:
            (usr_dir / "hermes-gateway.service").write_text("[Unit]\n")
        monkeypatch.setattr(
            gateway_cli,
            "get_systemd_unit_path",
            lambda system=False: (sys_dir if system else usr_dir) / "hermes-gateway.service",
        )

    def test_non_root_with_only_system_unit_returns_true(self, tmp_path, monkeypatch):
        self._setup_units(tmp_path, monkeypatch, system_present=True, user_present=False)
        monkeypatch.setattr(gateway_cli.os, "geteuid", lambda: 1000)

        assert gateway_cli._system_scope_wizard_would_need_root() is True

    def test_non_root_with_explicit_system_arg_returns_true(self, tmp_path, monkeypatch):
        # Caller passed system=True explicitly (e.g. ``hermes gateway start --system``).
        self._setup_units(tmp_path, monkeypatch, system_present=False, user_present=False)
        monkeypatch.setattr(gateway_cli.os, "geteuid", lambda: 1000)

        assert gateway_cli._system_scope_wizard_would_need_root(system=True) is True


class TestGatewayCommandCatchesSystemScopeError:
    """The direct CLI path (``hermes gateway start --system`` etc.) must
    still exit 1 with a clean message when non-root. The top-level
    ``gateway_command`` catches ``SystemScopeRequiresRootError`` and
    converts it back to ``sys.exit(1)``, preserving existing CLI behavior.
    """

    def test_non_root_system_start_exits_one_with_clean_message(self, tmp_path, monkeypatch, capsys):
        sys_dir = tmp_path / "sys"
        usr_dir = tmp_path / "usr"
        sys_dir.mkdir()
        usr_dir.mkdir()
        (sys_dir / "hermes-gateway.service").write_text("[Unit]\n")
        monkeypatch.setattr(
            gateway_cli,
            "get_systemd_unit_path",
            lambda system=False: (sys_dir if system else usr_dir) / "hermes-gateway.service",
        )
        monkeypatch.setattr(gateway_cli.os, "geteuid", lambda: 1000)
        monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: True)
        monkeypatch.setattr(gateway_cli, "kill_gateway_processes", lambda **kw: 0)

        args = SimpleNamespace(gateway_command="start", system=True, all=False)

        with pytest.raises(SystemExit) as excinfo:
            gateway_cli.gateway_command(args)

        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        # Renders the message, NOT the ``('msg', 'action')`` tuple repr
        assert "requires root" in out
        assert "('" not in out  # no tuple repr leaking through


class TestServiceWorkingDirIsStable:
    """The gateway service must anchor WorkingDirectory at a stable path
    (HERMES_HOME), never the source checkout / worktree, so a relocated or
    deleted checkout can't crash-loop the unit on CHDIR (status=200).
    """

    def test_user_unit_workingdirectory_is_hermes_home_not_checkout(self, tmp_path, monkeypatch):
        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setattr(gateway_cli, "get_hermes_home", lambda: home)
        unit = gateway_cli.generate_systemd_unit(system=False)
        wd = [l for l in unit.splitlines() if l.startswith("WorkingDirectory=")]
        assert wd, "unit has no WorkingDirectory line"
        value = wd[0].split("=", 1)[1]
        assert Path(value).resolve() == home.resolve()
        # The bug class: never pin cwd inside a transient worktree checkout.
        assert "/.worktrees/" not in value



class TestServiceTakeoverGovernance:
    """Supervised service definitions must never arm ``--replace`` takeover.

    Regression for #79048: two launchd-supervised profile gateways sharing
    one platform token (e.g. the same Discord bot) entered an endless
    mutual-eviction loop because the generated plist armed ``--replace`` on
    every KeepAlive respawn: each revived process was authorized to terminate
    the sibling holding the shared token, and launchd immediately revived the
    victim. The systemd unit already runs ``gateway run`` without
    ``--replace``; the launchd plist must match so a supervised restart can
    never evict a legitimate cross-profile lock holder. Bounded replacement
    stays the lifecycle commands' job (kickstart -k / drain / bootout).
    """



    def test_systemd_unit_does_not_arm_takeover(self, tmp_path, monkeypatch):
        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setattr(gateway_cli, "get_hermes_home", lambda: home)
        unit = gateway_cli.generate_systemd_unit(system=False)
        exec_starts = [l for l in unit.splitlines() if l.startswith("ExecStart=")]
        assert exec_starts, "unit has no ExecStart line"
        # The safe service posture the launchd plist now mirrors.
        assert "--replace" not in exec_starts[0]








class TestTimeoutStopSecCoversCronFloor:
    """#94759: TimeoutStopSec must cover the FULL stop budget.

    resolve_cron_drain_budget() can hold the shutdown drain for
    agent.cron_drain_timeout plus CRON_DRAIN_CLEANUP_RESERVE_S on top of
    (as a max of) the restart drain, so the unit leash is sized from
    max(restart_drain, cron_floor) + 30 — not the restart drain alone."""

    def _unit_with_config(self, tmp_path, monkeypatch, config_yaml, env=None):
        hermes = tmp_path / "home" / ".hermes"
        hermes.mkdir(parents=True)
        (hermes / "config.yaml").write_text(config_yaml, encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(hermes))
        monkeypatch.delenv("HERMES_RESTART_DRAIN_TIMEOUT", raising=False)
        monkeypatch.delenv("HERMES_CRON_DRAIN_TIMEOUT", raising=False)
        monkeypatch.setattr(gateway_cli.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(
            gateway_cli, "_build_user_local_paths", lambda home, existing: []
        )
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        return gateway_cli.generate_systemd_unit(system=False)


    def test_restart_drain_still_dominates_when_larger(self, tmp_path, monkeypatch):
        unit = self._unit_with_config(
            tmp_path,
            monkeypatch,
            "agent:\n  restart_drain_timeout: 60\n",
        )
        # An explicit restart drain above the default cron floor keeps the old
        # formula's result — no regression for restart-drain-dominated installs.
        assert "TimeoutStopSec=90" in unit  # 60s drain + 30s cleanup: same as the pre-cron formula


class TestUnitAnchoredServiceIdentity:
    """The installed ``hermes-gateway.service`` owns the bare name: under ``sudo`` the naming basis moves
    mid-command when ``_sync_hermes_home_from_systemd_unit()`` adopts the unit's HERMES_HOME (#108674).

    ``platforms("linux")`` because ``_bare_unit_pinned_home()`` is Linux- and root-gated on purpose: a systemd unit
    is not an identity authority for launchd labels, Windows tasks, or s6 slots, which share the same
    resolver, and only an elevated process operates the system unit.
    """

    @pytest.mark.platforms("linux")
    def test_home_not_pinned_by_unit_keeps_its_suffix(self, tmp_path, monkeypatch):
        alice_home = tmp_path / "alice" / ".hermes"
        alice_home.mkdir(parents=True)
        bob_home = tmp_path / "bob" / ".hermes"
        bob_home.mkdir(parents=True)
        root_home = tmp_path / "root" / ".hermes"
        root_home.mkdir(parents=True)
        unit_dir = tmp_path / "systemd"
        unit_dir.mkdir()
        (unit_dir / f"{gateway_cli._SERVICE_BASE}.service").write_text(
            f'[Service]\nEnvironment="HERMES_HOME={alice_home}"\n', encoding="utf-8"
        )
        monkeypatch.setattr(gateway_cli, "_SYSTEM_UNIT_DIR", unit_dir)
        monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home", lambda: root_home)
        monkeypatch.setenv("HERMES_HOME", str(bob_home))
        name = gateway_cli.get_service_name()
        assert name != gateway_cli._SERVICE_BASE
        assert name.startswith(gateway_cli._SERVICE_BASE + "-")

    @pytest.mark.platforms("linux")
    def test_unprivileged_profile_command_ignores_the_system_unit(self, tmp_path, monkeypatch):
        """A bare system unit pinning ``profiles/<name>`` must not alias that profile onto the user's
        default unit when an unprivileged user-scope command resolves the name."""
        profile_home = tmp_path / "alice" / ".hermes" / "profiles" / "kimi"
        profile_home.mkdir(parents=True)
        unit_dir = tmp_path / "systemd"
        unit_dir.mkdir()
        (unit_dir / f"{gateway_cli._SERVICE_BASE}.service").write_text(
            f'[Service]\nEnvironment="HERMES_HOME={profile_home}"\n', encoding="utf-8"
        )
        monkeypatch.setattr(gateway_cli, "_SYSTEM_UNIT_DIR", unit_dir)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "alice")
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        monkeypatch.setenv("HERMES_HOME", str(profile_home))
        assert gateway_cli.get_service_name() == "hermes-gateway-kimi"

    @pytest.mark.platforms("linux")
    def test_bare_unit_pinning_a_named_profile_home_keeps_the_bare_name(self, tmp_path, monkeypatch):
        """``sudo ... install --system`` names the unit from root's default but pins the invoking user's
        remapped home, so the BARE unit legitimately carries a ``profiles/<name>`` home. The unit-pinned
        check therefore has to win over the profile branch, which would answer ``-kimi`` for a unit that
        was installed bare."""
        profile_home = tmp_path / "alice" / ".hermes" / "profiles" / "kimi"
        profile_home.mkdir(parents=True)
        root_home = tmp_path / "root" / ".hermes"
        root_home.mkdir(parents=True)
        unit_dir = tmp_path / "systemd"
        unit_dir.mkdir()
        unit_path = unit_dir / f"{gateway_cli._SERVICE_BASE}.service"
        unit_path.write_text(f'[Service]\nEnvironment="HERMES_HOME={profile_home}"\n', encoding="utf-8")
        monkeypatch.setattr(gateway_cli, "_SYSTEM_UNIT_DIR", unit_dir)
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home", lambda: root_home)
        monkeypatch.setenv("HERMES_HOME", str(profile_home))
        assert gateway_cli.get_service_name() == gateway_cli._SERVICE_BASE
        # The profile branch, consulted against the home that owns the profile, would have answered
        # with the readable suffix -- which is why the unit-pinned check has to be evaluated first.
        assert gateway_cli._profile_name_from_home(profile_home, profile_home.parent.parent) == profile_home.name

    @pytest.mark.platforms("linux")
    def test_real_unit_sync_keeps_the_name_it_validated(self, tmp_path, monkeypatch):
        """Drive the production sync instead of simulating the adoption with setenv: the name resolved
        before ``_sync_hermes_home_from_systemd_unit()`` must survive the mutation it performs."""
        alice_home = tmp_path / "alice" / ".hermes"
        alice_home.mkdir(parents=True)
        root_home = tmp_path / "root" / ".hermes"
        root_home.mkdir(parents=True)
        unit_dir = tmp_path / "systemd"
        unit_dir.mkdir()
        (unit_dir / f"{gateway_cli._SERVICE_BASE}.service").write_text(
            f'[Service]\nEnvironment="HERMES_HOME={alice_home}"\n', encoding="utf-8"
        )
        monkeypatch.setattr(gateway_cli, "_SYSTEM_UNIT_DIR", unit_dir)
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home", lambda: root_home)
        monkeypatch.delenv("HERMES_HOME", raising=False)

        pre_sync_name = gateway_cli.get_service_name()
        gateway_cli._sync_hermes_home_from_systemd_unit(system=True)

        assert os.environ["HERMES_HOME"] == str(alice_home)  # the sync really ran
        assert gateway_cli.get_service_name() == pre_sync_name
