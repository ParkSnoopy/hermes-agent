"""Message helpers shared by turn persistence and media delivery."""

from __future__ import annotations

from typing import Any


def tool_name_by_call_id(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Map assistant tool-call ids to tool names for the given messages."""
    mapping: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            call_id = call.get("id") or call.get("call_id")
            name = str((call.get("function") or {}).get("name") or call.get("name") or "")
            if call_id and name:
                mapping[str(call_id)] = name
    return mapping


def _current_turn_messages(messages: list[dict[str, Any]], history_offset: int) -> list[dict[str, Any]]:
    if not history_offset or len(messages) >= history_offset:
        return messages[history_offset:]
    # Compression may invalidate the offset; fail closed if no user message remains.
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            return messages[index:]
    return []
