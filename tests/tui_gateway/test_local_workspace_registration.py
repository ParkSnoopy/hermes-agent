"""Local desktop workspace registration remains independent of terminal settings."""


def test_a_bad_unrelated_terminal_setting_does_not_drop_the_registration(tmp_path):
    """An invalid timeout must not prevent recording the selected workspace."""
    from tools import terminal_tool
    from tools.terminal_scope import reset_terminal_scope, set_terminal_scope
    from tui_gateway import server

    token = set_terminal_scope({"TERMINAL_TIMEOUT": "5m"})
    try:
        server._register_session_cwd({"session_key": "bad-timeout", "cwd": str(tmp_path), "explicit_cwd": True})
        assert terminal_tool.resolve_task_overrides("bad-timeout") == {
            "cwd": str(tmp_path), "cwd_source": "session",
        }
    finally:
        terminal_tool.clear_task_env_overrides("bad-timeout")
        reset_terminal_scope(token)


def test_an_unreadable_profile_keeps_the_desktop_cwd_record(tmp_path, monkeypatch):
    """An unreadable profile must not lose the host-side workspace record."""
    import hermes_constants as hc
    from tools import terminal_tool
    from tui_gateway import server

    home = tmp_path / "home"
    profile = home / "profiles" / "bad"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("terminal: [unclosed", encoding="utf-8")
    workspace = tmp_path / "host-project"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    vars(server)["_register_session_cwd"]({
        "session_key": "bad-profile", "cwd": str(workspace), "explicit_cwd": True,
        "source": "desktop", "profile_home": str(profile),
    })

    token = hc.set_hermes_home_override(str(profile))
    try:
        assert terminal_tool.resolve_task_overrides("bad-profile").get("cwd") == str(workspace)
    finally:
        terminal_tool.clear_task_env_overrides("bad-profile")
        hc.reset_hermes_home_override(token)
