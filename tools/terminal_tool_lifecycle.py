"""Local shell lifecycle for the terminal tool: idle reaping, teardown, manual/atexit
cleanup, and the lazy ensure_task_env bring-up. The env cache dicts and locks
stay in tools.terminal_tool (tests patch them there) and are read through it
at call time.

Split out of ``tools/terminal_tool.py``; every public/patched name is re-imported there,
so ``tools.terminal_tool.<name>`` keeps resolving (and monkeypatching) as before.
"""

import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional
from tools.terminal_tool_backends import (
    _local_config_from_config,
)
from tools.terminal_tool_config import _quiet

# Log-record parity with the origin module.
logger = logging.getLogger("tools.terminal_tool")


def _create_configured_env(config: dict[str, Any], env_type: str, *, cwd: str, timeout: int):
    """Construct the local environment using the shared persistence setting."""
    from tools.terminal_tool_backends import _create_environment
    return _create_environment(env_type=env_type, cwd=cwd, timeout=timeout,
                               local_config=_local_config_from_config(config))


def _cleanup_env(env: Any) -> None:
    """Close a local environment's persistent shell."""
    env.cleanup()


def _teardown_env(env: Any, task_id: str, *, done_msg: str = "Cleaned up inactive environment for task: %s") -> None:
    try:
        _cleanup_env(env)
        logger.info(done_msg, task_id)
    except Exception as exc:
        logger.warning("Error cleaning up environment for task %s: %s", task_id, exc)


def _clear_file_ops_cache(task_id: str) -> None:
    """Invalidate the file_ops cache entry so ShellFileOperations can't reference a dead sandbox."""
    try:
        from tools.file_tools import clear_file_ops_cache
        clear_file_ops_cache(task_id)
    except ImportError:
        pass


def _unregister_env(task_id: str):
    """Pop *task_id* from the env cache, activity map and creation locks; return
    the env (or None). Callers run teardown outside the lock so closing a
    persistent shell does not stall concurrent terminal/file calls."""
    from tools.terminal_tool import (
        _active_environments, _creation_locks, _creation_locks_lock, _env_lock,
        _last_activity,
    )
    with _env_lock:
        env = _active_environments.pop(task_id, None)
        _last_activity.pop(task_id, None)
    with _creation_locks_lock:
        _creation_locks.pop(task_id, None)
    return env


@contextmanager
def _call_in_flight(*keys: Optional[str]):
    """Hold the env cached under *keys* against the idle reaper for the duration of a call.

    Only background processes on workspace keys refresh ``_last_activity`` mid-run, so a
    foreground command longer than ``lifetime_seconds`` had its sandbox reaped out from under
    it. Refcounted (parallel calls share a key); exit restamps, so the idle countdown starts at
    call end.
    """
    from tools.terminal_tool import _calls_in_flight, _env_lock, _last_activity
    held = [k for k in dict.fromkeys(keys) if k]
    with _env_lock:
        for k in held:
            _calls_in_flight[k] = _calls_in_flight.get(k, 0) + 1
    try:
        yield
    finally:
        with _env_lock:
            now = time.time()
            for k in held:
                left = _calls_in_flight.pop(k) - 1
                if left:
                    _calls_in_flight[k] = left
                if k in _last_activity:
                    _last_activity[k] = now


def _cleanup_inactive_envs(lifetime_seconds: int = 300):
    """Clean up environments that have been inactive for longer than lifetime_seconds."""
    from tools.terminal_tool import (
        _active_environments, _calls_in_flight, _creation_locks, _creation_locks_lock, _env_lock,
        _last_activity,
    )
    current_time = time.time()

    # Shells with active background processes stay alive (refresh activity).
    try:
        from tools.process_registry import process_registry
        for task_id in list(_last_activity.keys()):
            if process_registry.has_active_processes(task_id):
                _last_activity[task_id] = current_time
    except ImportError:
        pass

    # Phase 1: unregister stale entries atomically under the lock; phase 2:
    # stop them outside it (see _unregister_env for why).
    with _env_lock:
        # A key with a foreground call in flight is busy, however old its stamp.
        stale = [t for t, last in list(_last_activity.items())
                 if current_time - last > lifetime_seconds and t not in _calls_in_flight]
        envs_to_stop = [(t, _active_environments.pop(t, None)) for t in stale]
        for t in stale:
            _last_activity.pop(t, None)
        with _creation_locks_lock:
            for t in stale:
                _creation_locks.pop(t, None)
    for task_id, env in envs_to_stop:
        if env is not None:
            _clear_file_ops_cache(task_id)
            _teardown_env(env, task_id)




def get_active_env(task_id: str):
    """Return the active BaseEnvironment for *task_id*, or None."""
    from tools.terminal_tool import _active_environments, _env_lock, _resolve_task_id
    lookup = _resolve_task_id(task_id)
    with _env_lock:
        return _active_environments.get(lookup)




def is_persistent_env(task_id: str) -> bool:
    """True if *task_id*'s active env persists across turns.

    The agent loop skips per-turn teardown for persistent shells;
    other shells are torn down at the end of the turn, and the idle reaper
    handles the rest.
    """
    env = get_active_env(task_id)
    if env is None:
        return False
    return bool(getattr(env, "_persistent", False))


def cleanup_all_environments():
    """Clean up ALL active environments (process exit). Use with caution."""
    from tools.environments.base import kill_live_foreground_processes
    from tools.terminal_tool import _active_environments
    # A command still running when the host exits would outlive it in its own process group.
    kill_live_foreground_processes()
    cleaned = 0
    for task_id in list(_active_environments.keys()):
        try:
            env = _unregister_env(task_id)
            _clear_file_ops_cache(task_id)
            if env is not None:
                _teardown_env(env, task_id)
            cleaned += 1
        except Exception as e:
            logger.error("Error cleaning %s: %s", task_id, e, exc_info=True)

    if cleaned > 0:
        logger.info("Cleaned %d environments", cleaned)
    return cleaned


def cleanup_vm(task_id: str):
    """Close the local shell cached for a resolved task key."""
    from tools.terminal_tool import _resolve_task_id
    key = _resolve_task_id(task_id)
    env = _unregister_env(key)
    _clear_file_ops_cache(key)
    if env is not None:
        _teardown_env(env, task_id, done_msg="Manually cleaned up environment for task: %s")


def _evict_environment_for_task(task_id: Optional[str]) -> None:
    """Drop only this profile's cached shell after an execution failure."""
    from tools.terminal_tool import (
        _active_environments, _env_lock, _last_activity, _resolve_task_id,
    )
    keys = {_resolve_task_id(task_id)}

    evicted = []
    with _env_lock:
        for key in keys:
            env = _active_environments.pop(key, None)
            _last_activity.pop(key, None)
            if env is not None:
                evicted.append(env)
    for env in evicted:
        with _quiet("cleanup of degraded environment failed"):
            env.cleanup()
