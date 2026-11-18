"""The first-party TUI uses the Bun selected by the caller's PATH."""

import os
from pathlib import Path

import pytest

from hermes_cli import main_tui_launch, source_build


def test_prebuilt_tui_uses_path_bun_without_acquiring_node(tmp_path, monkeypatch):
    bun = tmp_path / "bin" / ("bun.exe" if os.name == "nt" else "bun")
    bun.parent.mkdir()
    bun.write_text("fixture", encoding="utf-8")
    bun.chmod(0o755)
    dist = tmp_path / "prebuilt" / "dist"
    dist.mkdir(parents=True)
    entry = dist / "entry.js"
    entry.write_text("fixture", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bun.parent))
    monkeypatch.setenv("HERMES_TUI_DIR", str(dist.parent))
    argv, cwd = main_tui_launch._make_tui_argv(Path("missing"), False)
    assert argv == [str(bun), str(entry)]
    assert cwd == dist.parent


def test_missing_bun_is_reported_without_provisioning(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(RuntimeError, match="Bun is required"):
        source_build.bun_binary()
