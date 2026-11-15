"""Entrypoint driver for the OpenAI-compatible API server parity matrix.

It follows the driver contract in ``_drive_cli``. Daemon surfaces have no launch
directory, so the documented cwd channel ``terminal.cwd`` is pinned in config.yaml
(``DriveResult.cwd_channel = "terminal.cwd"``) and credentials go in the fixture
home's ``.env`` exactly as a user configures them.

* ``drive_api_server`` — the stock ``python -m gateway.run`` with only the
  ``api_server`` platform enabled (loopback, free port, strong key); one
  non-streaming ``POST /v1/chat/completions``.

Both stop through the normal operator path of ``hermes gateway stop``: write the
planned-stop marker for the gateway PID, then SIGTERM (a bare SIGTERM is treated
as an unexpected kill and exits non-zero on purpose so supervisors revive it).
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from tests.e2e.core.parity._helpers import TURN_TIMEOUT, DriveResult, ParityHome
from tests.fakes.fake_llm_provider import FakeLLMServer

STOP_TIMEOUT = 60.0


def _append_env(ph: ParityHome, values: dict[str, str]) -> None:
    path = ph.hermes_home / ".env"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")


def _spawn(ph: ParityHome, argv: list[str], log_name: str, extra_env: dict[str, str] | None = None):
    log_path = ph.root / log_name
    log = open(log_path, "wb")
    proc = subprocess.Popen(
        argv, cwd=ph.project, env=ph.env(extra_env), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1,
    )
    proc._parity_log = log  # type: ignore[attr-defined]
    proc._parity_log_path = log_path  # type: ignore[attr-defined]
    return proc



def _log_tail(proc: subprocess.Popen, n: int = 4000) -> str:
    try:
        return proc._parity_log_path.read_text(encoding="utf-8", errors="replace")[-n:]  # type: ignore[attr-defined]
    except OSError:
        return ""


def _stop(ph: ParityHome, proc: subprocess.Popen) -> bool:
    """``hermes gateway stop`` semantics (planned-stop marker for the PID, then SIGTERM),
    in its worst-case interleaving: the SIGTERM lands only AFTER the gateway's
    planned-stop watcher has already consumed the marker. Any interleaving of the
    CLI's two steps must still be a clean planned exit (exit 0; a non-zero exit
    makes systemd/launchd revive a gateway the operator just stopped).

    Returns True when the gateway exited 0 on its own within ``STOP_TIMEOUT``.
    """
    graceful = False
    try:
        if proc.poll() is None:
            marker = subprocess.run(
                [sys.executable, "-c",
                 "import sys; from gateway.status import write_planned_stop_marker as w, "
                 "_get_planned_stop_marker_path as p; ok = w(int(sys.argv[1])); print(p()); "
                 "sys.exit(0 if ok else 1)",
                 str(proc.pid)],
                cwd=ph.project, env=ph.env(), capture_output=True, text=True, timeout=60,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            assert marker.returncode == 0, f"planned-stop marker write failed: {marker.stderr[-1000:]}"
            marker_path = Path(marker.stdout.strip().splitlines()[-1])
            deadline = time.monotonic() + STOP_TIMEOUT
            while marker_path.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=STOP_TIMEOUT)
                graceful = proc.returncode == 0
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
    finally:
        proc._parity_log.close()  # type: ignore[attr-defined]
    return graceful



# OpenAI-compatible API server -------------------------------------------------------


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(method: str, url: str, *, key: str | None = None, body: dict | None = None,
          timeout: float = 5.0) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


PORT_ATTEMPTS = 3


def _await_own_api_server(proc: subprocess.Popen, base: str, key: str) -> bool:
    """True once OUR child answers on ``base`` (authenticated ``/health/detailed`` reporting its
    pid, so a stranger that grabbed the port is never mistaken for it); False when the child
    reports the port taken. The port is picked free and then released before the child binds
    it, so another process can win that race."""
    deadline = time.monotonic() + TURN_TIMEOUT
    while True:
        if "already in use" in _log_tail(proc, 20000):
            return False
        if proc.poll() is not None:
            raise AssertionError(f"api server exited {proc.returncode} before ready\n{_log_tail(proc)}")
        if time.monotonic() >= deadline:
            raise AssertionError(f"api server never became ready on {base}\n{_log_tail(proc)}")
        with contextlib.suppress(OSError, urllib.error.URLError, ValueError):
            status, raw = _http("GET", f"{base}/health/detailed", key=key, timeout=2.0)
            if status == 200 and json.loads(raw).get("pid") == proc.pid:
                return True
        time.sleep(0.2)


def drive_api_server(ph: ParityHome, srv: FakeLLMServer, prompt: str) -> DriveResult:
    ph.pin_terminal_cwd()
    key = secrets.token_hex(32)
    env_before = (ph.hermes_home / ".env").read_text(encoding="utf-8") if (ph.hermes_home / ".env").exists() else ""
    for _attempt in range(PORT_ATTEMPTS):
        port = _free_loopback_port()
        (ph.hermes_home / ".env").write_text(env_before, encoding="utf-8")
        _append_env(ph, {"API_SERVER_ENABLED": "true", "API_SERVER_KEY": key,
                         "API_SERVER_HOST": "127.0.0.1", "API_SERVER_PORT": str(port)})
        proc = _spawn(ph, [sys.executable, "-m", "gateway.run"], "api_server.stderr.log")
        base = f"http://127.0.0.1:{port}"
        try:
            ready = _await_own_api_server(proc, base, key)
        except BaseException:
            _stop(ph, proc)
            raise
        if ready:
            break
        _stop(ph, proc)
    else:
        raise AssertionError(f"api server lost the port race {PORT_ATTEMPTS} times\n{_log_tail(proc)}")
    status: int | None = None
    payload: dict[str, Any] = {}
    graceful = False
    try:
        status, raw = _http(
            "POST", f"{base}/v1/chat/completions", key=key, timeout=TURN_TIMEOUT,
            body={"model": "hermes-agent", "messages": [{"role": "user", "content": prompt}],
                  "stream": False},
        )
        payload = json.loads(raw) if raw.strip().startswith("{") else {"raw": raw}
        assert status == 200, f"/v1/chat/completions -> {status}: {raw[-2000:]}\n{_log_tail(proc)}"
    finally:
        graceful = _stop(ph, proc)
    choices = payload.get("choices") or [{}]
    text = (choices[0].get("message") or {}).get("content")
    return DriveResult(
        final_text=text, toolset="hermes-api-server", cwd_channel="terminal.cwd", graceful_exit=graceful,
        extra={"http_status": status, "exit_code": proc.returncode,
               "stderr_log": str(proc._parity_log_path)},  # type: ignore[attr-defined]
    )
