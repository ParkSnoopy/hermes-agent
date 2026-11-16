"""Process home ownership stays independent of retired graphical runtimes."""

from hermes_cli import process_identity


def test_profile_flag_pins_process_home(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    monkeypatch.setattr(process_identity, "_pid_environ", lambda pid: {"HERMES_HOME": str(root)})
    monkeypatch.setattr(process_identity, "_pid_cmdline", lambda pid: ["hermes", "-p", "work", "gateway", "run"])
    assert process_identity._hermes_home_for_pid(42, require_launch_identity=True) == str(root / "profiles" / "work")


def test_destructive_ownership_refuses_unpinned_or_unreadable_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(process_identity, "_pid_environ", lambda pid: {"HERMES_HOME": str(tmp_path)})
    monkeypatch.setattr(process_identity, "_pid_cmdline", lambda pid: ["hermes", "gateway", "run"])
    assert process_identity._hermes_home_for_pid(42, require_launch_identity=True) is None
    monkeypatch.setattr(process_identity, "_pid_environ", lambda pid: None)
    assert process_identity._hermes_home_for_pid(42) is None


def test_pinned_profile_home_survives_missing_argv_for_discovery(monkeypatch, tmp_path):
    home = tmp_path / "profiles" / "work"
    monkeypatch.setattr(process_identity, "_pid_environ", lambda pid: {"HERMES_HOME": str(home)})
    monkeypatch.setattr(process_identity, "_pid_cmdline", lambda pid: None)
    assert process_identity._hermes_home_for_pid(42) == str(home)
    assert process_identity._hermes_home_for_pid(42, require_launch_identity=True) is None