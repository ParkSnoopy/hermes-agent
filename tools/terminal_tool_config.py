"""Local terminal settings, scoped reads and best-effort lifecycle logging."""

import json
import logging
import os
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("tools.terminal_tool")


def require_local_environment(env_type: str) -> str:
    """Reject unsupported selectors before any command or file access on this host."""
    if env_type != "local":
        raise ValueError(
            f"Unsupported terminal environment {env_type!r}. Only 'local' is supported; "
            "no command was run. Set terminal.backend or TERMINAL_ENV to 'local' explicitly."
        )
    return "local"


@contextmanager
def _quiet(label: str, *args, exc=Exception, level: int = logging.DEBUG):
    """Best-effort block: swallow *exc*, log *label* (``%``-formatted with *args*)
    at *level* with the traceback. For janitor/advisory work that must never
    take the terminal tool down."""
    try:
        yield
    except exc:
        logger.log(level, label, *args, exc_info=True)


def _parse_env_var(name: str, default: str, converter: Any = int, type_label: str = "integer"):
    """Parse an env var with *converter*, raising a clear ValueError on bad
    values (e.g. TERMINAL_TIMEOUT=5m) instead of an opaque crash. TERMINAL_*
    names are read scope-aware via :func:`_tenv`."""
    raw = _tenv(name, default) if name.startswith("TERMINAL_") else os.getenv(name, default)
    try:
        return converter(raw)
    except (ValueError, json.JSONDecodeError):
        raise ValueError(
            f"Invalid value for {name}: {raw!r} (expected {type_label}). "
            f"Check ~/.hermes/.env or environment variables."
        )


def _safe_getcwd() -> str:
    """``os.getcwd()`` tolerant of a deleted cwd (FileNotFoundError) or a macOS
    TCC-protected one without Full Disk Access (PermissionError); falls back
    to TERMINAL_CWD, then the home directory."""
    try:
        return os.getcwd()
    except (FileNotFoundError, PermissionError):
        return _tenv("TERMINAL_CWD") or os.path.expanduser("~")


def _tenv(name: str, default: str = "") -> str:
    """Scope-aware read of a ``TERMINAL_*`` variable. Every terminal setting
    must go through this: under gateway multiplexing the active profile's
    config arrives via a per-turn scope, and a raw ``os.getenv`` would read
    whatever a previous turn pinned into the process env (cross-profile leak)."""
    from tools.terminal_scope import terminal_env

    return terminal_env(name, default)


def _tenv_bool(name: str, default: str) -> bool:
    """Scope-aware boolean ``TERMINAL_*`` read: true/1/yes (case-insensitive)."""
    return _tenv(name, default).lower() in {"true", "1", "yes"}
