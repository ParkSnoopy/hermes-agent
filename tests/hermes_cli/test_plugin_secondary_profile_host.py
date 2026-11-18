"""Native plugin managers and capability grants remain profile-scoped."""

import json
from pathlib import Path

import pytest

from hermes_cli import plugins
from hermes_cli.plugin_capabilities import record_consent
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from tui_gateway.server import _session_profile_runtime_scope


@pytest.fixture
def homes(tmp_path, monkeypatch):
    launch = tmp_path / ".hermes"
    beta = launch / "profiles" / "beta"
    for home, enabled in ((launch, True), (beta, False)):
        plugin = home / "plugins" / "probe"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text(json.dumps({"name": "probe", "version": "1.0.0"}))
        (plugin / "__init__.py").write_text(
            "def register(ctx):\n"
            "    ctx.register_hook('pre_tool_call', lambda **kw: {'home': ctx.profile_name})\n"
        )
        (home / "config.yaml").write_text(json.dumps({"plugins": {"enabled": ["probe"] if enabled else [],
                                                                   "disabled": [] if enabled else ["probe"]}}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: tmp_path / "empty")
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])
    plugins._reset_plugin_managers_for_tests()
    yield launch, beta
    plugins._reset_plugin_managers_for_tests()


def test_secondary_disabled_plugin_does_not_inherit_launch_registration(homes):
    launch, beta = homes
    with _session_profile_runtime_scope({"profile_home": launch}, hydrate_secrets=False):
        first = plugins.get_plugin_manager()
        first.discover_and_load()
        assert len(first.invoke_hook("pre_tool_call")) == 1
    with _session_profile_runtime_scope({"profile_home": beta}, hydrate_secrets=False):
        second = plugins.get_plugin_manager()
        second.discover_and_load()
        assert second is not first
        assert second.invoke_hook("pre_tool_call") == []
    with _session_profile_runtime_scope({"profile_home": launch}, hydrate_secrets=False):
        assert len(first.invoke_hook("pre_tool_call")) == 1


def test_secondary_enabled_plugin_does_not_enable_launch_copy(homes):
    launch, beta = homes
    (launch / "config.yaml").write_text(json.dumps({"plugins": {"disabled": ["probe"]}}))
    (beta / "config.yaml").write_text(json.dumps({"plugins": {"enabled": ["probe"]}}))
    with _session_profile_runtime_scope({"profile_home": beta}, hydrate_secrets=False):
        second = plugins.get_plugin_manager()
        second.discover_and_load()
        assert len(second.invoke_hook("pre_tool_call")) == 1
    with _session_profile_runtime_scope({"profile_home": launch}, hydrate_secrets=False):
        first = plugins.get_plugin_manager()
        first.discover_and_load()
        assert first is not second
        assert first.invoke_hook("pre_tool_call") == []


def test_consent_read_uses_owning_manager_not_ambient_profile(homes):
    launch, beta = homes
    with _session_profile_runtime_scope({"profile_home": launch}, hydrate_secrets=False):
        record_consent("probe", ["llm.side_question"], ["llm.side_question"])
        launch_ctx = PluginContext(PluginManifest(name="probe", key="probe", source="user"),
                                   plugins.get_plugin_manager())
    with _session_profile_runtime_scope({"profile_home": beta}, hydrate_secrets=False):
        beta_ctx = PluginContext(PluginManifest(name="probe", key="probe", source="user"),
                                 plugins.get_plugin_manager())
        assert launch_ctx._side_question_granted("llm.side_question")
        assert not beta_ctx._side_question_granted("llm.side_question")
        record_consent("probe", ["llm.side_question"], ["llm.side_question"])
    with _session_profile_runtime_scope({"profile_home": launch}, hydrate_secrets=False):
        assert beta_ctx._side_question_granted("llm.side_question")