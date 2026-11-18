"""Shared leaf helpers for the ``hermes update`` modules (no Hermes imports; no cycle)."""

import logging
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

# Log-record parity with the origin module.
logger = logging.getLogger("hermes_cli.update_cmd")


def _planned_stop_marker_path(profile_path: Path) -> Path:
    return Path(profile_path) / ".gateway-planned-stop.json"


def _write_update_planned_stop_marker(profile_path: Path, pid: int) -> bool:
    """Write a planned-stop marker into a specific profile home."""
    try:
        from gateway.status import _get_process_start_time
        from utils import atomic_json_write
        atomic_json_write(
            _planned_stop_marker_path(profile_path),
            {"target_pid": pid, "target_start_time": _get_process_start_time(pid), "stopper_pid": os.getpid(),
             "written_at": datetime.now(UTC).isoformat()},
            indent=None, separators=(",", ":"),
        )
        return True
    except (OSError, PermissionError):
        return False


@contextmanager
def _best_effort(message: str):
    """Run a non-critical update step; swallow ``Exception`` and log it at debug.

    The updater must never die on bookkeeping (receipt, notices, cache seeds):
    ``message`` is the ``%s``-style debug line the inline ``try/except`` used.
    """
    try:
        yield
    except Exception as exc:
        logger.debug(message, exc)
