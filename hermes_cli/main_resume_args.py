"""Recreate a removed worktree a resumed TUI session needs (N8)."""

import os


def _frozen_cwd(db, session_id) -> str:
    """The cwd the owner runs *session_id*'s tools in (its local policy), or "" without one. The
    row's cwd is main's stamp, which for a classic ``-w`` session is the repo root, not the tree."""
    import sqlite3

    from hermes_state_local import local_receipt
    from hermes_state_runtime import RuntimeStoreError
    try:
        return str(local_receipt(db, session_id)["policy"].get("cwd") or "").strip()
    except (RuntimeStoreError, KeyError, AttributeError, sqlite3.Error):  # no readable policy: use the row's cwd
        return ""


def recreate_resumed_tui_worktree(args) -> None:
    """A resumed session whose frozen cwd is a ``-w`` / ``--tui -w`` tree that quit removed gets that
    tree back before attaching, or the owner refuses every turn ``workspace_unavailable`` (N8)."""
    if not getattr(args, "resume", None) or getattr(args, "worktree", False):
        return
    cwd = ""
    from hermes_cli.main import _session_db
    with _session_db() as db:  # a store error is swallowed: nothing to recreate
        cwd = (_frozen_cwd(db, args.resume)
               or ((db.get_session(args.resume) or {}).get("cwd") or "").strip())
    if not cwd or os.path.isdir(cwd):
        return
    from hermes_cli.tui_worktree_resume import recreate_tui_worktree
    tree = recreate_tui_worktree(cwd, args.resume)
    if tree:
        print(f"↪ recreated the session's worktree: {tree}")
