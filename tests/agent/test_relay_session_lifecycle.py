"""Relay session lifetime, scope fallback, and working-directory projection."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from agent import relay_runtime
from agent.relay_runtime import (
    RelayRuntime,
    RelaySessionCoordinator,
)


def _run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    """Run a Python snippet in the repo root (not tests/) in a fresh process."""
    repo_root = Path(__file__).parent.parent.parent
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=30,
        check=False,
    )


class _ScopeHandle:
    def __init__(self, name: str, seq: int) -> None:
        self.name = name
        self.seq = seq


class _FakeScopeModule:
    def __init__(self, wedge_pop: threading.Event | None = None) -> None:
        self._wedge = wedge_pop
        self._seq = 0
        self.pushes: list[dict[str, Any]] = []  # {name, metadata, handle, input}
        self.pops: list[_ScopeHandle] = []

    def push(self, name: str, scope_type: Any, **kwargs: Any) -> _ScopeHandle:
        self._seq += 1
        self.pushes.append(
            {
                "name": name,
                "metadata": dict(kwargs.get("metadata") or {}),
                "parent": kwargs.get("handle"),
                "input": dict(kwargs.get("input") or {}),
                "seq": self._seq,
            }
        )
        return _ScopeHandle(name, self._seq)

    def pop(self, handle: _ScopeHandle, **kwargs: Any) -> None:
        if self._wedge is not None:
            self._wedge.wait()
        self.pops.append(handle)

    def event(self, *args: Any, **kwargs: Any) -> None:
        return None


class _FakeSubscribers:
    def __init__(self) -> None:
        self.flushed = 0

    def flush(self) -> None:
        self.flushed += 1


class _FakeScopeType:
    Function = "function"
    Agent = "agent"


class _FakePluginModule:
    def report(self) -> None:
        return None


class _FakeRelay:
    def __init__(self, wedge_pop: threading.Event | None = None) -> None:
        self.scope = _FakeScopeModule(wedge_pop)
        self.subscribers = _FakeSubscribers()
        self.ScopeType = _FakeScopeType()
        self.plugin = _FakePluginModule()

    def get_scope_stack(self) -> None:
        return None


_LIVE: list[tuple[RelayRuntime, _FakeRelay]] = []


def _make_runtime(fake: _FakeRelay) -> RelayRuntime:
    runtime = RelayRuntime(relay=fake, profile_key="/tmp/test-profile")
    _LIVE.append((runtime, fake))
    return runtime


@pytest.fixture(autouse=True)
def _teardown_runtimes():
    """Unwedge and drain every runtime so exit paths never replay wedged ops."""
    yield
    for runtime, fake in _LIVE:
        if fake.scope._wedge is not None:
            fake.scope._wedge.set()
        runtime.shutdown()
    _LIVE.clear()


@pytest.fixture(autouse=True)
def _fast_scope_timeout(monkeypatch):
    monkeypatch.setattr(relay_runtime, "SCOPE_OP_TIMEOUT", 1.0)


@pytest.fixture(autouse=True)
def _default_config(monkeypatch):
    """Do not inherit machine configuration."""
    monkeypatch.setattr(
        "hermes_cli.config_effective.load_user_config_effective", lambda *_a, **_k: {}
    )


@pytest.fixture()
def coordinator() -> RelaySessionCoordinator:
    return RelaySessionCoordinator()


class TestSessionScopeFallback:
    def test_push_runtime_error_does_not_double_push(self):
        """A RuntimeError raised by relay.scope.push inside the future is re-raised
        by future.result(); it must not be mistaken for executor refusal and retried."""
        fake = _FakeRelay()
        runtime = _make_runtime(fake)
        original_push = fake.scope.push
        calls: list = []

        def failing_push(*args, **kwargs):
            calls.append(args)
            raise RuntimeError("scope push failed")

        fake.scope.push = failing_push

        with pytest.raises(RuntimeError, match="scope push failed"):
            runtime.ensure_session({"session_id": "sess-rt"})
        assert len(calls) == 1
        # The failed open leaves no half-populated scope state: the session stays
        # registered with handle/context unset so a later ensure_session retries cleanly.
        session = runtime._sessions["sess-rt"]
        assert session.handle is None
        assert session.context is None

        fake.scope.push = original_push
        runtime.ensure_session({"session_id": "sess-rt"})
        assert len(_session_pushes(fake)) == 1
        assert session.handle is not None

    def test_executor_refusal_still_uses_sync_fallback(self, monkeypatch):
        """The intended lane: submit() refusing at interpreter shutdown pushes once,
        synchronously, via exit_fallback."""

        class _RefusingExecutor:
            def submit(self, *args, **kwargs):
                raise RuntimeError("cannot schedule new futures after shutdown")

        monkeypatch.setattr(
            relay_runtime, "_scope_op_executor", lambda: _RefusingExecutor()
        )
        fake = _FakeRelay()
        runtime = _make_runtime(fake)

        runtime.ensure_session({"session_id": "sess-ref"})
        assert len(_session_pushes(fake)) == 1


def _acquire(coordinator, runtime, session_id="sess-1", **kwargs):
    class _Registry:
        def for_profile(self, key):
            return runtime

    coordinator.registry = _Registry()
    coordinator._prepare_session = lambda host, ctx: None
    return coordinator.acquire_conversation(
        profile_key=runtime.profile_key,
        session_id=session_id,
        platform="test",
        **kwargs,
    )


def _session_pushes(fake):
    return [p for p in fake.scope.pushes if p["name"] == relay_runtime.SESSION_SCOPE]


def _run_turn(coordinator, lease, turn_id):
    turn = coordinator.begin_turn(lease, turn_id=turn_id, task_id=f"task-{turn_id}")
    coordinator.end_turn(turn, outcome="success")
    return turn


class TestTurnMetadata:
    def test_includes_request_metadata_without_overriding_runtime_fields(
        self, coordinator
    ):
        fake = _FakeRelay()
        runtime = _make_runtime(fake)
        lease = _acquire(coordinator, runtime)

        turn = coordinator.begin_turn(
            lease,
            turn_id="t1",
            task_id="task1",
            metadata={
                "request_id": "req-123",
                "context": {"tenant": "example"},
                relay_runtime.RUNTIME_INSTANCE_KEY: "caller-supplied",
            },
        )

        turn_metadata = [
            push
            for push in fake.scope.pushes
            if push["name"] == relay_runtime.TURN_SCOPE
        ][-1]["metadata"]
        assert turn_metadata["request_id"] == "req-123"
        assert turn_metadata["context"] == {"tenant": "example"}
        assert turn_metadata[relay_runtime.RUNTIME_INSTANCE_KEY] == runtime.runtime_id
        coordinator.end_turn(turn, outcome="success")


class TestSessionLifetime:
    def test_turns_share_one_session_scope(self, coordinator):
        fake = _FakeRelay()
        runtime = _make_runtime(fake)
        lease = _acquire(coordinator, runtime)
        assert lease.session is not None

        for i in range(5):
            _run_turn(coordinator, lease, f"t{i}")

        assert len(_session_pushes(fake)) == 1


class TestCwdProjection:
    def test_distinct_session_and_turn_cwds(self, coordinator):
        fake = _FakeRelay()
        runtime = _make_runtime(fake)
        lease = _acquire(
            coordinator, runtime,
            session_cwd="/workspace/session", turn_cwd="/workspace/task",
        )

        turn = coordinator.begin_turn(lease, turn_id="t1", task_id="task1")

        assert _session_pushes(fake)[-1]["input"] == {"cwd": "/workspace/session"}
        assert fake.scope.pushes[-1]["input"] == {"cwd": "/workspace/task"}
        coordinator.end_turn(turn, outcome="success")

        lease = _acquire(
            coordinator, runtime,
            session_cwd="/workspace/moved", turn_cwd="/workspace/next-task",
        )
        turn = coordinator.begin_turn(lease, turn_id="t2", task_id="task2")

        assert lease.session.cwd == "/workspace/moved"
        assert fake.scope.pushes[-1]["input"] == {"cwd": "/workspace/next-task"}
        coordinator.end_turn(turn, outcome="success")

        lease = _acquire(coordinator, runtime, session_cwd="", turn_cwd="")
        turn = coordinator.begin_turn(lease, turn_id="t3", task_id="task3")

        assert lease.session.cwd == ""
        assert fake.scope.pushes[-1]["input"] == {}
        coordinator.end_turn(turn, outcome="success")



class TestGatewayRunStaysUnimported:
    """Guard against re-importing gateway.run from a non-gateway host.

    Importing relay_runtime must NEVER trigger ``gateway.run`` — its
    import-time env setup (_HERMES_GATEWAY, HERMES_QUIET, TERMINAL_CWD := home)
    hangs CLI approvals (#87183) and runs ``hermes -z`` in $HOME (#95577). A
    monkeypatch can't catch a refactor re-adding the import, so this runs the
    real path in a fresh process with those vars unset.
    """

    def test_relay_runtime_never_imports_gateway_run(self, monkeypatch) -> None:
        for var in ("TERMINAL_CWD", "HERMES_QUIET", "_HERMES_GATEWAY"):
            monkeypatch.delenv(var, raising=False)
        result = _run_isolated(
            """
import os
import sys

import agent.relay_runtime

leaked = {v: os.environ[v] for v in ("TERMINAL_CWD", "HERMES_QUIET", "_HERMES_GATEWAY") if v in os.environ}
print("gateway.run imported:", "gateway.run" in sys.modules, "leaked env:", leaked)
sys.exit(1 if "gateway.run" in sys.modules or leaked else 0)
"""
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
