"""Project Python execution requires both project-source opt-in and plugin enablement."""

import json

import pytest

from hermes_cli import plugins
from hermes_cli.plugins import PluginManager


@pytest.mark.parametrize("flag", [None, "", "0", "false", "FALSE", "no", "off"])
def test_falsy_project_opt_in_never_executes_code(tmp_path, monkeypatch, flag):
    _project_case(tmp_path, monkeypatch, flag, enabled=True, executes=False)


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", "yes", "on"])
@pytest.mark.parametrize("enabled", [False, True])
def test_project_opt_in_still_requires_plugin_enablement(tmp_path, monkeypatch, flag, enabled):
    _project_case(tmp_path, monkeypatch, flag, enabled=enabled, executes=enabled)


def _project_case(tmp_path, monkeypatch, flag, *, enabled, executes):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    plugin = project / ".hermes" / "plugins" / "probe"
    plugin.mkdir(parents=True)
    marker = tmp_path / "executed"
    (plugin / "plugin.yaml").write_text(json.dumps({"name": "probe", "version": "1.0.0"}))
    (plugin / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
        "def register(ctx):\n    pass\n"
    )
    (home / "config.yaml").write_text(json.dumps({"plugins": {"enabled": ["probe"] if enabled else []}}))
    monkeypatch.chdir(project)
    monkeypatch.setenv("HERMES_HOME", str(home))
    if flag is None:
        monkeypatch.delenv("HERMES_ENABLE_PROJECT_PLUGINS", raising=False)
    else:
        monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", flag)
    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: tmp_path / "empty")
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])
    manager = PluginManager()
    try:
        manager.discover_and_load()
        assert marker.exists() is executes
    finally:
        manager.unload()