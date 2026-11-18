"""Linux liveness fallback used by the detached gateway watcher."""

import errno

import pytest

from hermes_cli import process_liveness as compat


@pytest.fixture(autouse=True)
def _moa_caches_isolated():
    pass


@pytest.fixture(autouse=True)
def _isolate_computer_use_approval_state():
    pass


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("probe_error, expected", [
    (None, True),
    (PermissionError(errno.EPERM, "denied"), True),
    (ProcessLookupError(errno.ESRCH, "gone"), False),
])
def test_pid_exists_uses_signal_zero(monkeypatch, probe_error, expected):
    monkeypatch.setattr(compat, "posix_is_zombie", lambda pid: False)
    calls = []

    def kill(pid, signal):
        calls.append((pid, signal))
        if probe_error:
            raise probe_error

    monkeypatch.setattr(compat.os, "kill", kill)
    assert compat.pid_exists_stdlib(123) is expected
    assert calls == [(123, 0)]


@pytest.mark.platforms("linux")
def test_zombie_does_not_probe_signal(monkeypatch):
    monkeypatch.setattr(compat, "posix_is_zombie", lambda pid: True)
    def unexpected(*args):
        raise AssertionError("zombie should not be signalled")
    monkeypatch.setattr(compat.os, "kill", unexpected)
    assert compat.pid_exists_stdlib(123) is False
