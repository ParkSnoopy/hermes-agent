"""profiles.describe / profiles.configure use the MCP ``enabled`` key the runtime reads.

Every runtime resolver (``enabled_mcp_server_names``, coding_context, oneshot, the gateway's
tool-name resolver) keys an MCP server's on/off state off ``mcp_servers.<name>.enabled``. The
profile editor used to read and write a separate ``disabled`` key, so a server toggled off in
the editor stayed live at runtime and a server with ``enabled: false`` showed as on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import hermes_yaml as yaml

from tui_gateway import server


@pytest.fixture
def profile_dir(tmp_path, monkeypatch) -> Path:
    """A temp HERMES_HOME root with one named profile, ``bot``, at ``<root>/profiles/bot``."""
    root = tmp_path / "hermes_home"
    path = root / "profiles" / "bot"
    path.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    return path


def _write_mcp(profile_dir: Path, servers: dict) -> None:
    (profile_dir / "config.yaml").write_text(yaml.safe_dump({"mcp_servers": servers}), encoding="utf-8")


def _read_mcp(profile_dir: Path) -> dict:
    return yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))["mcp_servers"]


def _call(method: str, params: dict) -> dict:
    resp = server._methods[method](1, {"name": "bot", **params})
    assert "error" not in resp, resp.get("error")
    return resp["result"]


def _described() -> dict:
    return {s["name"]: s["enabled"] for s in _call("profiles.describe", {})["mcp_servers"]}


def test_describe_matches_the_runtime_enabled_flag(profile_dir):
    """The editor and runtime resolve the same current enablement field."""
    from hermes_cli.tools_config import enabled_mcp_server_names


    _write_mcp(profile_dir, {
        "on": {"command": "on", "enabled": True},
        "off": {"command": "off", "enabled": False},

        "implicit-on": {"command": "implicit"},
    })
    expected = {"on": True, "off": False, "implicit-on": True}
    assert _described() == expected


    live = enabled_mcp_server_names({"mcp_servers": _read_mcp(profile_dir)})
    assert {name: name in live for name in expected} == expected == _described()


def test_configure_toggle_is_what_the_runtime_resolver_and_describe_see(profile_dir):
    from hermes_cli.tools_config import enabled_mcp_server_names

    _write_mcp(profile_dir, {
        "keep": {"command": "keep", "enabled": False},
        "drop": {"command": "drop"},

    })

    _call("profiles.configure", {"enabled_mcp_servers": ["keep"]})

    on_disk = _read_mcp(profile_dir)
    assert enabled_mcp_server_names({"mcp_servers": on_disk}) == {"keep"}
    assert _described() == {"keep": True, "drop": False}
