"""Per-turn terminal scope: profile-scoped TERMINAL_* policy.

Multiplexed surfaces (gateway, dashboard/TUI, cron) serve several profiles from one process;
mirroring terminal settings into ``os.environ`` let the first profile pin its backend onto
everyone else (sandbox escape). Like ``agent/secret_scope.py``, a ContextVar holds the active
profile's COMPLETE ``TERMINAL_*`` policy; while bound, ``terminal_env`` resolves ONLY from it
(omitted keys -> defined default, never ambient env). If the policy cannot be resolved a
*refusal* scope is installed and terminal execution raises :class:`TerminalPolicyUnavailable`.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

# None = no scope bound (process-env behavior); dict = complete policy; Refusal = resolution failed.
_terminal_scope_var: ContextVar = ContextVar("hermes_terminal_scope", default=None)

# Keys whose default lives in terminal_tool.py, not DEFAULT_CONFIG (which wins on overlap);
# without them the projection is not total.
_TOOL_LEVEL_DEFAULTS: dict[str, Any] = {
    "cwd": ".", "lifetime_seconds": 300, "home_mode": "auto",
}


class TerminalPolicyUnavailable(Exception):
    """The routed profile's ``.env``/``config.yaml`` exists but cannot be read/parsed."""


class TerminalPolicyRefusal(dict[str, str]):
    """Marker scope (empty dict subclass) installed when policy resolution failed."""

    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason


def set_terminal_scope(mapping: Optional[dict[str, str]]) -> Token:
    """Install *mapping* as the current context's terminal policy."""
    return _terminal_scope_var.set(mapping)


def reset_terminal_scope(token: Token) -> None:
    _terminal_scope_var.reset(token)


def get_terminal_scope() -> Optional[dict[str, str]]:
    """The active scope mapping/refusal, or ``None`` when no scope is bound."""
    return _terminal_scope_var.get()


def enforce_no_refusal() -> None:
    """Raise when the active scope is a refusal scope (fail closed).

    Execution paths (terminal tool, execute_code) call this before spawning anything: under a refusal scope
    the profile's terminal policy could not be resolved, and running with the launch process's ambient
    policy is exactly the authority leak this module closes (#68559 requires refusal, not fallback).
    Non-scoped and policy-scoped contexts pass silently.
    """
    scope = _terminal_scope_var.get()
    if isinstance(scope, TerminalPolicyRefusal):
        raise TerminalPolicyUnavailable(
            f"terminal policy unavailable for this profile: {scope.reason}")


def terminal_env(name: str, default: str = "") -> str:
    """Authoritative read of a ``TERMINAL_*`` variable.

    No scope: process env, then *default*. Refusal scope: raise. Policy scope: ONLY the
    policy; a missing key yields *default*, never os.environ.
    """
    scope = _terminal_scope_var.get()
    if scope is None:
        return os.environ.get(name, default)
    enforce_no_refusal()
    value = scope.get(name)
    return default if value is None else str(value)


def _apply_terminal_mapping(scope: dict[str, str], mapping: dict[str, Any]) -> None:
    from hermes_cli.config import TERMINAL_CONFIG_ENV_MAP, _terminal_env_value

    for cfg_key, value in mapping.items():
        # cwd placeholders are resolved per-surface later; not a policy value.
        if value is None or (cfg_key == "cwd" and str(value).strip() in {".", "auto", "cwd"}):
            continue
        env_var = TERMINAL_CONFIG_ENV_MAP.get(cfg_key)
        if env_var:
            # List/dict config values must be JSON (same contract as
            # apply_terminal_config_to_env). str() yields Python repr, which
            # json.loads in terminal_tool rejects.
            scope[env_var] = _terminal_env_value(value)


def default_terminal_scope() -> dict[str, str]:
    """Code-default ``TERMINAL_*`` policy: what a profile-bypassing session freezes instead of
    reading ``.env``/``config.yaml``. Total like the profile projection, minus the profile."""
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    scope: dict[str, str] = {}
    _apply_terminal_mapping(scope, {**_TOOL_LEVEL_DEFAULTS, **(DEFAULT_CONFIG.get("terminal") or {})})
    return scope


def build_profile_terminal_scope(
    hermes_home: Any, *, env_overlay: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Build the COMPLETE effective ``TERMINAL_*`` policy for a profile home.

    Projection: ``DEFAULT_CONFIG['terminal']`` <- profile ``.env`` TERMINAL_* <- *env_overlay*
    <- profile ``config.yaml`` ``terminal:`` <- managed-scope ``terminal:``. Total by construction,
    so a bound scope never widens back to ambient authority. Raises :class:`TerminalPolicyUnavailable`
    if a present file is unreadable, except a ``.env`` that refuses reads while managed scope pins
    ``terminal.backend``.

    *env_overlay* is a TRUSTED ``TERMINAL_*`` mapping captured from the launch process before
    multiplexing began (``tui_gateway/launch_profile_policy.py``): the launch profile's
    env-only policy (``TERMINAL_TIMEOUT`` from systemd, ``op run``, a launcher bridge) has no
    file to rebuild it from, and reading live ``os.environ`` here is the leak this module
    closes. It sits where the process env sits in the standalone bridge — explicit YAML keys
    still win (``apply_terminal_config_to_env``).
    """
    from hermes_cli import managed_scope
    from hermes_cli.config import TERMINAL_CONFIG_ENV_MAP, _deep_merge
    from hermes_cli.config_backend import config_exists

    home = Path(hermes_home)
    scope = default_terminal_scope()
    allowed_env = set(TERMINAL_CONFIG_ENV_MAP.values())
    env_path = home / ".env"
    if env_path.exists():
        # load_env_file swallows OSError by design (secret scope fails soft); an unreadable
        # profile .env must fail closed here, unless managed scope pins the backend: a sandbox
        # launcher withholds the .env and pins the backend there, and load_env_file reads it as {}.
        try:
            env_path.read_bytes()
        except Exception as exc:
            if not isinstance(exc, PermissionError) or managed_scope.pinned_terminal_backend() is None:
                raise TerminalPolicyUnavailable(f"cannot read {env_path}: {exc}") from exc
        from agent.secret_scope import load_env_file

        profile_env = load_env_file(env_path)
        scope.update((k, str(v)) for k, v in profile_env.items() if k in allowed_env)
    if env_overlay:
        scope.update((k, str(v)) for k, v in env_overlay.items() if k in allowed_env)
    # Read config.yaml directly, not via read_raw_config() (which collapses "missing" and
    # "unparseable" into {}): present-but-unparseable must fail closed.
    config_path = home / "config.yaml"
    try:
        has_config = config_exists(config_path)
    except Exception as exc:
        raise TerminalPolicyUnavailable(f"cannot resolve terminal config in {home}: {exc}") from exc
    raw_terminal = None
    if has_config:
        from hermes_cli.config_backend import read_config_doc_readonly

        try:
            # Signature-cached: a scope is rebuilt per routed turn/poll, the file rarely changes.
            raw = read_config_doc_readonly(config_path)
        except Exception as exc:
            raise TerminalPolicyUnavailable(f"cannot parse {config_path}: {exc}") from exc
        raw_terminal = raw.get("terminal") if isinstance(raw, dict) else None
        if isinstance(raw_terminal, dict):
            _validate_config_backend(raw_terminal)
            _apply_terminal_mapping(scope, raw_terminal)
    managed_terminal = managed_scope.apply_managed_overlay({}).get("terminal")
    if isinstance(managed_terminal, dict):
        _validate_config_backend(managed_terminal)
        _apply_terminal_mapping(
            scope, _deep_merge(raw_terminal if isinstance(raw_terminal, dict) else {}, managed_terminal))
    _resolve_scope_cwd_placeholder(scope)
    return scope


def _validate_config_backend(terminal: dict[str, Any]) -> None:
    backend = terminal.get("backend")
    if backend is None or not str(backend).strip():
        return
    from tools.terminal_tool_config import require_local_environment
    try:
        require_local_environment(str(backend))
    except ValueError as exc:
        raise TerminalPolicyUnavailable(str(exc)) from exc


def _resolve_scope_cwd_placeholder(scope: dict[str, str]) -> None:
    """Use the host home for an unspecified local working directory."""
    if not scope.get("TERMINAL_CWD"):
        scope["TERMINAL_CWD"] = str(Path.home())
    else:
        scope["TERMINAL_CWD"] = os.path.expanduser(scope["TERMINAL_CWD"])


def prepare_profile_terminal_scope(
    hermes_home: Any, *, env_overlay: Optional[dict[str, str]] = None) -> dict[str, str]:
    """The scope ``install_profile_terminal_scope`` installs: the profile's policy, or the refusal scope
    when it cannot be resolved. Never raises. Reads the profile files (the managed-scope overlay under the
    current context's home override); the result can be built in a worker and installed on the loop with
    ``install_and_reset_profile_terminal_scope(prepared=...)``."""
    try:
        return build_profile_terminal_scope(hermes_home, env_overlay=env_overlay)
    except TerminalPolicyUnavailable as exc:
        logger.warning("terminal policy unavailable: %s", exc)
        return TerminalPolicyRefusal(str(exc))


def install_profile_terminal_scope(
    hermes_home: Any, *, env_overlay: Optional[dict[str, str]] = None) -> Token:
    """Build AND install a profile's policy; on failure install the refusal scope. Never raises."""
    return set_terminal_scope(prepare_profile_terminal_scope(hermes_home, env_overlay=env_overlay))


@contextmanager
def install_and_reset_profile_terminal_scope(
    hermes_home: Any, *, prepared: Optional[dict[str, str]] = None) -> Iterator[None]:
    """Install the profile's terminal policy for a bounded turn/fire. Never raises. *prepared* is a
    ``prepare_profile_terminal_scope`` result built earlier (off the loop); without it the policy is
    built here."""
    token = set_terminal_scope(prepared) if prepared is not None else install_profile_terminal_scope(hermes_home)
    try:
        yield
    finally:
        reset_terminal_scope(token)
