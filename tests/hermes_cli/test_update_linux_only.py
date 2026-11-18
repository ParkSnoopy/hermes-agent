"""Linux updater refuses a non-Git source before invoking any Git operation."""

from types import SimpleNamespace

import pytest

from hermes_cli import update_cmd
from hermes_cli.update_cmd_fleet import _gateway_recovery_partition


def test_non_git_checkout_is_refused_before_git_preparation(tmp_path, monkeypatch):
    monkeypatch.setattr(update_cmd, "_m", lambda: SimpleNamespace(PROJECT_ROOT=tmp_path))
    monkeypatch.setattr(update_cmd, "_git_run", lambda *a, **kw: pytest.fail("unexpected Git operation"))
    with pytest.raises(SystemExit) as exc:
        update_cmd._prepare_git_command()
    assert exc.value.code == 1


def test_systemd_gateway_is_a_fresh_restart_candidate():
    runtime = SimpleNamespace(kind="gateway", profile="alpha", supervisor="systemd")
    candidates, skipped = _gateway_recovery_partition(SimpleNamespace(runtimes=[runtime]))
    assert candidates == {"alpha": "systemd"}
    assert skipped == []


def test_git_checkout_preserves_preparation_and_origin_resolution(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    git_cmd = ["git", "-c", "gc.autoDetach=false"]
    origin = "https://example.invalid/fork/project.git"
    calls = []
    facade = SimpleNamespace(
        PROJECT_ROOT=tmp_path,
        _get_origin_url=lambda command, root: calls.append(("origin", command, root)) or origin,
    )
    monkeypatch.setattr(update_cmd, "_m", lambda: facade)
    monkeypatch.setattr(update_cmd, "_base_git_cmd", lambda: git_cmd)
    monkeypatch.setattr(update_cmd, "_map_ssl_cert_file_for_git", lambda command: calls.append(("tls", command)))
    monkeypatch.setattr(update_cmd, "_discard_lockfile_churn", lambda command, root, **kw: calls.append(("lockfile", command, root)))
    monkeypatch.setattr(update_cmd, "_normalize_managed_eol", lambda command, root, **kw: calls.append(("eol", command, root)))
    monkeypatch.setattr(update_cmd, "_is_fork", lambda url: url == origin)

    assert update_cmd._prepare_git_command() == (git_cmd, True)
    assert calls == [
        ("tls", git_cmd),
        ("lockfile", git_cmd, tmp_path),
        ("eol", git_cmd, tmp_path),
        ("origin", git_cmd, tmp_path),
    ]
