"""Truncation footers point read_file at full results in the local profile cache."""

import os
import re

import pytest

from tools import delegate_tool_results
from tools import web_tools_truncate


def _footer_path(text: str) -> str:
    """Every truncation footer tells the agent how to page the full text via ``read_file path="..."``."""
    return re.search(r'read_file path="([^"]+)"', text).group(1)


@pytest.fixture(autouse=True)
def _materialize_mcp_sdk_symbols():
    """Cache-only tests do not import or initialize the MCP SDK."""
    yield


def test_truncation_footers_use_local_profile_paths(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TERMINAL_ENV", "local")
    body = "\n".join(f"row {i}" for i in range(5000))

    web_out, _ = web_tools_truncate._truncate_with_footer(body, "https://example.com/doc", 3000)
    deleg_out, _ = delegate_tool_results._trim_summary_with_footer(body, 3000, 0)

    for text, subdir in ((web_out, "cache/web"), (deleg_out, "cache/delegation")):
        path = _footer_path(text)
        assert path.startswith(str(home / subdir) + os.sep), path
        assert os.path.exists(str(home / subdir / os.path.basename(path)))
