"""Context-local state for delegate_task child execution."""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Mapping, MutableMapping, overload

_DELEGATED_CHILD_CONTEXT: ContextVar[bool] = ContextVar("hermes_delegated_child_context", default=False)
DELEGATED_CHILD_ENV_MARKER = "HERMES_DELEGATED_CHILD_CONTEXT"


@contextmanager
def delegated_child_context(session_id: str | None = None) -> Iterator[None]:
    """Mark a child and isolate its task-local session identity."""
    token = _DELEGATED_CHILD_CONTEXT.set(True)
    try:
        from gateway.session_context import scoped_current_session_id
        with scoped_current_session_id(session_id):
            yield
    finally:
        _DELEGATED_CHILD_CONTEXT.reset(token)


def is_delegated_child_context() -> bool:
    """Return True while code is running for a delegate_task child."""
    return bool(_DELEGATED_CHILD_CONTEXT.get())


def is_delegated_child_process_context() -> bool:
    """Return True in a child or a subprocess spawned by a child."""
    return is_delegated_child_context() or bool(os.environ.get(DELEGATED_CHILD_ENV_MARKER))


@overload
def delegated_child_subprocess_env(env: Mapping[str, str]) -> dict[str, str]: ...


@overload
def delegated_child_subprocess_env(env: None = None) -> dict[str, str] | None: ...


def delegated_child_subprocess_env(
    env: Mapping[str, str] | MutableMapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Preserve delegated-child identity across subprocess boundaries."""
    if not (is_delegated_child_process_context() or (env and env.get(DELEGATED_CHILD_ENV_MARKER))):
        return None if env is None else dict(env)
    result = dict(os.environ if env is None else env)
    result[DELEGATED_CHILD_ENV_MARKER] = result.get(DELEGATED_CHILD_ENV_MARKER) or "1"
    return result
