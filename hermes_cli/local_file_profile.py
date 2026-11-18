"""Refuse remote terminal profiles at the local file-routing boundary."""

from pathlib import Path

from fastapi import HTTPException


def require_local_file_profile(profile_home: Path) -> None:
    from tools.terminal_scope import TerminalPolicyUnavailable, build_profile_terminal_scope
    from tools.terminal_tool_config import require_local_environment

    try:
        policy = build_profile_terminal_scope(profile_home)
        require_local_environment(policy.get("TERMINAL_ENV", "local"))
    except (TerminalPolicyUnavailable, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
