"""Name a callable API key's credential source without calling it.

A callable ``api_key`` mints a bearer on first use (Entra ID, Vertex OAuth, a custom provider's
``key_cmd``, MiniMax OAuth). Display paths (``/config``, the agent's key banner) must say which
kind it is, but calling it would mint a token just to mask it. Each builder tags the callable it
returns with a source; display reads the tag through one source-to-label table. An untagged
callable gets a neutral label, never another vendor's name.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

ENTRA_ID = "entra-id"
VERTEX_OAUTH = "vertex-oauth"
KEY_CMD = "key-cmd"
MINIMAX_OAUTH = "minimax-oauth"

# Attribute read with getattr only: reading it never invokes the callable.
SOURCE_ATTR = "hermes_credential_source"

# Both display surfaces resolve credential labels here.
SOURCE_LABELS = {
    ENTRA_ID: "Microsoft Entra ID",
    VERTEX_OAUTH: "Google OAuth (Vertex AI)",
    KEY_CMD: "key_cmd token (command)",
    MINIMAX_OAUTH: "MiniMax OAuth",
}
UNKNOWN_LABEL = "Token provider"

_F = TypeVar("_F", bound=Callable[..., Any])


def tag_credential_source(provider: _F, source: str) -> _F:
    """Record *source* on *provider* and return it (a no-op for objects that refuse attributes)."""
    try:
        setattr(provider, SOURCE_ATTR, source)
    except (AttributeError, TypeError):
        pass
    return provider


def credential_source(key: Any) -> Optional[str]:
    """The source tag of a callable key, or None (string keys and untagged callables)."""
    if isinstance(key, str) or not callable(key):
        return None
    source = getattr(key, SOURCE_ATTR, None)
    return source if isinstance(source, str) else None


def credential_label(key: Any) -> Optional[str]:
    """Label for a callable key's source (neutral when untagged); None for a non-callable."""
    if isinstance(key, str) or not callable(key):
        return None
    return SOURCE_LABELS.get(credential_source(key) or "", UNKNOWN_LABEL)
