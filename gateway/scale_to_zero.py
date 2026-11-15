"""Generic idle, dashboard heartbeat and machine power primitives."""

from __future__ import annotations

import json
import logging
import os
import socket
import time

from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCALE_TO_ZERO_ENV = "HERMES_SCALE_TO_ZERO"  # stamped by NAS when the Labs toggle is on
FLY_APP_NAME_ENV = "FLY_APP_NAME"  # Fly-injected identity; both needed for self_suspend_available()
FLY_MACHINE_ID_ENV = "FLY_MACHINE_ID"
# Local flaps (Fly Machines API) socket; POST .../suspend freezes THIS machine.
FLY_API_SOCKET = "/.fly/api"


# Short is safe: real work always blocks the suspend, resume is sub-second; longer bills idle RAM.
DEFAULT_IDLE_TIMEOUT_MINUTES = 2
_TRUTHY = {"1", "true", "yes", "on"}
# Dashboard-client liveness marker, touched by every /api/ws client socket on each
# connect and inbound frame. Folded into the inbound clock so an open client holds the box awake —
# otherwise it suspends under the client, whose reconnect re-pokes the wake URL and the instance
# flaps every ~60s. Deliberately NO staleness cutoff: is_idle decides whether the mtime is recent.
DASHBOARD_CLIENT_HEARTBEAT_REL = os.path.join("state", "dashboard_clients.heartbeat")


def _env_str(env: Optional[dict], key: str) -> str:
    return str((os.environ if env is None else env).get(key, "")).strip()


def scale_to_zero_enabled(environ: Optional[dict] = None) -> bool:
    """Whether the Labs toggle stamp is set. Absent/blank/falsey -> disabled."""
    return _env_str(environ, SCALE_TO_ZERO_ENV).lower() in _TRUTHY


def parse_idle_timeout_seconds(cfg_value: Any,
                               default_minutes: int = DEFAULT_IDLE_TIMEOUT_MINUTES) -> float:
    """Coerce ``scale_to_zero.idle_timeout_minutes`` to seconds. Non-numeric / non-positive
    degrades to the default (never <= 0: instant dormancy)."""
    try:
        minutes = float(cfg_value)
    except (TypeError, ValueError):
        minutes = 0.0
    return (float(default_minutes) if minutes <= 0 else minutes) * 60.0


def is_idle(*, active_work_count: int, seconds_since_last_inbound: float,
            idle_timeout_seconds: float, has_live_background_work: bool) -> bool:
    """Pure idle predicate: no active work, no inbound within the window, no live background work.
    ``active_work_count`` is the BROAD aggregate (agent turns + cron + API runs) — passing only
    ``len(_running_agents)`` reopens the mid-cron-job suspend hole. Callers that cannot read a
    work source must fail AWAKE (pass a positive sentinel), never fail to 0."""
    return (active_work_count <= 0 and not has_live_background_work
            and seconds_since_last_inbound >= idle_timeout_seconds)


def dashboard_client_heartbeat_path(hermes_home: Optional[os.PathLike | str] = None):
    """Path of the dashboard-client liveness marker under the process's own HERMES_HOME.

    One marker per process, never per profile scope: the gateway's idle watcher reads it with no
    override, so a socket served in a secondary profile's scope must stamp the same file."""
    if hermes_home is None:
        from hermes_constants import get_process_hermes_home
        hermes_home = get_process_hermes_home()
    return Path(hermes_home) / DASHBOARD_CLIENT_HEARTBEAT_REL


def touch_dashboard_client_heartbeat(path: Optional[os.PathLike | str] = None) -> bool:
    """Mark "a dashboard client is attached right now". Best-effort, never raises."""
    try:
        p = dashboard_client_heartbeat_path() if path is None else path
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "a", encoding="utf-8").close()
        os.utime(p, None)
        return True
    except Exception:
        logger.debug("scale-to-zero: dashboard heartbeat touch failed", exc_info=True)
        return False


def dashboard_client_last_seen(path: Optional[os.PathLike | str] = None, *,
                               now: Optional[float] = None) -> Optional[float]:
    """Epoch seconds a dashboard client last sent a WS frame, or None if never. Missing marker ->
    None (steady state when nobody has the dashboard open — NOT fail-awake, or no instance would
    ever sleep). Unreadable marker -> ``now`` (fail-awake, as in ``is_idle``). Clamped to now: an
    NTP step-back can leave the mtime in the future."""
    current = time.time() if now is None else now
    p = dashboard_client_heartbeat_path() if path is None else path
    try:
        return min(os.stat(p).st_mtime, current)
    except FileNotFoundError:
        return None
    except OSError:
        return current


def self_suspend_available(environ: Optional[dict] = None) -> bool:
    """True iff Fly machine identity and the local Machines API socket exist."""
    return bool(_env_str(environ, FLY_APP_NAME_ENV) and _env_str(environ, FLY_MACHINE_ID_ENV)
                and os.path.exists(FLY_API_SOCKET))


def suspend_self(environ: Optional[dict] = None, *, socket_path: str = FLY_API_SOCKET,
                 timeout: float = 10.0) -> bool:
    """POST /v1/apps/{app}/machines/{id}/suspend on the local flaps socket (the socket is the
    credential). Returns True when flaps accepted (2xx); the kernel then freezes this process
    shortly after, so treat as fire-and-forget. Never raises: a failed suspend leaves the machine
    running (fail-awake). stdlib-only on purpose — a plain unix-socket HTTP/1.1 request, no async
    plumbing to freeze mid-await."""
    app, machine_id = _env_str(environ, FLY_APP_NAME_ENV), _env_str(environ, FLY_MACHINE_ID_ENV)
    if not app or not machine_id:
        logger.warning("scale-to-zero: suspend_self called without Fly machine identity")
        return False
    request = (f"POST /v1/apps/{app}/machines/{machine_id}/suspend HTTP/1.1\r\n"
               "Host: flaps\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(socket_path)
            sock.sendall(request.encode("ascii"))
            response = b""
            while len(response) < 65536 and (chunk := sock.recv(4096)):
                response += chunk
    except OSError as exc:
        logger.warning("scale-to-zero: flaps suspend request failed: %s", exc)
        return False
    status_line = response.split(b"\r\n", 1)[0].decode("ascii", "replace")
    parts = status_line.split()
    ok = len(parts) >= 2 and parts[1].isdigit() and 200 <= int(parts[1]) < 300
    if ok:
        logger.info("scale-to-zero: machine suspend accepted by flaps (%s)", status_line)
    else:
        body = response.split(b"\r\n\r\n", 1)[-1][:500].decode("utf-8", "replace")
        logger.warning("scale-to-zero: flaps suspend rejected: %s %s", status_line,
                       json.dumps(body)[:500])
    return ok
