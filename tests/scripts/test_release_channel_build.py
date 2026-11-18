"""Only the native, tagless commit workflow remains on the release CLI."""
import sys

import pytest

from scripts import release
from scripts.releases import commit_build


def test_plain_commit_dispatch_and_retired_desktop_options(monkeypatch):
    calls = []
    monkeypatch.setattr(commit_build, "cmd_build_commit", lambda args: calls.append(args.build_commit))
    monkeypatch.setattr(sys, "argv", ["release.py", "--build-commit", "main"])
    release.main()
    assert calls == ["main"]
    for option in ("--channel", "--channels", "--branding", "--bundle-env", "--bundle-unset"):
        monkeypatch.setattr(sys, "argv", ["release.py", "--build-commit", "main", option, "x"])
        with pytest.raises(SystemExit) as rejected:
            release.main()
        assert rejected.value.code == 2
    assert calls == ["main"]


def test_commit_workflow_dispatch_uses_only_termux_inputs():
    sha = "a" * 40
    command = commit_build.dispatch_command(sha, "fixture/repo", "main")
    assert command[:4] == ["gh", "workflow", "run", "termux-release.yml"]
    assert command[command.index("--ref") + 1] == "main"
    assert command[command.index("--repo") + 1] == "fixture/repo"
    fields = [command[i + 1] for i, value in enumerate(command) if value == "-f"]
    assert fields == [f"build_commit={sha}", "tag=", "upload_release=false", "termux_upgrade_from_tag="]
