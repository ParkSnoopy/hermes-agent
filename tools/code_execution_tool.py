#!/usr/bin/env python3
"""Code Execution Tool -- Programmatic Tool Calling (PTC).

The LLM writes a Python script that calls Hermes tools via RPC, collapsing
multi-step tool chains into one inference turn; only the script's stdout returns
to the LLM. A persistent per-conversation session kernel
(tools/code_kernel.py) uses a Unix socket (loopback TCP on Windows).
Siblings: tools/code_execution_env.py (env
scrubbing, interpreter/cwd), tools/code_execution_rpc.py (RPC servers).
"""

import json
import logging
import re
import subprocess
from typing import Any, Optional

from tools.registry import registry, tool_error

from tools.code_execution_env import _resolve_child_cwd, _resolve_child_python
from tools.tool_output_truncate import head_tail_split, truncation_notice

logger = logging.getLogger(__name__)

# Loopback TCP replaces AF_UNIX on Windows, so execute_code runs on every platform Hermes does.
SANDBOX_AVAILABLE = True

# Tools allowed inside the sandbox; ∩ the session's enabled tools decides which stubs are generated.
SANDBOX_ALLOWED_TOOLS = frozenset([
    "web_search", "web_extract", "read_file", "write_file", "search_files", "patch", "terminal",
])

# Resource limit defaults (overridable via config.yaml → code_execution.*)
DEFAULT_TIMEOUT = 300        # 5 minutes
DEFAULT_MAX_TOOL_CALLS = 50
MAX_STDOUT_BYTES = 50_000    # 50 KB
MAX_STDERR_BYTES = 10_000    # 10 KB
# Hard ceiling on the spilled file (as web_tools' MAX_STORED_TEXT_CHARS): a runaway print loop must not fill the disk.
MAX_SPILLED_STDOUT_BYTES = 5_000_000


def _truncate_stdout_text(stdout_text: str) -> tuple[str, dict[str, Any]]:
    """Cap stdout by bytes (40% head / 60% tail) with explicit truncation metadata: byte counts
    ride alongside the textual marker because a client layer can miss or re-truncate it. The
    omitted middle is spilled to cache/exec and the result carries the path (recover-don't-rerun)."""
    stdout_bytes = stdout_text.encode("utf-8", errors="replace")
    total = len(stdout_bytes)
    captured = min(total, MAX_STDOUT_BYTES)
    metadata: dict[str, Any] = {"stdout_truncated": total > captured, "stdout_bytes_captured": captured,
                                "stdout_bytes_total": total, "stdout_bytes_omitted": total - captured}
    if total <= MAX_STDOUT_BYTES:
        return stdout_bytes.decode("utf-8", errors="replace"), metadata
    head_bytes, tail_bytes = head_tail_split(MAX_STDOUT_BYTES)
    text = (stdout_bytes[:head_bytes].decode("utf-8", errors="replace")
            + truncation_notice(total - captured, total, unit="bytes")
            + stdout_bytes[-tail_bytes:].decode("utf-8", errors="replace"))
    metadata["warning"] = ("execute_code stdout was truncated; the script did run, but only "
                           "the captured head/tail output is included. Re-run only with "
                           "narrower output if the omitted data is required.")
    spill_path = _spill_full_stdout(stdout_text)
    if spill_path:
        metadata["stdout_spill_path"] = spill_path
        metadata["warning"] = ("execute_code stdout was truncated (head/tail shown); the "
                               f"script did run. FULL output saved to {spill_path} — page it "
                               f'with read_file(path="{spill_path}", offset=...) instead of re-running.')
    return text, metadata


def _spill_full_stdout(stdout_text: str) -> Optional[str]:
    """Write full stdout to cache/exec; return its path (None on failure — best-effort,
    the truncated inline output is still returned). Keyed by content digest so identical
    reruns coalesce."""
    try:
        import hashlib
        from hermes_constants import get_hermes_dir
        from tools.spill_safety import write_text_exclusive
        if len(stdout_text) > MAX_SPILLED_STDOUT_BYTES:
            stdout_text = (stdout_text[:MAX_SPILLED_STDOUT_BYTES]
                           + f"\n\n[... spill capped at {MAX_SPILLED_STDOUT_BYTES:,} bytes ...]")
        cache_dir = get_hermes_dir("cache/exec", "exec_spill")
        cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(stdout_text.encode("utf-8", errors="replace")).hexdigest()[:12]
        path = cache_dir / f"stdout-{digest}.txt"
        write_text_exclusive(path, stdout_text, private=False, overwrite=True)
        return str(path)
    except Exception as exc:
        logger.debug("Failed to spill execute_code stdout: %s", exc)
        return None


# ---- hermes_tools.py code generator ----

# Per-tool stub templates: (signature, docstring, args_dict_expr — the JSON payload sent over RPC).
_TOOL_STUBS = {
    "web_search": ("query: str, limit: int = 5",
        '"""Search the web. Returns dict with data.web list of {url, title, description}."""',
        '{"query": query, "limit": limit}'),
    "web_extract": ("urls: list, char_limit: int = None",
        '"""Extract content from URLs (no LLM summarization). Returns dict with results list of {url, title, content, error}. Pages over char_limit (default 15000) are head+tail truncated with the full text stored on disk; the content footer gives the path. content is markdown."""',
        '{"urls": urls, "char_limit": char_limit}'),
    "read_file": ("path: str, offset: int = 1, limit: int = 2000",
        '"""Read a file (1-indexed lines). Returns dict with "content" and "total_lines"."""',
        '{"path": path, "offset": offset, "limit": limit}'),
    "write_file": ("path: str, content: str, cross_profile: bool = False",
        '"""Write content to a file (always overwrites). Returns dict with status."""',
        '{"path": path, "content": content, "cross_profile": cross_profile}'),
    "search_files": ('pattern: str, target: str = "content", path: str = ".", file_glob: str = None, limit: int = 50, offset: int = 0, output_mode: str = "content", context: int = 0, order: str = "discovery"',
        '"""Search file contents (target="content") or find files by name (target="files"). Returns dict with "matches"."""',
        '{"pattern": pattern, "target": target, "path": path, "file_glob": file_glob, "limit": limit, "offset": offset, "output_mode": output_mode, "context": context, "order": order}'),
    "patch": ('path: str = None, old_string: str = None, new_string: str = None, replace_all: bool = False, mode: str = "replace", patch: str = None, cross_profile: bool = False',
        '"""Targeted find-and-replace (mode="replace") or V4A multi-file patches (mode="patch"). Returns dict with status."""',
        '{"path": path, "old_string": old_string, "new_string": new_string, "replace_all": replace_all, "mode": mode, "patch": patch, "cross_profile": cross_profile}'),
    "terminal": ("command: str, timeout: int = None, workdir: str = None",
        '"""Run a shell command (foreground only). Returns dict with "output" and "exit_code"."""',
        '{"command": command, "timeout": timeout, "workdir": workdir}'),
}


def _missing_hermes_tools_import_hint(m, enabled_tools) -> str:
    missing = m.group(1)
    if missing in {"json_parse", "shell_quote", "retry"}:
        return (f"Import helpers with `from hermes_tools import {missing}`. "
                "If that import failed, the generated module may be stale or another "
                "hermes_tools may be first on sys.path. Check hermes_tools.__file__ "
                "and retry with reset=true.")
    available = sorted(SANDBOX_ALLOWED_TOOLS & set(enabled_tools or SANDBOX_ALLOWED_TOOLS))
    return (f"'{missing}' is not available inside the execute_code sandbox. "
            f"Importable tools here: {', '.join(available)}. For anything "
            "else, use the normal tool call instead of execute_code.")


# (regex, formatter(match, enabled_tools)) — first match wins. Production mining (state.db) ranked
# these as the top execute_code failure classes: hermes_tools import misuse, missing helper
# imports, treating tool results as strings, importing third-party packages absent from the sandbox.
_FAILURE_HINT_RULES = (
    (r"cannot import name '(\w+)' from 'hermes_tools'", _missing_hermes_tools_import_hint),
    (r"NameError: name '(json_parse|shell_quote|retry)' is not defined",
     lambda m, _: f"Import {m.group(1)} before calling it: "
                  f"from hermes_tools import {m.group(1)}"),
    (r"ModuleNotFoundError: No module named '([\w.]+)'",
     lambda m, _: f"'{m.group(1)}' is not installed in the sandbox interpreter. "
                  "Use Python stdlib inside execute_code, or run the code via "
                  "terminal() with the project venv's python instead."),
    (r"TypeError: string indices must be integers|AttributeError: 'str' object has no attribute 'get'",
     lambda m, _: "Tool functions in the sandbox return DICTS (already parsed) — "
                  "do not json.loads() them or index them like strings. Example: read_file(path)['content']."),
)


def _sandbox_failure_hint(stderr_text: str, enabled_tools=None) -> Optional[str]:
    """Map well-known sandbox script failures to one actionable recovery hint
    (bounded scan, first match wins, never raises)."""
    if not stderr_text:
        return None
    window = stderr_text[:4000]
    try:
        for pattern, fmt in _FAILURE_HINT_RULES:
            m = re.search(pattern, window)
            if m:
                return fmt(m, enabled_tools)
    except Exception:
        return None
    return None


def generate_hermes_tools_module(enabled_tools: list[str]) -> str:
    """Source of the hermes_tools.py stub module for SANDBOX_ALLOWED_TOOLS ∩ *enabled_tools*.
    The client connects to the local authenticated socket."""
    header = _UDS_TRANSPORT_HEADER
    return header + "\n".join(
        f"def {name}({sig}):\n    {doc}\n    return _call({name!r}, {args_expr})\n"
        for name, (sig, doc, args_expr) in sorted(_TOOL_STUBS.items()) if name in set(enabled_tools)
    )


# ---- Shared helpers section (embedded in both transport headers) ----------

_COMMON_HELPERS = '''\

# ---------------------------------------------------------------------------
# Convenience helpers (avoid common scripting pitfalls)
# ---------------------------------------------------------------------------

def json_parse(text: str):
    """Parse JSON tolerant of control characters and UTF-8 BOM (strict=False).
    Use this instead of json.loads() when parsing output from terminal()
    or web_extract() that may contain raw tabs/newlines in strings,
    or from tools/files that prepend a UTF-8 BOM (salvage #57870, credit @woxinwuhen713-bit)."""
    if isinstance(text, str) and text.startswith("\ufeff"):
        text = text[1:]
    return json.loads(text, strict=False)


def shell_quote(s: str) -> str:
    """Shell-escape a string for safe interpolation into commands.
    Use this when inserting dynamic content into terminal() commands:
        terminal(f"echo {shell_quote(user_input)}")
    """
    return shlex.quote(s)


def retry(fn, max_attempts=3, delay=2):
    """Retry a function up to max_attempts times with exponential backoff.
    Use for transient failures (network errors, API rate limits):
        result = retry(lambda: terminal("gh issue list ..."))
    """
    last_err = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < max_attempts - 1:
                time.sleep(delay * (2 ** attempt))
    raise last_err

'''

# ---- UDS transport (local backend) ---------------------------------------

_UDS_TRANSPORT_HEADER = '''\
"""Auto-generated Hermes tools RPC stubs."""
import json, os, socket, shlex, threading, time

_sock = None
# The RPC server handles a single client connection serially and has no
# request-id in the protocol, so concurrent _call() invocations from multiple
# threads (e.g. ThreadPoolExecutor) would race on the shared socket and get
# each other's responses. Serialize the entire send+recv round-trip.
_call_lock = threading.Lock()
''' + _COMMON_HELPERS + '''\

def _connect():
    """Connect to the parent's RPC server via the transport it picked.

    HERMES_RPC_SOCKET can be either:
      - a filesystem path (POSIX Unix domain socket — the default on
        Linux and macOS)
      - a string of the form ``tcp://127.0.0.1:<port>`` (Windows, where
        AF_UNIX is unreliable — the parent falls back to loopback TCP)
    """
    global _sock
    if _sock is None:
        endpoint = os.environ["HERMES_RPC_SOCKET"]
        if endpoint.startswith("tcp://"):
            # tcp://host:port  (host is always 127.0.0.1 in practice — we
            # only bind loopback server-side)
            _host_port = endpoint[len("tcp://"):]
            _host, _, _port = _host_port.rpartition(":")
            _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _sock.connect((_host or "127.0.0.1", int(_port)))
        else:
            _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _sock.connect(endpoint)
        _sock.settimeout(300)
    return _sock

def _call(tool_name, args):
    """Send a tool call to the parent process and return the parsed result."""
    request = json.dumps({
        "tool": tool_name,
        "args": args,
        "token": os.environ.get("HERMES_RPC_TOKEN", ""),
    }) + "\\n"
    # Session kernels outlive the RPC server's 300s idle window, so their
    # connection can be legitimately gone by the next cell. The server
    # re-accepts (HERMES_RPC_PERSISTENT=1); retry once on a fresh socket.
    _attempts = 2 if os.environ.get("HERMES_RPC_PERSISTENT") == "1" else 1
    with _call_lock:
        for _attempt in range(_attempts):
            try:
                conn = _connect()
                conn.sendall(request.encode())
                buf = b""
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        raise RuntimeError("Agent process disconnected")
                    buf += chunk
                    if buf.endswith(b"\\n"):
                        break
                break
            except (OSError, RuntimeError):
                global _sock
                try:
                    if _sock is not None:
                        _sock.close()
                except OSError:
                    pass
                _sock = None
                if _attempt + 1 >= _attempts:
                    raise
    raw = buf.decode().strip()
    result = json.loads(raw)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result
    return result

'''

def _format_interrupted_output(stdout_text: str) -> str:
    """Append an interruption marker without guessing who caused it."""
    from tools.interrupt import get_interrupt_reason
    reason = get_interrupt_reason()
    marker = f"[execution interrupted — {reason}]" if reason else "[execution interrupted]"
    return f"{stdout_text}\n{marker}" if stdout_text else marker


def _error_result(error: str, *, tool_calls_made: int = 0, duration: float = 0,
                  user_summary: Optional[str] = None) -> str:
    body = {"status": "error", "error": error, "tool_calls_made": tool_calls_made, "duration_seconds": duration}
    if user_summary:
        body["user_summary"] = user_summary  # one human sentence; surfaces show it before the model text
    return json.dumps(body, ensure_ascii=False)


def _sandbox_tools_for(enabled_tools: Optional[list[str]]) -> frozenset:
    """Enabled ∩ SANDBOX_ALLOWED_TOOLS, or every sandbox tool when the intersection is empty."""
    return frozenset(SANDBOX_ALLOWED_TOOLS & set(enabled_tools or ())) or SANDBOX_ALLOWED_TOOLS


# ---- Main entry point ----


def execute_code(
    code: str,
    task_id: Optional[str] = None,
    enabled_tools: Optional[list[str]] = None,
    reset: bool = False,
) -> str:
    """Run Python in the session's persistent local kernel,
    with RPC access to a subset of Hermes tools; returns the JSON result string. "Sandbox" means
    the security envelope (env scrubbing, tool whitelist + call budget, output redaction), not an
    isolation jail: default `project` mode runs in the session's cwd with the project venv.
    ``enabled_tools`` ∩ SANDBOX_ALLOWED_TOOLS; ``reset`` kills the existing kernel first."""
    if not SANDBOX_AVAILABLE:
        return tool_error("execute_code sandbox is unavailable in this environment. "
                          "Use normal tool calls (terminal, read_file, write_file, ...) instead.")
    # Fail closed under a terminal-policy refusal scope: the routed profile's terminal
    # policy is unresolved, so refuse rather than inherit the launch process's ambient policy.
    try:
        # See #68559.
        from tools.terminal_scope import enforce_no_refusal
        enforce_no_refusal()
    except Exception as refusal:
        return tool_error(f"execute_code refused: {refusal} "
                          "(profile terminal policy unresolved; fix the profile's config.yaml / .env and retry)")
    if not code or not code.strip():
        return tool_error("No code provided. execute_code requires a non-empty 'code' "
                          "parameter containing Python source. To run shell commands, use terminal(command=...) instead.")
    from tools.terminal_tool import _get_env_config
    from tools.terminal_tool_config import require_local_environment
    env_type = require_local_environment(_get_env_config()["env_type"])
    # Hard-block gateway-lifecycle commands (mirrors the terminal_tool guard — otherwise
    # `os.system("launchctl bootout ...")` here bypasses it and SIGTERMs the gateway mid-task).
    # Gated on PID-file ownership, not the inherited env marker.
    # Hard-block gateway-lifecycle commands, mirroring the terminal_tool guard (#68289): without this,
    # execute_code is a straight bypass — the terminal() path refuses `launchctl bootout ai.hermes.gateway`,
    # but the identical command inside `os.system(...)` / `subprocess.run([...])` here sailed through and
    # SIGTERM'd the gateway mid-task.
    # The identity probe ends in a kernel process query that has wedged on macOS
    # (#111922); share the cell's own deadline and fail CLOSED when it renders no verdict.
    from agent.deadline import run_bounded_sync
    from tools.process_registry import _is_supervised_gateway_process
    from tools.terminal_tool import _PRE_EXEC_GUARD_MIN_TIMEOUT_S
    _probe_timeout = max(_load_config().get("timeout", DEFAULT_TIMEOUT), _PRE_EXEC_GUARD_MIN_TIMEOUT_S)
    _probe = run_bounded_sync(
        _is_supervised_gateway_process, _probe_timeout, label="execute_code.lifecycle-guard",
    )
    if _probe.timed_out:
        return tool_error(
            f"execute_code lifecycle guard did not finish within {_probe_timeout}s "
            "(process-identity probe wedged); the code was not run. Retry the call."
        )
    if _probe.value:
        from cron.lifecycle_guard import (
            HOST_INTERPRETER_KILL_REJECTION,
            contains_gateway_lifecycle_command,
            contains_host_interpreter_kill,
        )
        if contains_gateway_lifecycle_command(code):
            if contains_host_interpreter_kill(code):
                return tool_error(HOST_INTERPRETER_KILL_REJECTION)
            return tool_error(
                "Blocked: cannot restart or stop the gateway from inside the "
                "gateway process. The gateway would kill this script before "
                "it could complete (SIGTERM propagates to child processes). "
                "Run the lifecycle command from a shell outside the gateway."
            )
    # Arbitrary Python never passes through terminal()/DANGEROUS_PATTERNS, so guard the whole
    # script before spawning it — in this (tool-executor) thread, which holds the session context.
    # See #30882.
    from tools.approval import check_execute_code_guard
    _guard = check_execute_code_guard(code, env_type)
    if not _guard.get("approved", False):
        return _error_result(_guard.get("message") or "execute_code blocked by approval guard.",
                             user_summary=_guard.get("user_summary"))
    # Clear a stale interrupt bit that landed during the blocking approval-wait so it can't
    # kill the just-approved run on the first poll. A genuine post-clear interrupt re-sets it.
    if _guard.get("user_approved"):
        from tools.interrupt import clear_current_thread_interrupt
        clear_current_thread_interrupt()
    from tools.interrupt import is_interrupted as _is_interrupted
    # Session kernels are always on locally (one interpreter per conversation); the guards above
    # already ran for this cell, and the kernel path shares env builder, RPC server and redaction.
    from tools.code_kernel import execute_in_session_kernel
    _cfg = _load_config()
    _mode = _get_execution_mode()
    return execute_in_session_kernel(
        code, task_id=task_id or "", mode=_mode, child_python=_resolve_child_python(_mode),
        child_cwd=_resolve_child_cwd(_mode, "", task_id=task_id or ""),
        sandbox_tools=frozenset(_sandbox_tools_for(enabled_tools)),
        timeout=_cfg.get("timeout", DEFAULT_TIMEOUT),
        max_tool_calls=_cfg.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS),
        reset=bool(reset), is_interrupted=_is_interrupted,
    )


def _kill_process_group(proc, escalate: bool = False):
    """Kill the child and its whole process tree via agent.deadline.kill_process_tree: SIGTERM
    (killpg + psutil descendant sweep; ``taskkill /T /F`` on Windows, where sig is ignored); with
    ``escalate=True`` wait 5s then SIGKILL survivors. Never raises — falls back to ``proc.kill()``."""
    import signal as _signal
    def _tree_signal(sig) -> None:
        try:
            from agent.deadline import kill_process_tree
            kill_process_tree(proc.pid, sig=sig)
        except Exception as e:
            logger.debug("Could not terminate process tree: %s", e, exc_info=True)
            try:
                proc.kill()
            except Exception as e2:
                logger.debug("Could not kill process: %s", e2, exc_info=True)
    _tree_signal(getattr(_signal, "SIGTERM", None))
    if escalate:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _tree_signal(getattr(_signal, "SIGKILL", None))


def _load_config() -> dict:
    """Effective ``code_execution`` section (defaults + user file + managed overlay) — runs while the
    module-level schema is built at tool discovery, so it must not import ``cli``."""
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly().get("code_execution", {})
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


# ---- Execution mode resolution (strict vs project) ----

# Canonical code_execution.mode values (referenced by tests and the config layer). Session
# kernels are the only local execution model; a leftover kernel_mode config key is ignored.
EXECUTION_MODES = ("project", "strict")
DEFAULT_EXECUTION_MODE = "project"


def _get_execution_mode() -> str:
    """``code_execution.mode`` (invalid → default with a warning). ``project``: session cwd + active
    venv python; ``strict``: isolated temp dir + ``sys.executable``. Scrubbing/whitelist apply to both."""
    cfg_value = str(_load_config().get("mode", DEFAULT_EXECUTION_MODE)).strip().lower()
    if cfg_value in EXECUTION_MODES:
        return cfg_value
    logger.warning(
        "Ignoring code_execution.mode=%r (expected one of %s), falling back to %r",
        cfg_value, EXECUTION_MODES, DEFAULT_EXECUTION_MODE,
    )
    return DEFAULT_EXECUTION_MODE


# ---- OpenAI Function-Calling Schema ----

# Per-tool documentation lines for the execute_code description, in canonical display order.
_TOOL_DOC_LINES = [
    ("web_search", "  web_search(query: str, limit: int = 5) -> dict\n"
     "    Returns {\"data\": {\"web\": [{\"url\", \"title\", \"description\"}, ...]}}"),
    ("web_extract", "  web_extract(urls: list[str], char_limit: int = None) -> dict\n"
     "    Returns {\"results\": [{\"url\", \"title\", \"content\", \"error\"}, ...]} where content is markdown.\n"
     "    No LLM summarization. Pages over char_limit (default 15000) are head+tail truncated; full text stored on disk (path in the content footer)."),
    ("read_file", "  read_file(path: str, offset: int = 1, limit: int = 2000) -> dict\n"
     "    Lines are 1-indexed. Returns {\"content\": \"...\", \"total_lines\": N}"),
    ("write_file", "  write_file(path: str, content: str) -> dict\n    Always overwrites the entire file; an existing file must be read_file'd first or the write is refused."),
    ("search_files", "  search_files(pattern: str, target=\"content\", path=\".\", file_glob=None, limit=50, order=\"discovery\") -> dict\n"
     "    target: \"content\" (search inside files) or \"files\" (find files by name). Returns {\"matches\": [...]}"),
    ("patch", "  patch(path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict\n"
     "    Replaces old_string with new_string in the file."),
    ("terminal", "  terminal(command: str, timeout=None, workdir=None) -> dict\n"
     "    Foreground only (no background/pty). Returns {\"output\": \"...\", \"exit_code\": N}"),
]


def build_execute_code_schema(enabled_sandbox_tools: set | None = None,
                              mode: str | None = None) -> dict:
    """execute_code schema listing only *enabled_sandbox_tools* — a disabled tool (e.g. web off)
    must not appear or the model keeps trying it. ``mode`` (None → config) picks the cwd sentence."""
    if enabled_sandbox_tools is None:
        enabled_sandbox_tools = SANDBOX_ALLOWED_TOOLS
    if mode is None:
        mode = _get_execution_mode()
    tool_lines = "\n".join(doc for name, doc in _TOOL_DOC_LINES if name in enabled_sandbox_tools)
    import_examples = [n for n in ("web_search", "terminal") if n in enabled_sandbox_tools]
    import_examples = import_examples or sorted(enabled_sandbox_tools)[:2]
    import_str = ", ".join(import_examples) + ", ..." if import_examples else "..."
    if mode == "strict":
        cwd_note = (
            "Scripts run in their own temp dir, not the session's CWD — pass "
            "absolute paths for any file that lives outside the session's "
            "working directory, or use terminal()/read_file() to reach it."
        )
    else:
        cwd_note = (
            "Scripts run in the session's working directory. Interpreter: "
            "the project's activated venv/conda python when one is active "
            "(VIRTUAL_ENV/CONDA_PREFIX — matches terminal()); otherwise "
            "Hermes's own python (the common case — stdlib plus Hermes's "
            "deps; check `import x` before relying on project packages)."
        )
    # Session kernels are always on (kernel_mode retired in #96787): persistence is part of the tool's one
    # description, not a bolt-on paragraph behind a dead conditional.
    description = (
        "Run Python that calls Hermes tools programmatically. Use when you "
        "need 3+ tool calls with logic between them: filtering/reducing "
        "large outputs before they enter context, branching, or loops "
        "(N pages/files, retry on failure). Use normal tool calls for "
        "single calls, results you must reason over in full, or anything needing user interaction.\n\n"
        "Calls run in a persistent session kernel: variables, imports, and "
        "loaded data survive across execute_code calls, so build on earlier "
        "work instead of re-loading it. A timed-out or interrupted call loses that state.\n\n"
        f"Available via `from hermes_tools import ...`:\n\n"
        f"{tool_lines}\n\n"
        "Limits: 5-minute timeout, max 50 tool calls per call. Stdout over "
        "50KB shows head/tail inline; the FULL text is auto-saved to a file whose path rides in the result.\n\n"
        f"{cwd_note}\n\n"
        "Helpers require imports: `from hermes_tools import json_parse, shell_quote, retry`. "
        "json_parse(text) — tolerant "
        "json.loads for terminal() output; shell_quote(s) — shlex.quote for "
        "dynamic shell args; retry(fn, max_attempts=3, delay=2) — exponential backoff."
    )
    return {
        "name": "execute_code",
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": (
                    "Python code to execute. Import tools with "
                    f"`from hermes_tools import {import_str}` "
                    "and print your final result to stdout.")},
                "reset": {"type": "boolean", "description": (
                    "Discard the kernel's persistent state and start fresh before running this code.")},
            },
            "required": ["code"],
        },
    }


# Registration-time schema (all sandbox tools, configured mode); model_tools.py rebuilds per-session.
EXECUTE_CODE_SCHEMA = build_execute_code_schema()


def _execute_code_handler(args: dict, **kwargs) -> str:
    """Redirect misdirected calls (terminal's ``command`` arg, non-string ``code``) with an
    actionable error before dispatching to ``execute_code``."""
    if "code" not in args and "command" in args:
        logger.warning("execute_code received 'command' instead of the required 'code' argument")
        return tool_error("execute_code received a 'command' parameter, but it requires "
                          "Python source in 'code'. Use terminal(command=...) for shell "
                          "commands; for Python, retry as execute_code(code=...).")
    code = args.get("code", "")
    if code is not None and not isinstance(code, str):
        return tool_error(f"execute_code received a {type(code).__name__} in 'code', but it "
                          "requires Python source as a string. Retry as execute_code(code=\"...\").")
    return execute_code(code=code or "", task_id=kwargs.get("task_id"),
                        enabled_tools=kwargs.get("enabled_tools"), reset=bool(args.get("reset", False)))


registry.register(
    name="execute_code", toolset="code_execution", schema=EXECUTE_CODE_SCHEMA,
    handler=_execute_code_handler, check_fn=lambda: True, emoji="🐍",
    max_result_size_chars=100_000,
)
