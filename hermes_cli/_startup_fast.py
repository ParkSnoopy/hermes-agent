"""Pre-import startup fast paths — THE canonical lightweight helpers.

This module is imported by ``hermes_cli/main.py`` BEFORE its heavy import
wall (config, argparse tree, logging, providers). Everything here must stay
**stdlib-only and cheap** (os/sys file probes; no yaml, no hermes_cli.config,
no argparse). A guard test (``test_startup_fast_import_weight``) subprocess-
imports this module and fails if any heavy module sneaks into sys.modules.

Why this module exists (the bug class it kills): version-printing kept being
reimplemented as ``*_fast()`` copies at the top of main.py, each duplicating
canonical project-root resolution. The copies drifted: eb4040242 changed the canonical output and
referenced ``PROJECT_ROOT`` inside the fast function, which doesn't exist
yet on the fast path → the fast path NameError'd on --version and nobody
noticed. One implementation, imported by both the fast path and the module
constants, makes that drift structurally impossible; the parity guard test
would have caught eb4040242 the day it landed.

"""

from __future__ import annotations

import os
import sys

__all__ = [
    "ensure_project_root_on_path",
    "is_desktop_ssh_backend_argv",
    "is_global_fast_version_argv",
    "normalize_hermes_home_env",
    "print_fast_version_info",
    "project_root_str",
    "read_install_method",
    "read_openai_version",
    "try_fast_version",
]


def _read_text(path: str) -> str | None:
    """Read a small text file, or None when it is missing/unreadable."""
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def project_root_str() -> str:
    """Repo root as a str — the single source for main.py's PROJECT_ROOT."""
    return os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))


def normalize_hermes_home_env() -> None:
    """Expand ``~``/``$VAR`` in ``HERMES_HOME`` once, at process entry, and write it back.

    fish does not expand ``~`` inside ``VAR=~/...`` and every shell passes a quoted value
    through verbatim, so a literal tilde reaches the process. ``Path("~/.hermes")`` is
    *relative*: the many raw ``os.environ["HERMES_HOME"]`` readers (this fast path, the
    active_profile probe, profile re-home, the dotenv loader) would each resolve it against
    cwd and scaffold a full home under ``<cwd>/~/.hermes``. One expansion here gives every
    reader the same absolute spelling; ``hermes_constants`` expands as well for non-CLI
    entry points. A relative value that is not tilde/variable-shaped is left alone.
    """
    raw = os.environ.get("HERMES_HOME", "")
    if not raw.strip():
        return
    expanded = os.path.expanduser(os.path.expandvars(raw.strip()))
    if expanded != raw:
        os.environ["HERMES_HOME"] = expanded


def _realpath_or_self(path: str) -> str:
    """``os.path.realpath`` that survives a deleted cwd.

    A relative ``sys.path`` entry is resolved through ``os.getcwd()``, which raises
    ``FileNotFoundError`` once the directory the process started in has been removed
    (a cron delivery child spawned from a reaped scratch workspace, #102941); the CLI
    then dies before it can parse argv.
    """
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def ensure_project_root_on_path() -> None:
    """Put the project root at sys.path[0], deduping realpath-equivalents."""
    project_root = project_root_str()
    normalized_root = os.path.normcase(_realpath_or_self(project_root))
    sys.path[:] = [entry for entry in sys.path
                   if not entry or os.path.normcase(_realpath_or_self(entry)) != normalized_root]
    sys.path.insert(0, project_root)


def is_global_fast_version_argv(argv: list[str]) -> bool:
    return argv in (["--version"], ["-V"])


def is_desktop_ssh_backend_argv(argv: list[str]) -> bool:
    """Is ``argv`` the Desktop client's SSH backend spawn (``serve --ssh-session-token-file``)?

    That child has a fixed identity: Desktop names the remote profile explicitly (or none for
    the root home) and hands its session token through a 0600 FILE, never the
    ``HERMES_DASHBOARD_SESSION_TOKEN`` env var the local pool spawn uses. Every reader of
    "is this process Desktop's backend" needs both shapes; this is the argv half.
    """
    return "--ssh-session-token-file" in argv


def _default_home() -> str:
    return os.path.join(os.path.expanduser("~"), ".hermes")


def _resolved_home() -> str:
    return os.environ.get("HERMES_HOME", "").strip() or _default_home()


def read_openai_version() -> str | None:
    """Read OpenAI SDK version without importing ``importlib.metadata``."""
    for base in sys.path:
        version_file = os.path.join(base or os.getcwd(), "openai", "_version.py")
        try:
            with open(version_file, encoding="utf-8-sig") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped.startswith("__version__"):
                        continue
                    _key, _sep, value = stripped.partition("=")
                    value = value.split("#", 1)[0].strip().strip("\"'")
                    return value or None
        except OSError:
            continue
    return None


def read_install_method() -> str | None:
    """The installer's ``.install_method`` stamp, if present.

    Only the stamp (step 1 of ``config.detect_install_method``'s resolution order) — the
    managed/git/pip fallbacks need heavier imports and stay on the slow path.
    """
    method = _read_text(os.path.join(_resolved_home(), ".install_method"))
    return (method or "").strip().lower() or None


def print_fast_version_info(*, check_updates: bool = True) -> None:
    """THE canonical ``hermes --version`` output (also used by /version).

    Every lazy block degrades gracefully — a broken/heavy import can never take the basic version
    output down.
    """
    # Registry-owned banner label (includes "· upstream <sha>" for git installs); banner.py keeps
    # rich/prompt_toolkit lazy, so this import is light.
    try:
        from hermes_cli.banner import format_banner_version_label

        print(format_banner_version_label())
    except Exception:
        from hermes_cli.version_info import get_version_info

        print(f"Hermes Agent {get_version_info().version_label}")
    print(f"Install directory: {project_root_str()}")
    # Authoritative resolver first (code-scoped stamp → managed → nix → git → pip; also self-heals
    # poisoned shared-home 'docker' stamps); cheap stdlib stamp probe only if it fails.
    try:
        from pathlib import Path

        from hermes_cli.config import detect_install_method

        install_method = detect_install_method(Path(project_root_str()))
    except Exception:
        install_method = read_install_method()
    if install_method:
        print(f"Install method: {install_method}")
    print(f"Python: {sys.version.split()[0]}")
    openai_version = read_openai_version()
    print(f"OpenAI SDK: {openai_version}" if openai_version else "OpenAI SDK: Not installed")
    if not check_updates:
        return
    # Synchronous update status — bounded by check_for_updates' own subprocess/network timeouts
    # and its 6-hour cache; any failure prints nothing.
    try:
        from hermes_cli.source_check import UPDATE_AVAILABLE_NO_COUNT, check_for_updates
        from hermes_cli.config import recommended_update_command

        status = check_for_updates(passive=True)
        behind = status.get("behind")
        if status.get("rollback"):
            print(f"Newer than stable release v{status.get('sourceVersion')} — "
                  f"'{recommended_update_command()}' moves back to it")
        elif behind == UPDATE_AVAILABLE_NO_COUNT:
            print(f"Update available — run '{recommended_update_command()}'")
        elif behind and behind > 0:
            commits_word = "commit" if behind == 1 else "commits"
            print(f"Update available: {behind} {commits_word} behind — run '{recommended_update_command()}'")
        elif behind == 0:
            print("Up to date")
    except Exception:
        pass


def try_fast_version(argv: list[str] | None = None) -> bool:
    """Handle ``hermes --version`` before the heavy import wall.

    Only ``--version``/``-V`` (the ``version`` subcommand was removed —
    ``--version`` now carries the full output incl. update status).
    """
    if argv is None:
        argv = sys.argv[1:]
    if not is_global_fast_version_argv(argv):
        return False
    print_fast_version_info()
    return True
