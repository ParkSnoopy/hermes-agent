"""Runtime plugin disable takes effect on a native forced discovery reload."""

import json

from hermes_cli import plugins
from hermes_cli.plugins import PluginManager


def test_disable_removes_registered_hook_on_reload(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin = home / "plugins" / "probe"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(json.dumps({"name": "probe", "version": "1.0.0"}))
    (plugin / "__init__.py").write_text(
        "def register(ctx):\n"
        "    ctx.register_hook('pre_tool_call', lambda **kw: {'probe': True})\n"
    )
    config = home / "config.yaml"
    config.write_text(json.dumps({"plugins": {"enabled": ["probe"]}}))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: tmp_path / "empty")
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])
    manager = PluginManager()
    try:
        manager.discover_and_load()
        assert manager.invoke_hook("pre_tool_call") == [{"probe": True}]
        config.write_text(json.dumps({"plugins": {"enabled": ["probe"], "disabled": ["probe"]}}))
        manager.discover_and_load(force=True)
        assert manager.invoke_hook("pre_tool_call") == []
        assert not manager._plugins["probe"].enabled
        config.write_text(json.dumps({"plugins": {"enabled": ["probe"]}}))
        manager.discover_and_load(force=True)
        assert manager.invoke_hook("pre_tool_call") == [{"probe": True}]
    finally:
        manager.unload()