"""Regression: the terminal config→env re-bridge is scoped to the *true*
process profile, never a per-task ``set_hermes_home_override``.

The multiplex gateway shares os.environ. A secondary profile must not bridge its local
terminal timeout or working directory into the launch profile's environment.

The guard in ``env_loader._reapply_terminal_config_bridge`` compares the
loaded home against the process home. It must use the override-immune
``get_process_hermes_home()`` so the comparison reflects the launch scope,
not whichever profile the current task is scoped to.
"""

import os

import pytest

from hermes_cli import env_loader
from hermes_constants import (
    set_hermes_home_override,
    reset_hermes_home_override,
)


def _write_terminal_config(home, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(text)


@pytest.fixture(autouse=True)
def _clean_terminal_env(monkeypatch):
    for name in ("TERMINAL_ENV", "TERMINAL_TIMEOUT", "TERMINAL_CWD"):
        monkeypatch.delenv(name, raising=False)
    yield


def test_process_hermes_home_ignores_task_override(tmp_path, monkeypatch):
    """The guard's home resolver must not follow a per-task override."""
    launch_home = tmp_path / "laptop"
    other_home = tmp_path / "tommy"
    launch_home.mkdir()
    other_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(launch_home))

    token = set_hermes_home_override(str(other_home))
    try:
        assert (
            env_loader._process_hermes_home().resolve() == launch_home.resolve()
        ), "process home leaked to the per-task override profile"
    finally:
        reset_hermes_home_override(token)


def test_secondary_profile_reload_does_not_bridge_into_shared_env(
    tmp_path, monkeypatch
):
    """A secondary profile must not overwrite launch-profile local settings."""
    launch_home = tmp_path / "laptop"
    other_home = tmp_path / "tommy"
    _write_terminal_config(launch_home, "terminal:\n  backend: local\n  timeout: 41\n")
    _write_terminal_config(other_home, "terminal:\n  backend: local\n  timeout: 73\n")
    monkeypatch.setenv("HERMES_HOME", str(launch_home))
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_TIMEOUT", "41")

    # A per-turn handler for the secondary profile is scoped via the contextvar
    # and drives a reload for tommy's home.
    token = set_hermes_home_override(str(other_home))
    try:
        env_loader._reapply_terminal_config_bridge(other_home)
    finally:
        reset_hermes_home_override(token)

    assert os.environ["TERMINAL_ENV"] == "local"
    assert os.environ["TERMINAL_TIMEOUT"] == "41"
