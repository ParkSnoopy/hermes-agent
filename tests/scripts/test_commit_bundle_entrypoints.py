"""Tagless packaging enters the real builder with an exact source identity."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess



_BASH = shutil.which("bash")

def _child_env(**updates):
    return {**os.environ, **updates}







def test_termux_commit_args_reach_prerequisite_checks_without_mutation(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    out = tmp_path / "must-not-be-written"
    helper = tmp_path / "bin"
    helper.mkdir()
    # Empty prerequisite commands are not build substitutes: stop at the first
    # actual prerequisite check, before any payload or output is created.
    env = _child_env(PATH=str(helper), HERMES_PAYLOAD_TAG="")
    scripts = [repo / "scripts/termux/termux_build.sh", repo / "scripts/termux/build_deb.sh"]
    for script in scripts:
        args = ["--repo", str(repo), "--commit", "a" * 40, "--out", str(out)]
        if script.name == "build_deb.sh":
            args += ["--payload", str(tmp_path / "absent")]
        result = subprocess.run([_BASH, str(script), *args], env=env, cwd=tmp_path,
                                capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "usage:" not in result.stderr
        assert not out.exists()
