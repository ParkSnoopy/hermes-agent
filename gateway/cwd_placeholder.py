"""Resolve gateway ``terminal.cwd`` placeholder values to ``TERMINAL_CWD``.

When ``terminal.cwd`` is unset or a placeholder (``.``, ``auto``, ``cwd``),
the gateway must not blindly map host ``Path.home()`` into container backends.
Docker with workspace mounting still needs an explicit host path signal
(``MESSAGING_CWD`` or an absolute config path) for ``terminal_tool`` to map
``/host/project`` → ``/workspace``.
"""

from __future__ import annotations

CWD_PLACEHOLDERS = frozenset({".", "auto", "cwd"})


def resolve_placeholder_terminal_cwd(
    *,
    configured_cwd: str,
    messaging_cwd: str | None,
    home_fallback: str,
) -> str:
    """Resolve an explicit or placeholder cwd on the local host."""
    if configured_cwd and configured_cwd not in CWD_PLACEHOLDERS:
        return configured_cwd
    return (messaging_cwd or "").strip() or home_fallback
