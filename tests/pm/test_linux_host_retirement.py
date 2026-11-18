"""Linux PM host behavior without launching package-manager processes."""
from pathlib import Path
import os
import sys

import pytest

from pm import build_compilers, cli, environment, index_config, shell
from pm.package import InstallError
from pm.packages import Python
from pm.security_packages import Bws
from pm.store import Store


# These unrelated suite fixtures import agent/browser subsystems. This file exercises
# only pure PM paths and must not initialize those subsystems.
@pytest.fixture
def _moa_caches_isolated():
    yield


@pytest.fixture
def _isolate_computer_use_approval_state():
    yield


def test_linux_pip_configuration_precedence(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    env = {"XDG_CONFIG_DIRS": "/one:/two", "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    paths = index_config.pip_config_candidates(env)
    assert paths == [Path("/one/pip/pip.conf"), Path("/two/pip/pip.conf"), Path("/etc/pip.conf"),
                     tmp_path / ".pip/pip.conf", tmp_path / "xdg/pip/pip.conf",
                     Path(sys.prefix) / "pip.conf"]
    explicit = tmp_path / "explicit.conf"
    explicit.write_text("[global]\n", encoding="utf-8")
    assert index_config.pip_config_candidates({**env, "PIP_CONFIG_FILE": str(explicit)}) == [
        *paths[:3], Path(sys.prefix) / "pip.conf", explicit]


def test_shell_prefers_path_and_falls_back_without_executing(monkeypatch):
    monkeypatch.setattr(shell.shutil, "which", lambda _: "/custom/bash")
    assert shell.bash() == "/custom/bash"
    monkeypatch.setattr(shell.shutil, "which", lambda _: None)
    monkeypatch.setattr(shell.os.path, "isfile", lambda path: path == "/bin/bash")
    monkeypatch.setattr(shell.os, "access", lambda path, mode: True)
    assert shell.bash() == "/bin/bash"


def test_compiler_defaults_preserve_linux_toolchain_policy(monkeypatch):
    available = {"gcc", "g++", "ar"}
    monkeypatch.setattr(build_compilers.shutil, "which", lambda name, path=None: name if name in available else None)
    recorded = {"CC": "clang -pthread", "CXX": "clang++ -pthread", "LDSHARED": "clang -shared",
                "LDCXXSHARED": "clang++ -shared", "AR": "/missing/llvm-ar"}
    assert build_compilers._linux_defaults(recorded, {"PATH": "/test", "CXX": "custom"}) == {
        "CC": "gcc -pthread", "LDSHARED": "gcc -shared", "AR": "ar"}


def test_progress_chooses_terminal(monkeypatch):
    class Stream:
        def __init__(self, tty):
            self.tty = tty

        def isatty(self):
            return self.tty

    stdout, stderr = Stream(False), Stream(True)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    assert cli._progress_stream() is stderr
    stdout.tty = True
    assert cli._progress_stream() is stdout


def test_nonblocking_pipe_preserves_data_and_eof():
    read_fd, write_fd = os.pipe()
    try:
        os.set_blocking(read_fd, False)
        with pytest.raises(BlockingIOError):
            environment._read_pipe(read_fd)
        os.write(write_fd, b"progress")
        assert environment._read_pipe(read_fd) == b"progress"
        os.close(write_fd)
        write_fd = -1
        assert environment._read_pipe(read_fd) == b""
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)


@pytest.mark.parametrize("target", ["linux-x64", "linux-arm64", "linux-x64-musl", "linux-arm64-musl"])
def test_linux_python_staging_and_security_package_url(tmp_path, target):
    package = Python()
    staged = tmp_path / "stage"
    (staged / "bin").mkdir(parents=True)
    (staged / "bin/python3").write_bytes(b"interpreter")
    package.stage(Store(tmp_path / "store"), staged, "fixture", target)
    assert package.verify(staged, target) == ""
    assert "linux-musl" in Bws().fetch_url("1", target)


@pytest.mark.parametrize("target", ["darwin-arm64", "win32-x64"])
def test_security_package_refuses_retired_host_targets(target):
    with pytest.raises(InstallError, match="unsupported target"):
        Bws().fetch_url("1", target)
