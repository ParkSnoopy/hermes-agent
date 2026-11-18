"""Mocked Linux lock and custody boundaries; never starts updater children."""

import builtins
import io
import os
from types import SimpleNamespace

import pytest

from hermes_cli import update_custody, update_lock


def test_inherited_fd_requires_inheritable_matching_inode_and_flock(monkeypatch, tmp_path):
    import fcntl

    target = SimpleNamespace(st_dev=1, st_ino=2)
    monkeypatch.setattr(update_lock.os, "stat", lambda path: target)
    monkeypatch.setattr(update_lock.os, "listdir", lambda path: ["10", "11", "12", "13"])
    monkeypatch.setattr(update_lock.os, "get_inheritable", lambda fd: fd != 10)
    monkeypatch.setattr(update_lock.os, "fstat", lambda fd: SimpleNamespace(st_dev=1, st_ino=3 if fd == 11 else 2))
    calls = []

    def flock(fd, flags):
        calls.append((fd, flags))
        if fd == 12:
            raise BlockingIOError()

    monkeypatch.setattr(fcntl, "flock", flock)
    assert update_lock._inherited_lock_fd(tmp_path / "lock") == 13
    assert calls == [(12, fcntl.LOCK_EX | fcntl.LOCK_NB), (13, fcntl.LOCK_EX | fcntl.LOCK_NB)]


@pytest.mark.parametrize("owned", [True, False])
def test_checkout_release_is_close_only_and_nested(monkeypatch, owned):
    import fcntl

    monkeypatch.setattr(update_lock, "_HELD", {"path": "/lock", "fd": 41, "owned": owned, "depth": 2})
    closed = []
    monkeypatch.setattr(update_lock.os, "close", closed.append)
    monkeypatch.setattr(fcntl, "flock", lambda *args: pytest.fail("release must not unlock shared flock"))
    update_lock._release_checkout()
    assert closed == [] and update_lock._HELD["depth"] == 1
    update_lock._release_checkout()
    assert closed == ([41] if owned else [])
    assert update_lock._HELD is None


def test_linux_proc_identity_parses_parent_and_start_time(monkeypatch):
    stat = b"42 (name with ) parens) S 17 " + b"0 " * 17 + b"2500 0\n"
    def fake_open(path, mode):
        return io.BytesIO(b"cpu 0\nbtime 1000\n" if path == "/proc/stat" else stat)
    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(update_lock.os, "sysconf", lambda key: 100)
    assert update_lock._stdlib_parent_pid(42) == 17
    assert update_lock._stdlib_create_time(42) == 1025
    monkeypatch.setattr(builtins, "open", lambda *args: io.BytesIO(b"malformed"))
    assert update_lock._stdlib_parent_pid(42) is None
    assert update_lock._stdlib_create_time(42) is None


def test_git_dispatch_passes_custody_only_to_mutators(monkeypatch):
    monkeypatch.setattr(update_lock, "_HELD", {"path": "/lock", "fd": 41, "owned": True, "depth": 1})
    sentinel = object()
    monkeypatch.setattr(update_custody, "_death_signal_preexec", lambda: sentinel)
    calls = []
    monkeypatch.setattr(update_custody, "_prefetch_for_move", lambda *args: None)
    proc = SimpleNamespace(communicate=lambda **kw: (None, None), returncode=0)
    monkeypatch.setattr(update_custody.subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)) or proc)
    for args in (["reset", "--hard", "HEAD"], ["fetch", "origin"]):
        update_custody.run_git(["git"], args, cwd="/not-a-checkout", pass_fds=(7,))
    mutator, reader = calls
    assert mutator[1]["pass_fds"] == (7, 41)
    assert "preexec_fn" not in mutator[1]
    assert reader[1]["pass_fds"] == (7,) and reader[1]["preexec_fn"] is sentinel
    for argv, _ in calls:
        assert "gc.autoDetach=false" in argv and "maintenance.auto=false" in argv
    assert "credential.helper=" in mutator[0] and "credential.helper=" not in reader[0]
    assert f"core.hooksPath={os.devnull}" in mutator[0]
    assert "core.fsmonitor=false" in mutator[0]


def test_containment_wrapper_preserves_command_and_lock_fd(monkeypatch):
    monkeypatch.setattr(update_lock, "_HELD", {"path": "/lock", "fd": 41, "owned": True, "depth": 1})
    with update_custody.contained_command(["node", "build"]) as (argv, kwargs):
        assert argv[-2:] == ["node", "build"] and kwargs == {"pass_fds": (41,)}
        assert argv[5] == "41"
    with update_custody.contained_command(["node", "build"], inherit_lock=False) as (argv, kwargs):
        assert argv == ["node", "build"] and kwargs == {}


def test_atomic_marker_compare_swap_preserves_replacement(tmp_path):
    marker = tmp_path / "marker"
    marker.write_bytes(b"replacement")
    assert not update_lock._compare_and_swap(marker, b"old", b"new")
    assert marker.read_bytes() == b"replacement"
    assert update_lock._compare_and_swap(marker, b"replacement", b"new")
    assert marker.read_bytes() == b"new"
    assert not list(tmp_path.glob("*.tmp"))
