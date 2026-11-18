"""The fork installer follows main by default and stable only when selected."""
from pathlib import Path
import os
import subprocess

import pytest

from hermes_cli.distribution import REPOSITORY, REPO_GIT

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.platforms("posix")


def _git(cwd, *args, env=None):
    return subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
    ).stdout.strip()


@pytest.mark.parametrize("side,start,args,tagged,lands", [
    (False, None, [], True, "tip"),
    (False, None, ["--channel", "stable"], True, "release"),
    (False, "before", ["--channel", "stable"], True, "release"),
    (False, "tip", ["--channel", "stable"], True, "tip"),
    (False, None, ["--channel", "stable"], False, None),
    # The release cut on a release branch, main past the fork point (v0.22.2's shape): main moves
    # back only through `hermes update`, whose downgrade guard the installer cannot run.
    (True, "tip", [], True, "tip"),
    (True, "tip", ["--channel", "stable"], True, "tip"),
    (True, "before", ["--channel", "stable"], True, "release"),
])
def test_installer_lands_on_the_selected_channel(tmp_path, side, start, args, tagged, lands):
    origin, home, bin_dir = tmp_path / "origin", tmp_path / "home", tmp_path / "bin"
    install = home / ".hermes" / "hermes-agent"
    for d in (origin, home, bin_dir):
        d.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "before")
    if side:
        _git(origin, "checkout", "-q", "-b", "release/1.2")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "release")
    commits = {"before": _git(origin, "rev-parse", "HEAD~1"), "release": _git(origin, "rev-parse", "HEAD")}
    if tagged:
        _git(origin, "tag", "-a", "v1.2.3", "-m", "release")
    _git(origin, "checkout", "-q", "main")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "newer")
    commits["tip"] = _git(origin, "rev-parse", "HEAD")
    (home / ".gitconfig").write_text(f'[url "file://{origin}"]\n  insteadOf = {REPO_GIT}\n', encoding="utf-8")
    (bin_dir / "curl").write_text(
        '#!/bin/sh\ncase "$*" in *api.github.com/repos/' + REPOSITORY
        + '/releases/latest*) printf \'{\\n  "tag_name": "v1.2.3",\\n}\\n\';; *) exit 22;; esac\n',
        encoding="utf-8")
    (bin_dir / "curl").chmod(0o755)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HOME": str(home), "NO_COLOR": "1",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "file"}
    if start:
        install.parent.mkdir(parents=True)
        _git(tmp_path, "clone", "-q", REPO_GIT, str(install), env=env)
        _git(install, "checkout", "-q", "--detach", commits[start])

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "install.sh"), "--stage", "repository", "--dir", str(install),
         "--hermes-home", str(home / ".hermes"), "--non-interactive", *args],
        check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120, env=env,
    )

    if lands is None:
        assert result.returncode != 0 and not install.exists(), result.stdout + result.stderr
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(install, "rev-parse", "HEAD") == commits[lands], result.stdout + result.stderr
    assert _git(install, "status", "--porcelain") == "", result.stdout + result.stderr
