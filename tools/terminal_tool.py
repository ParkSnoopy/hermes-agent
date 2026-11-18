#!/usr/bin/env python3
"""Local shell commands, per-task persistent shells, approvals and tracked processes."""

import json
import logging
import os
import time
import threading
import atexit
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def _redact_terminal_error_text(value: Any) -> str:
    """Force-redact text before serializing a terminal error envelope."""
    from agent.redact import redact_sensitive_text

    return redact_sensitive_text("" if value is None else str(value), force=True)


from tools.registry import tool_error
from tools.terminal_tool_lifecycle import (
    _call_in_flight, _cleanup_inactive_envs, _create_configured_env,
    _evict_environment_for_task, cleanup_all_environments,
)
from tools.terminal_tool_config import (
    _parse_env_var, _quiet, _safe_getcwd, _tenv, _tenv_bool, require_local_environment,
)
from tools.terminal_tool_backends import (
    _record_unavailable_reason, terminal_backend_unavailable_reason,
)
# display_hermes_home imported lazily at call site (stale-module safety during hermes update)


def _safe_parse_import_env(name: str, default: Any, converter, type_label: str):
    """Parse a module-level numeric env var; a malformed value must never make
    the module unloadable at import time (CLI, ACP, tests, tool discovery)."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return converter(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid value for %s: %r (expected %s). Falling back to %r.",
            name, raw, type_label, default,
        )
        return default


# Hard cap on foreground timeout; override via TERMINAL_MAX_FOREGROUND_TIMEOUT env var.
FOREGROUND_MAX_TIMEOUT = _safe_parse_import_env("TERMINAL_MAX_FOREGROUND_TIMEOUT", 600, int, "integer")


# Approval / sudo-prompt UI callbacks (CLI registers prompt_toolkit-aware
# ones). Thread-local so overlapping ACP sessions, each on its own executor
# thread, can't stomp on each other (GHSA-qg5c-hvr5-hjgr). Gateway mode
# resolves approvals via the per-session queue in tools.approval instead.
_callback_tls = threading.local()


def _get_sudo_password_callback():
    return getattr(_callback_tls, "sudo_password", None)


def _get_approval_callback():
    return getattr(_callback_tls, "approval", None)


def set_sudo_password_callback(cb):
    """Register the CLI's sudo password prompt callback (per-thread slot)."""
    _callback_tls.sudo_password = cb


def set_approval_callback(cb):
    """Register the dangerous-command approval prompt callback (per-thread slot)."""
    _callback_tls.approval = cb


def _current_session_key() -> str:
    """Active gateway/WebUI session key, or "" outside sessions (ContextVar with
    the ``get_session_env`` os.environ fallback for CLI/cron/tests)."""
    from gateway.session_context import get_session_env

    return get_session_env("HERMES_SESSION_KEY", "")


from tools.approval import (
    check_all_command_guards as _check_all_guards_impl,
)


def _check_all_guards(command: str, env_type: str) -> dict:
    """Delegate to the consolidated command guard with the CLI callback."""
    return _check_all_guards_impl(command, env_type,
                                  approval_callback=_get_approval_callback())




# Tool description for LLM
TERMINAL_TOOL_DESCRIPTION = """Run shell commands on the OS/shell/backend named in your environment section. The working directory, files and exported variables persist between calls: activate a virtualenv or export once, not before every command.

Use read_file, search_files, patch and write_file instead of cat/head/tail, grep/find/ls, sed/awk and echo/heredoc. Output is auto-truncated with the full text saved to a file; never pipe through head/tail to shorten it.

Foreground (default) returns as soon as the command finishes. To wait a fixed time, run the wait in the foreground; never background a sleep or polling loop. Background is only for commands that must keep running after this call returns; health-check a server in a separate call and manage jobs with process_manage. When a command changes directory, trust the result's "cwd" instead of prefixing later commands with cd.
"""

# Environment lifecycle state.
_active_environments: dict[str, Any] = {}
_last_activity: dict[str, float] = {}
_env_lock = threading.Lock()
_creation_locks: dict[str, threading.Lock] = {}  # Per-task locks for sandbox creation
_creation_locks_lock = threading.Lock()  # Protects _creation_locks dict itself
_calls_in_flight: dict[str, int] = {}  # Foreground calls running per env key; the reaper skips these
_cleanup_thread = None
_cleanup_running = False


_task_env_overrides: dict[str, dict[str, Any]] = {}

_session_cwd: dict[str, str] = {}
_session_cwd_lock = threading.Lock()

def record_session_cwd(session_key: Optional[str], cwd: Optional[str]) -> None:
    """Record *cwd* as *session_key*'s working directory (after a completed
    command, or on workspace-override registration). None/empty keys collapse
    to ``"default"``; non-string / empty cwds are ignored. Keys are routed-profile
    qualified (:func:`_qualify_task_key`); the read/clear side qualifies identically."""
    if not isinstance(cwd, str) or not cwd.strip():
        return
    key = _qualify_task_key(str(session_key or "default"))
    with _session_cwd_lock:
        if _session_cwd.get(key) != cwd:
            _session_cwd[key] = cwd


def get_session_cwd(session_key: Optional[str]) -> Optional[str]:
    """Recorded cwd for *session_key*, or None. No fallback chain on purpose:
    callers decide what an absent record means. None/empty keys read ``"default"``."""
    with _session_cwd_lock:
        return _session_cwd.get(_qualify_task_key(str(session_key or "default")))


def clear_session_cwd(session_key: str) -> None:
    """Drop a session's cwd record (session teardown)."""
    with _session_cwd_lock:
        _session_cwd.pop(_qualify_task_key(session_key), None)


def register_task_env_overrides(task_id: str, overrides: dict[str, Any]):
    """Bind a task's local workspace; refuse retired execution settings before mutating state."""
    if "env_type" in overrides:
        require_local_environment(overrides["env_type"])
    unsupported = set(overrides) - {"cwd", "cwd_source", "env_type"}
    if unsupported:
        raise ValueError(f"Unsupported terminal task settings: {', '.join(sorted(unsupported))}")
    key = _resolve_task_id(task_id)
    _task_env_overrides[key] = dict(overrides)
    new_cwd = overrides.get("cwd")
    if new_cwd:
        record_session_cwd(task_id, new_cwd)
        with _env_lock:
            env = _active_environments.get(key)
            if env is not None:
                env.cwd = new_cwd


def clear_task_env_overrides(task_id: str):
    """Drop a task's workspace override and cwd record."""
    _task_env_overrides.pop(_resolve_task_id(task_id), None)
    clear_session_cwd(task_id)


def _qualify_task_key(key: str) -> str:
    """Keep a routed profile's local shells and cwd records separate from the launch profile."""
    from hermes_constants import get_hermes_home_override, hermes_home_key
    if get_hermes_home_override() is None:
        return key
    prefix = f"home:{hermes_home_key()}:"
    return key if key.startswith(prefix) else f"{prefix}{key}"


def _resolve_task_id(task_id: Optional[str]) -> str:
    """Resolve a local shell key without merging independent tasks or profiles."""
    return _qualify_task_key(task_id or _current_session_key() or "default")


def resolve_task_overrides(task_id: Optional[str]) -> dict[str, Any]:
    """Read the workspace settings bound to this task and profile."""
    overrides = _task_env_overrides.get(_resolve_task_id(task_id), {})
    require_local_environment(overrides.get("env_type", "local"))
    return overrides


def _lookup_active_env(effective_task_id: str, task_id: Optional[str]):
    """Return only this profile-qualified task's cached local shell. Caller holds the lock."""
    env = _active_environments.get(effective_task_id)
    if env is not None:
        _last_activity[effective_task_id] = time.time()
    return env


# One-shot guard for the config-fallback bridge: after the first attempt
# either TERMINAL_ENV is set or the import failed, so retrying is wasted work.
_terminal_config_bridge_attempted = False


def _ensure_terminal_env_bridged() -> None:
    """Backfill TERMINAL_* env vars from config.yaml when no launcher did.

    CLI, gateway and TUI/dashboard PTY launches bridge ``terminal.*`` into env vars
    at startup; processes that skip those paths (``hermes serve``, Desktop
    in-process agents, desktop cron ticker, ACP) would otherwise fall back to the
    local backend without honoring a configured selection. Unsupported legacy selections must be
    refused rather than silently running locally. Explicit keys in the ``terminal`` section override matching
    env values (possibly stale from ``hermes setup``); env values for omitted keys
    are preserved. Without a terminal section an existing TERMINAL_ENV is kept and
    defaults are backfilled only when none is set. A per-turn terminal scope
    suppresses the bridge entirely: writing scope values into the process-global
    env would re-create the first-writer-wins cross-profile leak the scope fixes.

    Ambient ``os.environ`` is the *launch* profile's authority only. Under a
    context-local ``HERMES_HOME`` override (multiplexed dashboard / gateway
    secondary profile), this bridge is a no-op — otherwise the first unscoped
    call under that override would latch the secondary profile's ``terminal.*``
    into process-global env and poison later unscoped launch-profile turns
    (#107422 residual of #68559). Routed profiles must bind a terminal scope
    instead (same rule as ``env_loader._reapply_terminal_config_bridge``).

    terminal_tool reads ALL terminal settings from os.environ (TERMINAL_*). See #61115, #65696.
    """
    from tools.terminal_scope import get_terminal_scope

    if get_terminal_scope() is not None:
        return
    # Never write a secondary profile's terminal.* into process-global env.
    from hermes_constants import get_hermes_home_override

    if get_hermes_home_override() is not None:
        return
    global _terminal_config_bridge_attempted
    if _terminal_config_bridge_attempted:
        return
    from hermes_cli.config import apply_terminal_config_to_env, read_raw_config

    raw_config = read_raw_config()
    if isinstance(raw_config.get("terminal"), dict):
        selected = raw_config["terminal"].get("backend", _tenv("TERMINAL_ENV", "local"))
        require_local_environment(selected)
        apply_terminal_config_to_env(env=None, override=True)
    elif "TERMINAL_ENV" not in os.environ:
        apply_terminal_config_to_env(env=None, override=False)
    _terminal_config_bridge_attempted = True


def _get_env_config() -> dict[str, Any]:
    """Resolve local terminal settings; never fall back from a retired selector."""
    _ensure_terminal_env_bridged()
    env_type = require_local_environment(_tenv("TERMINAL_ENV", "local"))
    return {
        "env_type": env_type,
        "cwd": os.path.expanduser(_tenv("TERMINAL_CWD") or _safe_getcwd()),
        "timeout": _parse_env_var("TERMINAL_TIMEOUT", "180"),
        "lifetime_seconds": _parse_env_var("TERMINAL_LIFETIME_SECONDS", "300"),
        "local_persistent": _tenv_bool("TERMINAL_LOCAL_PERSISTENT", "true"),
    }


def _cleanup_thread_worker():
    """Background thread worker that periodically cleans up inactive environments."""
    while _cleanup_running:
        with _quiet("Error in cleanup thread", level=logging.WARNING):
            _cleanup_inactive_envs(_get_env_config()["lifetime_seconds"])
        for _ in range(60):
            if not _cleanup_running:
                break
            time.sleep(1)


def _start_cleanup_thread():
    """Start the background cleanup thread if not already running."""
    global _cleanup_thread, _cleanup_running

    with _env_lock:
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_running = True
            _cleanup_thread = threading.Thread(target=_cleanup_thread_worker, daemon=True)
            _cleanup_thread.start()


def _stop_cleanup_thread():
    """Stop the background cleanup thread."""
    global _cleanup_running
    _cleanup_running = False
    if _cleanup_thread is not None:
        try:
            _cleanup_thread.join(timeout=5)
        except (SystemExit, KeyboardInterrupt):
            pass


def _atexit_cleanup():
    """Stop the idle reaper and close the remaining local shells."""
    _stop_cleanup_thread()
    if _active_environments:
        cleanup_all_environments()

atexit.register(_atexit_cleanup)


def _command_requires_pipe_stdin(command: str) -> bool:
    """True when PTY mode would break a stdin-driven command: `gh auth login
    --with-token` waits for EOF on piped stdin, and under a PTY
    `process.submit()` only sends a newline, so it hangs forever."""
    normalized = " ".join(command.lower().split())
    return normalized.startswith("gh auth login") and "--with-token" in normalized


from tools.terminal_tool_guards import (
    _foreground_background_guidance, _safe_command_preview, _validate_workdir,
    gateway_lifecycle_block, locked_skill_block, trailing_amp_background_command,
)
from tools.terminal_tool_background import _YIELDED_NOTE, spawn_background_process, yield_to_background_handler
from tools.terminal_tool_result import finalize_foreground_result


def _resolve_notification_flag_conflict(*, notify_on_complete: bool, watch_patterns, background: bool) -> tuple:
    """Resolve notify_on_complete + watch_patterns both set: drop watch_patterns
    (combined they produce duplicate async notifications — one per match plus
    one on exit — that can spam the user long after the process ends).
    Returns ``(watch_patterns_to_use, conflict_note)``; note is "" without conflict."""
    if background and notify_on_complete and watch_patterns:
        return None, (
            "watch_patterns ignored because notify_on_complete=True; "
            "these two flags produce duplicate notifications when combined"
        )
    return watch_patterns, ""


def _resolve_command_cwd(*, workdir: Optional[str], default_cwd: str,
                         session_key: Optional[str] = None) -> str:
    """Explicit workdir wins over the session's completed-command cwd and configured cwd."""
    return workdir or get_session_cwd(session_key) or default_cwd


def _error_json(error: str, *, exit_code: int = -1, status: Optional[str] = None, **extra) -> str:
    """The terminal error envelope: ``output``/``exit_code``/``error`` (+ ``status``, extras)."""
    body: dict[str, Any] = {"output": "", "exit_code": exit_code, "error": error}
    if status is not None:
        body["status"] = status
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _fatal_error_json(e: BaseException) -> str:
    """Log the traceback and return the redacted error+traceback envelope.

    Exception text can embed the failing command line (and any secrets inline
    in it), so both fields are force-redacted before reaching the model.
    """
    import traceback
    tb_str = traceback.format_exc()
    logger.error("terminal_tool exception:\n%s", tb_str)
    return json.dumps({
        "output": "",
        "exit_code": -1,
        "error": _redact_terminal_error_text(f"Failed to execute command: {e}"),
        "traceback": _redact_terminal_error_text(tb_str),
        "status": "error"
    }, ensure_ascii=False)


class _Rejected(Exception):
    """Carries a finished tool-result JSON out of the planning/guard helpers, so
    each early-return site is one ``raise`` instead of an isinstance-checked
    ``str | plan`` union at the caller."""

    def __init__(self, result_json: str):
        super().__init__(result_json)
        self.result_json = result_json


@dataclass
class _ApprovalVerdict:
    """Outcome of the pre-exec guard pass.

    ``note`` is the audit note attached to the result. ``approved_run`` is True
    when the user explicitly approved (or pre-confirmed via ``force``); it drives
    the clean-interrupt-slate clear before ``env.execute`` so an approved command
    can't be SIGINT-killed by a bit that landed during the approval-wait.
    """
    note: Optional[str] = None
    approved_run: bool = False


def _run_approval_guards(command: str, env_type: str, config: dict[str, Any], *, force: bool) -> _ApprovalVerdict:
    """Run the command guards; ``force`` skips them entirely.
    Raises :class:`_Rejected` when the command may not run (denied, or pending
    gateway approval)."""
    if force:
        return _ApprovalVerdict(approved_run=True)
    approval = _check_all_guards(command, env_type)
    if not approval["approved"]:
        if approval.get("status") == "pending_approval":  # gateway ask mode
            raise _Rejected(_error_json(
                "", status="pending_approval",
                approval_pending=True,
                command=approval.get("command", command),
                description=approval.get("description", "command flagged"),
                pattern_key=approval.get("pattern_key", ""),
                smart_denied=approval.get("smart_denied", False),
                allow_permanent=approval.get("allow_permanent", True),
            ))
        desc = approval.get("description", "command flagged")
        fallback_msg = (
            f"Command denied: {desc}. "
            "Use the approval prompt to allow it, or rephrase the command."
        )
        raise _Rejected(_error_json(approval.get("message", fallback_msg), status="blocked",
                                    **({"user_summary": approval["user_summary"]} if approval.get("user_summary") else {})))
    desc = approval.get("description", "flagged as dangerous")
    if approval.get("user_approved"):
        return _ApprovalVerdict(
            note=f"Command required approval ({desc}) and was approved by the user.",
            approved_run=True,
        )
    if approval.get("smart_approved"):
        return _ApprovalVerdict(note=f"Command was flagged ({desc}) and auto-approved by smart approval.")
    if approval.get("pre_approved"):
        from tools.approval_provenance import preapproval_note
        return _ApprovalVerdict(note=preapproval_note(approval["pre_approved"]))
    return _ApprovalVerdict()


@dataclass
class _ExecPlan:
    """Per-call execution parameters resolved before any environment is touched."""
    config: dict[str, Any]
    env_type: str
    effective_task_id: str
    cwd: str
    effective_timeout: int
    # Set when a foreground call asked for more than FOREGROUND_MAX_TIMEOUT and was promoted to a
    # tracked background process instead of being refused (the requested seconds, for the note).
    promoted_from_foreground_timeout: Optional[int] = None
    # Set when a foreground `cmd &` (the trailing `&` its only backgrounding) is run as
    # terminal(command="cmd", background=true) instead of being refused: the command to run.
    auto_background_command: Optional[str] = None


_PROMOTED_NOTE = (
    "Requested foreground timeout {requested}s exceeds the {cap}s cap, so this command was started as a "
    "tracked background process with notify_on_complete=true instead of being refused. Do NOT re-run it. "
    "Its completion (exit code + output tail) arrives as a notification; poll with "
    "process(action=\"poll\", session_id=...) if you need it sooner."
)


def _plan_execution(
    command: Any, *, task_id: Optional[str], timeout: Optional[int],
    background: bool,
) -> _ExecPlan:
    """Resolve backend, env-cache key, image, cwd and timeout for one call.

    Raises :class:`_Rejected` when the call is rejected up front (non-string
    command, non-positive or over-cap timeout, a foreground command that must
    run in the background).
    """
    if not isinstance(command, str):
        logger.warning("Rejected invalid terminal command value: %s", type(command).__name__)
        raise _Rejected(_error_json(
            f"Invalid command: expected string, got {type(command).__name__}", status="error",
        ))

    config = _get_env_config()
    env_type = require_local_environment(config["env_type"])

    # Fail closed under a refusal scope: the routed profile's terminal
    # policy could not be resolved, so running with the launch process's
    # ambient policy is forbidden.
    # See #68559.
    from tools.terminal_scope import enforce_no_refusal

    enforce_no_refusal()

    effective_task_id = _resolve_task_id(task_id)


    overrides = resolve_task_overrides(task_id)
    cwd = overrides.get("cwd") or get_session_cwd(task_id) or config["cwd"]
    # Reject non-positive timeouts before deadline math: ``timeout or
    # default`` would silently turn 0 into the default, and a negative
    # value is truthy and would fire an immediate "-Ns" timeout.
    if timeout is not None and timeout <= 0:
        raise _Rejected(tool_error(f"timeout must be a positive number of seconds (got {timeout})."))
    promoted = None
    if not background:
        # An over-cap foreground timeout is a bounded job the caller wants to wait for (test suites,
        # builds). Refusing it only bought a mechanical retry: 454 refusals in one run, every one
        # re-sent lower/split/background. Promote to a tracked background process instead; the
        # caller is told in the result. The `&`/nohup/server guidance below stays a refusal: those
        # need the command itself rewritten, which the tool cannot do safely.
        # The detachment guidance applies whether or not the call is promoted: a promoted `cmd &`
        # would start a tracked shell that exits at once while its payload runs untracked.
        # Exception: a lone TRAILING `&` is unambiguous (the caller asked for exactly what
        # background=true does), so run it that way instead of spending a model turn on a resend.
        auto_bg = trailing_amp_background_command(command)
        if auto_bg is None:
            guidance = _foreground_background_guidance(command)
            if guidance:
                raise _Rejected(_error_json(guidance, status="error"))
            if timeout and timeout > FOREGROUND_MAX_TIMEOUT:
                promoted = timeout
    else:
        auto_bg = None

    return _ExecPlan(
        config=config, env_type=env_type, effective_task_id=effective_task_id,
        cwd=cwd, effective_timeout=timeout or config["timeout"],
        promoted_from_foreground_timeout=promoted, auto_background_command=auto_bg,
    )


_PROMOTED_NOTE_POLL_ONLY = (
    "Requested foreground timeout {requested}s exceeds the {cap}s cap, so this command was started as a "
    "tracked background process instead of being refused. Do NOT re-run it. This session cannot receive "
    "completion notifications, so poll it with process(action=\"poll\", session_id=...) until it exits."
)


def _with_promoted_note(result_json: str, requested_timeout: int) -> str:
    """Attach the foreground->background promotion note to a spawn result (unchanged on error). The
    note only promises a notification when the spawn actually kept notify_on_complete (finite sessions
    such as one-shot runners cannot route one back; the spawn already said so and cleared the flag)."""
    try:
        data = json.loads(result_json)
    except (TypeError, ValueError):
        return result_json
    if not isinstance(data, dict) or data.get("error"):
        return result_json
    template = _PROMOTED_NOTE if data.get("notify_on_complete") else _PROMOTED_NOTE_POLL_ONLY
    data["promoted_from_foreground"] = template.format(requested=requested_timeout, cap=FOREGROUND_MAX_TIMEOUT)
    return json.dumps(data, ensure_ascii=False)


def _acquire_env(plan: _ExecPlan, task_id: Optional[str]) -> Any:
    """Cached env for the task, else create it under the per-task creation lock.

    Concurrent calls for the same task_id wait for the first sandbox instead
    of each creating their own; the cache is re-checked under that lock.
    Raises :class:`_Rejected` with the ``"disabled"`` envelope when creation
    raises ImportError.
    """
    _start_cleanup_thread()
    env_type, eff = plan.env_type, plan.effective_task_id

    with _env_lock:
        env: Any = _lookup_active_env(eff, task_id)
    if env is not None:
        return env

    with _creation_locks_lock:
        task_lock = _creation_locks.setdefault(eff, threading.Lock())

    with task_lock:
        with _env_lock:
            env = _lookup_active_env(eff, task_id)
        if env is not None:
            return env

        logger.info("Creating new %s environment for task %s...", env_type, eff[:8])
        try:
            new_env = _create_configured_env(
                plan.config, env_type, cwd=plan.cwd, timeout=plan.effective_timeout,
            )
        except ImportError as e:
            raise _Rejected(_error_json(
                _redact_terminal_error_text(f"Terminal tool disabled: environment creation failed ({e})"),
                status="disabled",
            ))

        with _env_lock:
            _active_environments[eff] = new_env
            _last_activity[eff] = time.time()
        logger.info("%s environment ready for task %s", env_type, eff[:8])
        return new_env


def _yield_kwargs(command: str, **ctx) -> dict:
    """``env.execute`` kwargs enabling yield-to-background (local backend only)."""
    handler = yield_to_background_handler(command=command, **ctx)
    return {"yield_handler": handler} if handler is not None else {}


def _run_foreground(
    command: str, env: Any, plan: _ExecPlan, *,
    task_id: Optional[str], session_id: Optional[str], session_key: str,
    workdir: Optional[str], approval_note: Optional[str], clear_interrupt: bool,
) -> str:
    """Execute in the foreground with retry on transient errors, then finalize."""
    max_retries = 3
    env_type, eff, effective_timeout = plan.env_type, plan.effective_task_id, plan.effective_timeout

    # Clean interrupt slate for an approved command, ONCE before the retry
    # loop: drop a stale bit that landed during the approval-wait so it
    # can't SIGINT the just-approved run. Do NOT re-clear inside the loop —
    # a genuine interrupt during the backoff sleep must survive and abort
    # the next attempt (rc 130).
    if clear_interrupt:
        from tools.interrupt import clear_current_thread_interrupt
        clear_current_thread_interrupt()

    for retry_count in range(max_retries + 1):
        try:
            command_cwd = _resolve_command_cwd(
                workdir=workdir, default_cwd=plan.cwd, session_key=session_key,
            )
            # bounded_capture: model-facing output keeps a head/tail window
            # while streaming so a verbose command can't OOM the gateway;
            # internal env.execute() consumers stay unbounded.
            result = env.execute(
                command, timeout=effective_timeout, cwd=command_cwd, bounded_capture=True,
                **_yield_kwargs(command, env_type=env_type, cwd=command_cwd, effective_task_id=eff,
                                task_id=task_id, session_key=session_key),
            )
            break
        except Exception as e:
            # An execution exception never reached an exit status, so it
            # is not a terminal outcome; Hermes' own deadline arrives as ``hermes_timed_out``.
            if "timeout" in str(e).lower():
                return _error_json(f"Command timed out after {effective_timeout} seconds", exit_code=124)
            # Retry on transient errors
            if retry_count < max_retries:
                wait_time = 2 ** (retry_count + 1)
                logger.warning("Execution error, retrying in %ds (attempt %d/%d) - Command: %s - Error: %s: %s - Task: %s, Backend: %s",
                               wait_time, retry_count + 1, max_retries, _safe_command_preview(command), type(e).__name__, e, eff, env_type)
                time.sleep(wait_time)
                continue
            logger.error("Execution failed after %d retries - Command: %s - Error: %s: %s - Task: %s, Backend: %s",
                         max_retries, _safe_command_preview(command), type(e).__name__, e, eff, env_type)
            return _error_json(_redact_terminal_error_text(f"Command execution failed: {type(e).__name__}: {e}"))

    if result.get("yielded_session_id"):  # handed to the background: no exit status yet
        return json.dumps({
            "output": result.get("output", ""), "exit_code": None, "error": None,
            "status": "yielded_to_background", "session_id": result["yielded_session_id"],
            "pid": result.get("pid"), "notify_on_complete": True, "note": _YIELDED_NOTE,
        }, ensure_ascii=False)

    return finalize_foreground_result(
        command=command, result=result, env=env, env_type=env_type, effective_task_id=eff,
        task_id=task_id, session_id=session_id, session_key=session_key, workdir=workdir,
        command_cwd=command_cwd, approval_note=approval_note,
    )


# Floor for the pre-exec guard's share of the command deadline: a short command timeout
# (1s in tests, a few seconds in practice) must not turn the guard's own cold-start cost
# (module imports, git probes under load) into a refusal; the wedge it bounds lasted an hour.
_PRE_EXEC_GUARD_MIN_TIMEOUT_S = 30


def _pre_exec_block(
    command: str, *, env: Any, env_type: str, cwd: str,
    workdir: Optional[str], session_key: str,
) -> None:
    """Raise :class:`_Rejected` with the blocked-result JSON when the command must not run.

    Order matters: gateway lifecycle first (protects the running gateway),
    then the dangerous-workdir check, then the self-repo guard (local only).
    """
    blocked = gateway_lifecycle_block(
        command=command, env=env, env_type=env_type, cwd=cwd, workdir=workdir, session_key=session_key,
    )
    if blocked:
        raise _Rejected(blocked)
    if workdir:
        workdir_error = _validate_workdir(workdir)
        if workdir_error:
            logger.warning("Blocked dangerous workdir: %s (command: %s)",
                           workdir[:200], _safe_command_preview(command))
            raise _Rejected(_error_json(workdir_error, status="blocked"))
    if env_type == "local":
        blocked = locked_skill_block(command=command, cwd=cwd, workdir=workdir, session_key=session_key)
        if blocked:
            raise _Rejected(blocked)


_PTY_DISABLED_REASON = (
    "PTY disabled for this command because it expects piped stdin/EOF "
    "(for example gh auth login --with-token). For local background "
    "processes, call process(action='close') after writing so it receives "
    "EOF."
)




def terminal_tool(
    command: str,
    background: bool = False,
    timeout: Optional[int] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    force: bool = False,
    workdir: Optional[str] = None,
    pty: bool = False,
    notify_on_complete: bool = False,
    watch_patterns: Optional[list[str]] = None,

    _completion_output_chars: int = 0,
    heartbeat: int = 0,
    persist_on_release: bool = False,
) -> str:
    """Execute *command* in the configured terminal environment; returns a JSON string.

    ``force`` (internal, not in the model schema) skips the dangerous-command
    check after the user confirmed. ``workdir`` is per-command and never
    recorded as the session cwd. ``pty`` applies to the local backend only.
    ``notify_on_complete`` and ``watch_patterns`` are mutually exclusive
    background-only flags: on conflict watch_patterns is dropped. watch_patterns
    is hard rate-limited (1 notification / 15s / process) and auto-disabled
    after repeated strikes or a lifetime cap, promoting to notify_on_complete —
    use it only for rare one-shot signals on long-lived processes. ``heartbeat`` (seconds,
    background-only, implies notify_on_complete) emits a "still running + output since last
    time" event every N seconds so the agent stays current on a long job without polling.
    ``persist_on_release`` (background-only) keeps the process alive across agent-lifecycle
    cleanup — session end, context compression, error recovery, max-iteration stop — all of
    which kill the task's background processes; the user can still stop it on purpose via
    process_manage kill (#41225).
    ``_completion_output_chars`` (internal) sizes the completion notification's output for a
    spawner whose output is the payload (a bot DM's reply); 0 keeps the usual tail.

    """

    plan = None
    try:
        plan = _plan_execution(
            command, task_id=task_id, timeout=timeout, background=background,
        )
        auto_bg = getattr(plan, "auto_background_command", None)
        if auto_bg is not None:
            # From here on this IS a background=true call for the stripped command: every guard
            # below (lifecycle, workdir, self-repo, dangerous-command approval) sees what runs.
            command, background = auto_bg, True
        env = _acquire_env(plan, task_id)
        # _lookup_active_env stamps whichever of the two keys holds the env.
        with _call_in_flight(plan.effective_task_id, task_id):
            env_type, cwd, effective_task_id = plan.env_type, plan.cwd, plan.effective_task_id

            # Session key for cwd records: the contextvar doesn't cross tool-worker
            # threads, so fall back to the raw task_id (the top-level agent's
            # session_key) as a stable anchor.
            from tools.approval import get_current_session_key

            session_key = get_current_session_key(default="") or (task_id or "")

            # The supervised-gateway identity probe ends in a kernel process query
            # (psutil create_time) that has wedged for the better part of an hour on
            # macOS; ``env.execute`` is already behind ``run_bounded_sync`` but this
            # chain ran ahead of it, so the tool call never returned and the cron
            # slot stayed occupied (#111922). Share the command's own deadline. A
            # guard that never rendered a verdict fails CLOSED: these checks apply
            # unconditionally (``force`` cannot bypass them), so the command is
            # refused with a retryable error instead of running unguarded.
            from agent.deadline import run_bounded_sync
            from tools.interrupt import acting_for_tid

            # The guard chain runs on the deadline worker; keep it answerable to /stop
            # aimed at this tool thread.
            guard_timeout = max(plan.effective_timeout, _PRE_EXEC_GUARD_MIN_TIMEOUT_S)
            _acting_token = acting_for_tid.set(threading.current_thread().ident)
            try:
                bounded_guard = run_bounded_sync(
                    lambda: _pre_exec_block(
                        command, env=env, env_type=env_type, cwd=cwd, workdir=workdir, session_key=session_key,
                    ),
                    guard_timeout,
                    label="terminal.pre-exec-guard",
                )
            finally:
                acting_for_tid.reset(_acting_token)
            if bounded_guard.timed_out:
                raise _Rejected(_error_json(
                    f"Terminal pre-execution guard did not finish within {guard_timeout}s "
                    "(process-identity probe wedged); the command was not run. Retry the call.",
                    status="error",
                ))
            # Pre-exec security checks (floors + dangerous command detection);
            # force=True means the user already confirmed.
            verdict = _run_approval_guards(command, env_type, plan.config, force=force)

            pty_disabled = pty and _command_requires_pipe_stdin(command)
            if plan.promoted_from_foreground_timeout is not None:
                # Promotion implies notify_on_complete; watch_patterns is a background-only flag the
                # caller could not have meant for a foreground call, and the two are exclusive anyway.
                background, notify_on_complete, watch_patterns = True, True, None
            if background:
                result = spawn_background_process(
                    command=command, env=env, env_type=env_type, effective_task_id=effective_task_id,
                    task_id=task_id, session_key=session_key, workdir=workdir, cwd=cwd,
                    effective_pty=pty and not pty_disabled, notify_on_complete=notify_on_complete,
                    watch_patterns=watch_patterns, approval_note=verdict.note,
                    pty_disabled_reason=_PTY_DISABLED_REASON if pty_disabled else None,
                    completion_output_chars=_completion_output_chars,
                    heartbeat_seconds=heartbeat,
                    persist_on_release=persist_on_release,
                    auto_backgrounded=auto_bg is not None and plan.promoted_from_foreground_timeout is None,
                )
                if plan.promoted_from_foreground_timeout is not None:
                    result = _with_promoted_note(result, plan.promoted_from_foreground_timeout)
                return result
            return _run_foreground(
                command, env, plan,
                task_id=task_id, session_id=session_id, session_key=session_key,
                workdir=workdir, approval_note=verdict.note, clear_interrupt=verdict.approved_run,
            )
    except _Rejected as r:
        return r.result_json
    except Exception as e:
        return _fatal_error_json(e)


def check_terminal_requirements() -> bool:
    """Enable local execution only when the configured selector is supported."""
    try:
        _get_env_config()
    except Exception as exc:
        _record_unavailable_reason(str(exc))
        logger.warning("Terminal tool unavailable: %s", exc)
        return False
    _record_unavailable_reason(None)
    return True


from tools.registry import registry

TERMINAL_SCHEMA = {
    "name": "terminal",
    "description": TERMINAL_TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute"
            },
            "background": {
                "type": "boolean",
                "description": "Keep running after this call returns; returns a session_id.",
                "default": False
            },
            "timeout": {
                "type": "integer",
                "description": f"Max seconds (default 180, foreground max {FOREGROUND_MAX_TIMEOUT}). Finishing early returns at once, so set it generously. Above {FOREGROUND_MAX_TIMEOUT}s the command runs as a tracked background process with notify_on_complete=true (the result says so; do not re-run it).",
                "minimum": 1
            },
            "workdir": {
                "type": "string",
                "description": "Absolute working directory for this command only."
            },
            "pty": {
                "type": "boolean",
                "description": "With background=true: pseudo-terminal for interactive CLIs (REPLs, coding agents); drive it with process_manage write/submit. Local backend only.",
                "default": False
            },
            "notify": {
                "description": "With background=true. true: one notification on exit; use it for anything that ends (builds, tests, deploys), or the job runs silently. ['pattern', ...]: notify when a line matches, only for one-shot readiness on processes that never exit; rate-limited. Omit for servers/daemons.",
                "anyOf": [
                    {"type": "boolean"},
                    {"type": "array", "items": {"type": "string"}}
                ]
            },
            "heartbeat": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "With background=true: also notify every N seconds (min 60) with new output; quiet ticks are skipped. For bounded jobs you must react to mid-run, never servers. Implies notify=true; 0 = off.",
            },
            "persist_on_release": {
                "type": "boolean",
                "default": False,
                "description": "With background=true: survive session end, /new, compression and other cleanup (not host exit). Only for jobs the user explicitly wants to outlive the conversation."
            }

        },
        "required": ["command"]
    }
}


def _handle_terminal(args, **kw):
    from agent.terminal_approval_batch import validate_prepared_terminal
    validate_prepared_terminal(args)
    # Models sometimes send execute_code's ``code`` here; name the stray
    # argument and the right tool instead of failing on command=None.
    if "command" not in args and "code" in args:
        return tool_error(
            "terminal received a 'code' parameter, but it requires a shell "
            "command in 'command'. Use execute_code(code=...) for Python; "
            "for shell, retry as terminal(command=...)."
        )
    # `notify` maps to the internal completion and pattern flags. Notification
    # intent on a foreground call is refused with
    # the corrected call. A foreground `heartbeat` carries no intent: it has
    # no execution meaning without a tracked process, and providers that
    # materialize every schema property (or models copying a background call
    # shape) send `heartbeat=60` on ordinary commands — refusing it produced
    # identical-call retry loops (46 subagent sessions, 4 refusals each, 38 of
    # which never ran a single command), so it is normalized away instead.
    if "notify_on_complete" in args or "watch_patterns" in args:
        return tool_error("Use notify=true or notify=[patterns] instead of retired notification arguments.")
    notify = args.get("notify")
    notify_on_complete = False
    watch_patterns = None
    heartbeat = args.get("heartbeat") or 0
    persist_on_release = bool(args.get("persist_on_release", False))
    if not isinstance(heartbeat, int) or isinstance(heartbeat, bool) or heartbeat < 0:
        return tool_error("heartbeat must be a whole number of seconds (0 disables; positive values are clamped to min 60).")
    if not args.get("background", False):
        if notify or watch_patterns or notify_on_complete:
            return tool_error(
                "notify only applies to background commands (foreground "
                "results return directly). Either drop it, or run as "
                "terminal(command=..., background=true, notify=...)."
            )
        heartbeat = 0
        if args.get("pty", False):
            return tool_error(
                "pty requires background=true (a PTY session is interacted "
                "with via process(action='write'/'submit'), which needs a "
                "tracked background process). Retry as terminal(command=..., "
                "background=true, pty=true)."
            )
        if persist_on_release:
            return tool_error(
                "persist_on_release only applies to background commands (a foreground "
                "process is awaited inline and has nothing to persist). Retry as "
                "terminal(command=..., background=true, persist_on_release=true)."
            )
    if notify is not None:
        if isinstance(notify, bool):
            notify_on_complete = notify
            watch_patterns = None
        elif isinstance(notify, list):
            watch_patterns = notify
            notify_on_complete = False
        else:
            return tool_error(
                "notify must be true/false (notify on exit) or a list of "
                "strings (notify on output pattern match)."
            )
    if heartbeat:
        notify_on_complete = True  # the heartbeat rides the completion delivery path
    return terminal_tool(
        command=args.get("command"),
        background=args.get("background", False),
        timeout=args.get("timeout"),
        task_id=kw.get("task_id"),
        session_id=kw.get("session_id"),
        workdir=args.get("workdir"),
        pty=args.get("pty", False),
        notify_on_complete=notify_on_complete,
        watch_patterns=watch_patterns,
        heartbeat=heartbeat,
        persist_on_release=persist_on_release,
    )


registry.register(
    name="terminal",
    toolset="terminal",
    schema=TERMINAL_SCHEMA,
    handler=_handle_terminal,
    check_fn=check_terminal_requirements,
    emoji="💻",
    max_result_size_chars=100_000,
)
