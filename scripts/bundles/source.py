"""Source checkout custody for native payload builds."""
from __future__ import annotations

from pathlib import Path
import subprocess


def git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=source, text=True, encoding="utf-8").rstrip("\r\n")

def require_source(source: Path, commit: str) -> None:
    if git(source, "rev-parse", "HEAD") != commit:
        raise ValueError("preparation source checkout changed revision; prepare again")
    status = git(source, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise ValueError(f"native builds require a clean source checkout at the admitted revision:\n{status}")
