"""Resolve the shell used by Hermes commands on Linux."""
from __future__ import annotations

import os
import shutil


def bash() -> str | None:
    """Resolve bash from PATH or a usable system-shell fallback."""
    on_path = shutil.which("bash")
    if on_path:
        return on_path
    for candidate in ("/usr/bin/bash", "/bin/bash", os.environ.get("SHELL"), "/bin/sh"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None
