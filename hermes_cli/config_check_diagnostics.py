"""Read-only diagnostics for saved toolsets."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def config_check_diagnostics(config: dict[str, Any], get_env_value: Callable[[str], str | None]) -> list[str]:
    """Report saved selections that would otherwise be lost in startup output.

    Toolset names are judged by the resolver ``hermes config migrate`` also uses.
    """
    from hermes_cli.toolset_validation import saved_toolset_resolver, validate_platform_toolsets

    return validate_platform_toolsets(config.get("platform_toolsets"), saved_toolset_resolver(config))
