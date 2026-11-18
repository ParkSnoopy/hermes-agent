"""Platform scope rules for configured toolsets."""

from toolsets import TOOLSET_SESSION_PLATFORMS as _TOOLSET_PLATFORM_RESTRICTIONS


def toolset_allowed_for_platform(ts_key: str, platform: str) -> bool:
    """Return whether ``ts_key`` is available on ``platform``."""
    allowed: set[str] | None = _TOOLSET_PLATFORM_RESTRICTIONS.get(ts_key)
    return allowed is None or platform in allowed
