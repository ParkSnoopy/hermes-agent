"""Verify that the gateway admits, runs, and persists a native WebSocket turn."""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import pytest
from websockets.sync.client import connect

from tests.e2e.core.chaos._helpers import (
    hermetic_env, kill_tagged, new_tag, persisted_messages, python_exe, write_chaos_home)
from tests.fakes.fake_llm_provider import FakeLLMServer, Text, _handler_for
from tests.waiting import wait_until

# The gateway child is spawned and reaped by this module (fake HOME, no systemd bus in its env).
pytestmark = [pytest.mark.platforms("linux"),  # caller attribution reads /proc/net/tcp
              pytest.mark.spawns_gateway_lookalike]

BOOT_S = 180.0
TURN_S = 90.0
REPLY = "gateway-ownership-reply"


class _Model(FakeLLMServer):
    """Loopback model that records which process called it."""

    def __init__(self) -> None:
        super().__init__(self._main)
        self.procs: dict[str, subprocess.Popen] = {}
        self.callers: list[str] = []

        self._peer = threading.local()
        self._seen = threading.Condition()

    def start(self) -> None:
        model, base = self, _handler_for(self)

        class Handler(base):  # type: ignore[misc,valid-type]
            def do_POST(self) -> None:
                model._peer.port = self.client_address[1]
                super().do_POST()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="gateway-own-llm", daemon=True)
        self._thread.start()

    def _main(self, _record: dict[str, Any]) -> Text:
        with self._seen:
            self.callers.append(self._owner(self._peer.port))
            self._seen.notify_all()
        return Text(REPLY)

    def _owner(self, port: int) -> str:
        """Label of the started process (or a descendant) holding the client end of ``port``."""
        # The whole 4-tuple: Linux shares a connect()-assigned local port across remotes, so another
        # process's socket can carry the same local address and sort first.
        local, remote = f"0100007F:{port:04X}", f"0100007F:{self.port:04X}"
        rows = Path("/proc/net/tcp").read_text().splitlines()[1:]
        inode = next((f[9] for f in map(str.split, rows) if f[1] == local and f[2] == remote), None)
        for label, proc in self.procs.items():
            for pid in _tree(proc.pid):
                try:
                    fds = [os.readlink(f"/proc/{pid}/fd/{fd}") for fd in os.listdir(f"/proc/{pid}/fd")]
                except OSError:
                    continue
                if inode and f"socket:[{inode}]" in fds:
                    return label
        return f"unknown(port {port})"

    def wait_calls(self, n: int, what: str) -> str:
        deadline = time.monotonic() + TURN_S
        with self._seen:
            while len(self.callers) < n:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"{what}: the model saw {len(self.callers)} main calls, wanted {n}")
                self._seen.wait(left)
            return self.callers[n - 1]


def _tree(pid: int) -> list[int]:
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        for task in Path(f"/proc/{p}/task").glob("*/children"):
            try:
                todo += [int(c) for c in task.read_text().split()]
            except OSError:
                pass
    return out


def _poll(pred: Callable[[], Any], timeout: float, what: str, log: Path) -> Any:
    try:
        return wait_until(pred, timeout=timeout, interval=0.1, what=what)
    except AssertionError as exc:
        raise AssertionError(f"{exc}\n{log.read_text(errors='replace')[-4000:]}") from None


def _control(home: Path, verb: str, params: dict | None = None) -> dict:
    from gateway.control_socket import resolve_client_socket_path
    path = resolve_client_socket_path(home)
    if path is None:
        raise OSError("control socket absent")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(10)
        peer.connect(str(path))
        peer.sendall(json.dumps({"protocol": 1, "id": 1, "verb": verb, "params": params or {}}).encode() + b"\n")
        with peer.makefile("rb") as stream:
            reply = json.loads(stream.readline())
    assert reply.get("ok"), reply
    return reply["result"]


def _identify(home: Path) -> dict | None:
    try:
        desc = _control(home, "identify")
    except (OSError, ValueError, AssertionError):
        return None
    return desc if desc.get("state") == "ready" else None


def _rpc(ws, rid: int, method: str, params: dict) -> dict:
    ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    reply = _until(ws, lambda m: m.get("id") == rid and "method" not in m)
    assert "result" in reply, f"{method}: {reply}"
    return reply["result"]


def _until(ws, pred: Callable[[dict], bool]) -> dict:
    deadline = time.monotonic() + TURN_S
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no matching websocket message within {TURN_S}s")
        msg = json.loads(ws.recv(timeout=remaining))
        if pred(msg):
            return msg


def _admissions(db: Path, sid: str) -> list[tuple[str, str]]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10) as conn:
        return conn.execute("SELECT admission_id, status FROM session_admissions WHERE target_session_id=?",
                            (sid,)).fetchall()


def _replied(db: Path, sid: str) -> bool:
    return any(m["role"] == "assistant" and REPLY in (m["content"] or "") for m in persisted_messages(db, sid))


@pytest.fixture(scope="module")
def scenario(tmp_path_factory: pytest.TempPathFactory):
    root, tag, model = tmp_path_factory.mktemp("gateway-own"), new_tag(), _Model()
    model.start()
    home, hermes_home = write_chaos_home(
        root, model.base_url, stale_timeout=600, request_timeout=600,
        extra="auxiliary:\n  title_generation:\n    enabled: false\n")
    db, log = hermes_home / "state.db", root / "procs.log"
    env = hermetic_env(home, hermes_home, tag)
    for key in ("NOTIFY_SOCKET", "INVOCATION_ID", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        env.pop(key, None)
    # Host rendezvous stays inside the tmp root: the developer's live gateway is never seen.
    env.update(HERMES_GATEWAY_LOCK_DIR=str(root / "locks"), HERMES_GATEWAY_MAX_STARTS="0")
    out = open(log, "wb")

    def spawn(*argv: str, **extra: str) -> subprocess.Popen:
        return subprocess.Popen([python_exe(), "-m", "hermes_cli.main", *argv], cwd=str(home),
                                env={**env, **extra}, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT)
    try:
        gateway = model.procs["gateway"] = spawn("gateway", "run", "--quiet")
        desc = _poll(lambda: _identify(hermes_home) or (gateway.poll() is not None and {"exited": True}),
                     BOOT_S, "gateway identify ready", log)
        assert "exited" not in desc, f"gateway exited rc={gateway.returncode}\n{log.read_text(errors='replace')}"
        served = {Path(p["home"]).resolve() for p in desc.get("served_profiles", [])}
        assert hermes_home.resolve() in served, desc

        # A turn through the gateway's own /api/ws is admitted and runs in the gateway.
        grant = _control(hermes_home, "session-ticket", {"profile_id": str(hermes_home.resolve()),
                                                         "instance_id": desc["instance_id"], "purpose": "interactive"})
        url = desc["api_origin"].replace("http:", "ws:") + "/api/ws"
        with connect(url, subprotocols=["hermes-gateway-v1", "hermes-gateway-ticket." + grant["ticket"]],
                     open_timeout=60, max_size=None) as gws:
            sid = _rpc(gws, 1, "session.create", {"request_id": "control", "source": "cli"})["session_id"]
            _rpc(gws, 2, "prompt.submit", {"session_id": sid, "text": "control turn", "input_id": "control-0"})
            done = _until(gws, lambda m: (m.get("params") or {}).get("type") in {"message.complete", "error"})
            assert REPLY in json.dumps(done), done
        yield {"sid": sid, "caller": model.wait_calls(1, "native turn"),
               "admissions": _admissions(db, sid), "replied": _replied(db, sid)}
    finally:
        for proc in model.procs.values():
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
        kill_tagged(tag)
        out.close()
        model.stop()


def test_native_turn_is_admitted_and_runs_in_the_gateway(scenario) -> None:
    assert scenario["admissions"], f"turn left no session_admissions row for {scenario['sid']}"
    assert scenario["caller"] == "gateway", scenario
    assert scenario["replied"], scenario
