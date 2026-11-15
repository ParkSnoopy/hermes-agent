"""Origin metadata carried by a cron delivery."""

from __future__ import annotations

from typing import Any


def stamp_origin_discriminators(t: Any, route_metadata: dict, media_metadata: dict) -> None:
    """Preserve the originating scope only when delivering to that origin."""
    discriminators = (
        ("scope_id", t.origin.get("scope_id") if t.origin_target else None),
    )
    for key, value in discriminators:
        if value:
            route_metadata.setdefault(key, str(value))
            media_metadata.setdefault(key, str(value))
