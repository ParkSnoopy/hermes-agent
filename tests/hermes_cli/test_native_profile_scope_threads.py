"""Context-local profile homes survive retained worker-thread boundaries."""
import asyncio
import logging
import threading
from types import SimpleNamespace

from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override


def test_boot_probe_worker_keeps_the_request_home_override(tmp_path):
    from gateway.run_startup import GatewayStartupMixin

    secondary = tmp_path / "profiles" / "secondary"
    secondary.mkdir(parents=True)

    async def run():
        token = set_hermes_home_override(str(secondary))
        try:
            runner = SimpleNamespace(config=SimpleNamespace(multiplex_profiles=False))
            return await GatewayStartupMixin._run_boot_probe_in_launch_scope(runner, get_hermes_home)
        finally:
            reset_hermes_home_override(token)

    assert asyncio.run(run()) == secondary


def test_mcp_discovery_thread_keeps_the_request_home_override(tmp_path, monkeypatch):
    from hermes_cli import mcp_startup

    secondary = tmp_path / "profiles" / "secondary"
    secondary.mkdir(parents=True)
    seen, done = {}, threading.Event()

    def discover():
        seen["home"] = get_hermes_home()
        seen["thread"] = threading.current_thread()
        done.set()

    monkeypatch.setattr(mcp_startup, "_mcp_discovery_started", set())
    monkeypatch.setattr(mcp_startup, "_mcp_discovery_thread", {})
    monkeypatch.setattr(mcp_startup, "_has_configured_mcp_servers", lambda: True)
    monkeypatch.setattr(mcp_startup, "_discover_mcp_tools_without_interactive_oauth", discover)
    monkeypatch.setattr(mcp_startup, "_any_mcp_connected", lambda: True)
    token = set_hermes_home_override(str(secondary))
    try:
        mcp_startup.start_background_mcp_discovery(logger=logging.getLogger(__name__), thread_name="test-mcp")
    finally:
        reset_hermes_home_override(token)
    assert done.wait(5)
    seen["thread"].join(timeout=5)
    assert not seen["thread"].is_alive()
    assert seen["home"] == secondary