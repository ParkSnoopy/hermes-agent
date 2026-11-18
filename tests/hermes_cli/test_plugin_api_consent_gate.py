"""Native plugin capability consent gates, independent of retired HTTP routes."""

import pytest

from hermes_cli.plugin_capabilities import pending_capabilities, record_consent
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest, PluginToolOverrideError


def test_tool_override_requires_explicit_plugin_consent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ctx = PluginContext(PluginManifest(name="probe", key="probe", source="user"), PluginManager())
    args = ("consent_probe", "probe", {}, lambda args: "ok")
    with pytest.raises(PluginToolOverrideError):
        ctx.register_tool(*args, override=True)
    assert pending_capabilities("probe", ["tools.override"]) == ["tools.override"]
    record_consent("probe", ["tools.override"], ["tools.override"])
    try:
        assert ctx.register_tool(*args, override=True) is not None
        assert pending_capabilities("probe", ["tools.override", "llm.side_question"]) == ["llm.side_question"]
    finally:
        ctx._manager.unload()


def test_unknown_and_other_plugin_capabilities_remain_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    record_consent("other", ["tools.override"], ["tools.override"])
    ctx = PluginContext(PluginManifest(name="probe", key="probe", source="user"), PluginManager())
    assert not ctx.has_capability("tools.override")
    assert not ctx.has_capability("root.everything")