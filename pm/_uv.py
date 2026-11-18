"""Resolve system uv and PM's managed Python for the private engine."""
from __future__ import annotations

import shutil
from pathlib import Path

from pm.filesystem import native
from pm.package import InstallError
from pm.registry import get_package
from pm.store import current_target


def _toolchain(*, realize: bool = True, explicit: bool = False) -> tuple[Path, Path] | None:
    """Resolve uv from PATH and Python from the managed store."""
    from pm.install import _installed_location, _lockfile, ensure

    uv = shutil.which("uv")
    if uv is None:
        if not realize:
            return None
        raise InstallError("uv", "uv was not found on PATH", "install uv with your system package manager")
    if realize:
        ensure("python", explicit=explicit)
    target = current_target()
    package = get_package("python")
    location = _installed_location(package, _lockfile(), target)
    if location is None:
        if not realize:
            return None
        raise InstallError("python", "pinned tool is unavailable", "run `hermes pm install`")
    facts, store = location
    binary = package.binary(store.entry(facts.get("python")["entry"]), target)
    if binary is None or not binary.is_file():
        if not realize:
            return None
        raise InstallError("python", "installed binary is missing", "run `hermes pm install`")
    return Path(uv), Path(native(binary))
