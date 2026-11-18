"""Deadlock-safe Linux probe capture, cleanup and captured PID identities.

Real child-process cases cover pipe inheritance and process-group semantics;
mocked contract cases cover spawn options, failure handling and PID validation.
Descendant-survival cases also live in ``test_git_probe_tree_kill.py``.
"""

import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from hermes_cli._subprocess_compat import bounded_git_probe, bounded_probe_run
from hermes_cli import _subprocess_compat as compat

_PY = sys.executable


def test_success_returns_completed_process():
    result = bounded_probe_run([_PY, "-c", "print('ok'); import sys; sys.exit(0)"], timeout=30)
    assert result is not None
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_nonzero_exit_is_returned_not_swallowed():
    """Unlike bounded_git_probe, callers see the real returncode — the gateway
    scan branches on ``returncode != 0`` to trip the wmic→powershell fallback."""
    result = bounded_probe_run([_PY, "-c", "print('partial'); import sys; sys.exit(3)"], timeout=30)
    assert result is not None
    assert result.returncode == 3
    assert result.stdout.strip() == "partial"


def test_spawn_failure_returns_none():
    result = bounded_probe_run(["definitely-not-a-real-binary-87134"], timeout=5)
    assert result is None


def test_timeout_returns_none_within_bounded_time():
    """A child that sleeps past the timeout must produce ``None`` promptly —
    timeout + tree-kill + 1s bounded drain, not an unbounded join."""
    start = time.monotonic()
    result = bounded_probe_run(
        [_PY, "-c", "import time; time.sleep(300)"],
        timeout=1.0,
    )
    elapsed = time.monotonic() - start
    assert result is None
    # 1s timeout + tree-kill + 1s drain + slack.  The pre-fix failure mode is
    # an indefinite hang, so any bound proves the property; keep it loose for
    # slow CI runners.
    assert elapsed < 30


def test_decode_errors_configurable():
    """The process scans pass errors='ignore' (wmic emits system code page);
    undecodable bytes must not raise or None out stdout (#17049 class)."""
    result = bounded_probe_run(
        [_PY, "-c", "import sys; sys.stdout.buffer.write(b'ok\\xff\\xfe')"],
        timeout=30,
        errors="ignore",
    )
    assert result is not None
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_stdin_is_devnull_not_inherited():
    """A probe must never block reading the caller's stdin."""
    result = bounded_probe_run(
        [_PY, "-c", "import sys; print(repr(sys.stdin.read()))"],
        timeout=30,
    )
    assert result is not None
    assert result.returncode == 0
    assert result.stdout.strip() == "''"


def test_bounded_git_probe_delegates_same_contract():
    """The historical git-probe wrapper keeps its exact contract on top of
    bounded_probe_run: stripped stdout on rc==0, '' on any failure."""
    assert bounded_git_probe([_PY, "-c", "print('  x  ')"], timeout=30) == "x"
    assert bounded_git_probe([_PY, "-c", "import sys; sys.exit(1)"], timeout=30) == ""
    assert bounded_git_probe(["definitely-not-a-real-binary-87134"], timeout=5) == ""


@pytest.mark.platforms("posix")  # POSIX process-group check
def test_posix_child_gets_own_process_group():
    """POSIX spawns use process_group=0 so timeout cleanup can killpg the
    whole tree (same contract bounded_git_probe had)."""
    result = bounded_probe_run(
        [_PY, "-c", "import os; print(os.getpgid(0) == os.getpid())"],
        timeout=30,
    )
    assert result is not None
    assert result.stdout.strip() == "True"


@pytest.mark.parametrize("input_value", [None, "payload"])
@pytest.mark.parametrize("returncode", [0, 3])
def test_mocked_probe_preserves_spawn_and_output_contract(monkeypatch, tmp_path, input_value, returncode):
    proc = Mock(returncode=returncode)
    proc.communicate.return_value = ("output\n", "diagnostic\n")
    spawn = Mock(return_value=proc)
    monkeypatch.setattr(compat.subprocess, "Popen", spawn)
    env = {"SENTINEL": "kept"}
    result = bounded_probe_run(["probe", "--status"], timeout=2, errors="ignore",
                               env=env, cwd=tmp_path, input=input_value)
    assert result is not None
    assert (result.args, result.returncode, result.stdout, result.stderr) == (
        ["probe", "--status"], returncode, "output\n", "diagnostic\n",
    )
    spawn.assert_called_once_with(
        ["probe", "--status"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL if input_value is None else subprocess.PIPE,
        text=True, encoding="utf-8", errors="ignore", env=env, cwd=tmp_path, process_group=0,
    )
    proc.communicate.assert_called_once_with(input=input_value, timeout=2)
    proc.kill.assert_not_called()


@pytest.mark.parametrize("raise_on_failure", [False, True])
def test_mocked_probe_spawn_failure_policy(monkeypatch, raise_on_failure):
    monkeypatch.setattr(compat.subprocess, "Popen", Mock(side_effect=FileNotFoundError("missing probe")))
    if raise_on_failure:
        with pytest.raises(FileNotFoundError, match="missing probe"):
            bounded_probe_run(["probe"], timeout=2, raise_on_spawn_failure=True)
    else:
        assert bounded_probe_run(["probe"], timeout=2) is None


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("probe", 2), ValueError("closed pipe")])
def test_mocked_probe_communication_failure_is_drained_bounded(monkeypatch, failure):
    proc = Mock()
    proc.communicate.side_effect = failure
    monkeypatch.setattr(compat.subprocess, "Popen", Mock(return_value=proc))
    drain = Mock()
    monkeypatch.setattr(compat, "kill_and_drain", drain)
    assert bounded_probe_run(["probe"], timeout=2) is None
    drain.assert_called_once_with(proc, 1)


@pytest.mark.parametrize("pid", [-1, 0, "not-a-pid", True])
def test_mocked_pid_validation_rejects_invalid_inputs(monkeypatch, pid):
    probe = Mock()
    monkeypatch.setattr(compat, "_process_start_time", probe)
    assert compat.pid_is_hermes(pid) is False
    probe.assert_not_called()


@pytest.mark.parametrize("current,accepted", [(17, True), (18, False), (None, False)])
def test_mocked_pid_validation_checks_captured_identity(monkeypatch, current, accepted):
    monkeypatch.setattr(compat, "_process_start_time", Mock(return_value=current))
    assert compat.pid_is_hermes(1234, expected_start_time=17) is accepted


def test_mocked_pid_validation_fails_closed_when_identity_is_unreadable(monkeypatch):
    monkeypatch.setattr(compat, "_process_start_time", Mock(side_effect=OSError("unreadable process")))
    assert compat.pid_is_hermes(1234, expected_start_time=17) is False


def test_mocked_pid_validation_without_fingerprint_does_not_probe(monkeypatch):
    probe = Mock()
    monkeypatch.setattr(compat, "_process_start_time", probe)
    assert compat.pid_is_hermes(1234) is True
    probe.assert_not_called()


@pytest.mark.parametrize("group", [4321, 4320])
def test_mocked_fallback_cleanup_only_signals_owned_group(monkeypatch, group):
    import signal

    proc = Mock(pid=4321)
    monkeypatch.setattr(compat.os, "getpgid", Mock(return_value=group))
    killpg = Mock()
    monkeypatch.setattr(compat.os, "killpg", killpg)
    compat._legacy_kill_process_tree(proc)
    if group == proc.pid:
        killpg.assert_called_once_with(group, signal.SIGKILL)
    else:
        killpg.assert_not_called()
    proc.kill.assert_called_once_with()
