"""Local terminal environment construction and availability diagnostics."""

from typing import Any

from tools.environments.local import LocalEnvironment
from tools.terminal_tool_config import require_local_environment

_last_unavailable_reason: str | None = None


def _record_unavailable_reason(reason: str | None) -> None:
    global _last_unavailable_reason
    _last_unavailable_reason = reason


def terminal_backend_unavailable_reason() -> str | None:
    """Reason the most recent terminal requirements check failed."""
    return _last_unavailable_reason


def _local_config_from_config(config: dict[str, Any]) -> dict:
    return {"persistent": config.get("local_persistent", True)}


def _create_environment(env_type: str, cwd: str, timeout: int,
                        local_config: dict | None = None):
    require_local_environment(env_type)
    env = LocalEnvironment(cwd=cwd, timeout=timeout,
                           persistent=(local_config or {}).get("persistent", True))
    return env
