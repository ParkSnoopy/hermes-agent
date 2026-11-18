"""Authenticated local RPC server for execute_code kernels."""

import json
import logging
import secrets
import socket
import threading
import time

from agent.thread_scoped_output import thread_scoped_silence
from tools.registry import tool_error

# Logger name kept as the origin module's so existing log expectations hold.
logger = logging.getLogger("tools.code_execution_tool")

# Terminal parameters that must not be used from ephemeral sandbox scripts.
_TERMINAL_BLOCKED_PARAMS = {"background", "pty", "notify", "notify_on_complete", "watch_patterns", "heartbeat", "persist_on_release"}


def _default_dispatch(task_id):
    from model_tools import handle_function_call
    return lambda tool_name, tool_args: handle_function_call(tool_name, tool_args, task_id=task_id)


def _rpc_token_ok(request: dict, rpc_token: str) -> bool:
    """Constant-time token check; an empty server token fails closed. Compared as bytes:
    compare_digest raises TypeError on a non-ASCII str, and the token is script-supplied JSON."""
    return bool(rpc_token) and secrets.compare_digest(
        str(request.get("token") or "").encode(), rpc_token.encode()
    )


def _handle_rpc_request(request: dict, *, allowed_tools: frozenset, tool_call_counter: list,
                        max_tool_calls: int, dispatch, tool_call_log: list, call_start: float,
                        where: str) -> str:
    """Enforce allow-list + budget, then dispatch one authenticated request. Only a dispatched
    call consumes budget and is logged; refusals are free."""
    tool_name = request.get("tool", "")
    tool_args = request.get("args", {})
    if tool_name not in allowed_tools:
        return tool_error(f"Tool '{tool_name}' is not available in execute_code. "
                          f"Available: {', '.join(sorted(allowed_tools))}")
    if tool_call_counter[0] >= max_tool_calls:
        return tool_error(f"Tool call limit reached ({max_tool_calls}). "
                          "No more tool calls allowed in this execution.")
    if tool_name == "terminal" and isinstance(tool_args, dict):
        for param in _TERMINAL_BLOCKED_PARAMS:
            tool_args.pop(param, None)
    # Silence handler status prints so they don't leak into the CLI spinner.
    try:
        with thread_scoped_silence():
            result = dispatch(tool_name, tool_args)
    except Exception as exc:
        logger.error("Tool call failed in %s: %s", where, exc, exc_info=True)
        result = tool_error(str(exc))
    tool_call_counter[0] += 1
    entry = {"tool": tool_name, "args_preview": str(tool_args)[:80],
             "duration": round(time.monotonic() - call_start, 2)}
    error = _result_error(result)
    if error:
        entry["error"] = error
    tool_call_log.append(entry)
    return result


def _result_error(result) -> str:
    """The ``error`` text of a JSON-object tool result, else ``""``."""
    if not isinstance(result, str) or not result.startswith("{") or '"error"' not in result:
        return ""
    try:
        body = json.loads(result)
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    return str(error)[:300] if error else ""


def tool_errors_since(tool_call_log: list, start: int = 0) -> list:
    """Failed in-script tool calls since *start*, for the execute_code result: a script that
    ignores a helper's ``{"error": ...}`` return would otherwise report plain success while
    the write/patch it relied on never happened."""
    return [{"tool": e["tool"], "error": e["error"]} for e in tool_call_log[start:] if e.get("error")][:5]


def _rpc_server_loop(server_sock: socket.socket, task_id: str, tool_call_log: list,
                     tool_call_counter: list, max_tool_calls: int, allowed_tools: frozenset,
                     stop_event: threading.Event, rpc_token: str, dispatch=None):
    """Accept one client and serve newline-delimited JSON requests until it disconnects, idles
    300s, or the call limit is reached. ``tool_call_counter`` is a mutable ``[int]``. ``dispatch``
    overrides how an allowed, budgeted call runs: per-call sandboxes use the default (the thread
    carries the cell's context); session kernels rebind each call to the CURRENT cell's authority.
    """
    if dispatch is None:
        dispatch = _default_dispatch(task_id)
    conn = None
    try:
        server_sock.settimeout(0.05)
        while not stop_event.is_set():
            try:
                conn, _ = server_sock.accept()
                break
            except TimeoutError:
                continue
        if conn is None:
            return
        conn.settimeout(300)
        buf = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except TimeoutError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                call_start = time.monotonic()
                try:
                    request = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    resp = tool_error(f"Invalid RPC request: {exc}")
                else:
                    resp = _handle_rpc_request(
                        request, allowed_tools=allowed_tools, tool_call_counter=tool_call_counter,
                        max_tool_calls=max_tool_calls, dispatch=dispatch, tool_call_log=tool_call_log,
                        call_start=call_start, where="sandbox",
                    ) if _rpc_token_ok(request, rpc_token) else tool_error("Unauthorized RPC request")
                conn.sendall((resp + "\n").encode())
    except TimeoutError:
        logger.debug("RPC listener socket timeout")
    except OSError as e:
        logger.debug("RPC listener socket error: %s", e, exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except OSError as e:
                logger.debug("RPC conn close error: %s", e)
