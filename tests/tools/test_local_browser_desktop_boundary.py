"""Local-only desktop and browser boundaries without starting graphical processes."""
from types import SimpleNamespace

import pytest

from tools import browser_session, terminal_tool
from tools.bot_desktop import runtime



@pytest.fixture(autouse=True)
def _materialize_mcp_sdk_symbols():
    """Do not import or initialize the MCP SDK in this isolated test."""
    yield


@pytest.fixture(autouse=True)
def no_terminal_bridge(monkeypatch):
    monkeypatch.setattr(terminal_tool, "_ensure_terminal_env_bridged", lambda: None)


@pytest.mark.parametrize("backend", ["docker", "ssh", "vercel_sandbox", "plugin"])
def test_retired_terminal_never_spawns_local_desktop_browser_or_driver(monkeypatch, backend):
    monkeypatch.setenv("TERMINAL_ENV", backend)
    from tools import browser_launcher
    calls = []
    monkeypatch.setattr(browser_launcher, "launch_chromium", lambda *a, **kw: calls.append("browser"))
    monkeypatch.setattr(runtime, "_should_auto_start", lambda env: calls.append("desktop"))
    for action in (runtime.start, runtime.ensure_started_for_tool,
                   lambda: browser_session._create_local_session("test")):
        with pytest.raises(ValueError, match="Only 'local'"):
            action()
    assert calls == []


def test_local_browser_uses_shared_desktop_target(monkeypatch):
    from tools import browser_launcher
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(browser_session, "ensure_screen_for_headed_chromium", lambda: None)
    monkeypatch.setattr(browser_session, "_launch_target", lambda key: ("profile:desk", {"user_data_dir": "/profile"}, True))
    calls = []
    monkeypatch.setattr(browser_launcher, "launch_chromium", lambda key, **kwargs:
                        (calls.append((key, kwargs)) or SimpleNamespace(cdp_url="ws://local", pid=123), None))
    info = browser_session._create_chromium_session("task")
    assert calls == [("profile:desk", {"user_data_dir": "/profile"})]
    assert info["launcher_key"] == "profile:desk" and info["shared_profile"]


def test_external_cdp_remains_available_with_retired_terminal(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    from tools import browser_session_cdp
    monkeypatch.setattr(browser_session_cdp, "get_cdp_override", lambda: "ws://external")
    info = browser_session._create_session_for_key("task", False)
    assert info["cdp_url"] == "ws://external"
    assert info["features"]["cdp_override"] is True
