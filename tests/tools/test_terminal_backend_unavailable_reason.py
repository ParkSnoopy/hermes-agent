"""Invariant: when a terminal backend's requirements check fails, the human reason is captured so the
CLI startup notice / doctor can tell the user WHY the terminal tool is missing (tools-runtime-01)."""

import pytest

from tools import terminal_tool
import tools.terminal_tool_backends as backends


@pytest.fixture(autouse=True)
def _reset_reason():
    backends._last_unavailable_reason = None
    yield
    backends._last_unavailable_reason = None








def test_successful_check_clears_reason(monkeypatch):
    backends._last_unavailable_reason = "stale"
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": "local"})
    assert terminal_tool.check_terminal_requirements() is True
    assert backends.terminal_backend_unavailable_reason() is None


def test_check_terminal_requirements_exception_captures_reason(monkeypatch):
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert terminal_tool.check_terminal_requirements() is False
    assert "boom" in terminal_tool.terminal_backend_unavailable_reason()
