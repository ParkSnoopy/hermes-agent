"""A stalled ``docker exec`` is one failed poll attempt, never the poll's verdict.

Driven without Docker: ``docker_exec_sh`` is replaced by a scripted stand-in and the poll's clock
is fake, so the timeout branches run on every host. The module lives outside ``tests/docker/`` on
purpose: default discovery skips that directory (it needs a daemon and an image), and these
stand-in tests need neither.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from tests.docker import conftest as docker_conftest


class _Clock:
    """``time`` stand-in: each probe attempt and each sleep advance the fake clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _scripted_exec(clock: _Clock, outcomes: list):
    def fake(container, command, *, user="hermes", timeout=30):
        clock.now += timeout if outcomes[0] == "stall" else 0.1
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if outcome == "stall":
            raise subprocess.TimeoutExpired(["docker", "exec"], timeout)
        return outcome
    return fake


def test_stalled_exec_is_retried_until_the_deadline(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(docker_conftest, "time", clock)
    ok = SimpleNamespace(returncode=0, stdout="ready", stderr="")
    monkeypatch.setattr(docker_conftest, "docker_exec_sh", _scripted_exec(clock, ["stall", ok]))
    assert docker_conftest.poll_container("c", "probe", deadline_s=30.0) == (True, "ready")

    clock.now = 0.0
    monkeypatch.setattr(docker_conftest, "docker_exec_sh", _scripted_exec(clock, ["stall"]))
    with pytest.raises(AssertionError, match="docker exec stalled"):
        docker_conftest.poll_container("c", "probe", deadline_s=12.0)
    assert clock.now <= 12.0  # each attempt gets only the time left, so the deadline holds


def test_stall_after_a_completed_probe_keeps_its_output(monkeypatch):
    """A late stall must not replace the last real probe output that positive callers print."""
    clock = _Clock()
    monkeypatch.setattr(docker_conftest, "time", clock)
    failed = SimpleNamespace(returncode=1, stdout="HOME=/root", stderr="")
    monkeypatch.setattr(docker_conftest, "docker_exec_sh", _scripted_exec(clock, [failed, "stall"]))
    assert docker_conftest.poll_container("c", "probe", deadline_s=12.0) == (False, "HOME=/root")


def test_sleep_between_failed_probes_stops_at_the_deadline(monkeypatch):
    """The pause after a failed probe never runs past the deadline the caller gave."""
    clock = _Clock()
    monkeypatch.setattr(docker_conftest, "time", clock)
    failed = SimpleNamespace(returncode=1, stdout="", stderr="")
    monkeypatch.setattr(docker_conftest, "docker_exec_sh", _scripted_exec(clock, [failed]))
    assert docker_conftest.poll_container("c", "probe", deadline_s=1.0) == (False, "")
    assert clock.now <= 1.0
