"""Shared user-directory creation and permission diagnostics."""
from __future__ import annotations

from pathlib import Path


def make_user_dir(path: str | Path, mode: int = 0o777, *, parents: bool = False, exist_ok: bool = False) -> Path:
    """Create a user directory with the same semantics as :meth:`Path.mkdir`."""
    target = Path(path)
    target.mkdir(mode=mode, parents=parents, exist_ok=exist_ok)
    return target


def unusable_dir_remedy(path: str | Path) -> str:
    """Describe how to repair directory ownership on the host."""
    return f'run: sudo chown -R "$(id -un)" "{path}"'
