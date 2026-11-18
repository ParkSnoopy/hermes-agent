"""Retained local terminal discovery and default workspace contracts."""

import os
import sys
from pathlib import Path
import pytest

# Ensure repo root is importable
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    import tools.terminal_tool
    _tt_mod = sys.modules["tools.terminal_tool"]
except ImportError:
    pytest.skip("hermes-agent tools not importable (missing deps)", allow_module_level=True)


# =========================================================================
# Test 1: Tool resolution includes terminal + file tools
# =========================================================================

class TestToolResolution:
    """Verify get_tool_definitions returns all expected tools for eval."""


    def test_terminal_tool_present(self):
        """The terminal tool must be present (not silently dropped)."""
        from model_tools import get_tool_definitions
        tools = get_tool_definitions(
            enabled_toolsets=["terminal", "file"],
            quiet_mode=True,
        )
        names = [t["function"]["name"] for t in tools]
        assert "terminal" in names, f"terminal tool missing! Only got: {names}."


# =========================================================================
# Test 2: Local default workspace
# =========================================================================

class TestCwdHandling:
    """Verify the local terminal defaults to the current workspace."""
    def test_local_backend_uses_getcwd(self, monkeypatch):
        """Local backend should use os.getcwd(), not /root."""
        monkeypatch.setenv("TERMINAL_ENV", "local")
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        config = _tt_mod._get_env_config()
        assert config["cwd"] == os.getcwd()
