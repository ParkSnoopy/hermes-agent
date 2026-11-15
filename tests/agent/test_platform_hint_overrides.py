"""Tests for per-platform prompt-hint overrides (config.yaml → platform_hints).

Covers agent/system_prompt.py::_resolve_platform_hint — the resolver that
applies append/replace overrides to a platform's default hint. Feature added
for enterprise managed profiles (per-platform behavior without affecting other
platforms). See HA Core ticket: configurable per-platform prompt hints.
"""

import types

import pytest

from agent.system_prompt import _resolve_platform_hint, platform_hint
from gateway.platform_registry import PlatformEntry, platform_registry


def _agent(overrides):
    """Minimal stand-in carrying just the override attribute the resolver reads."""
    a = types.SimpleNamespace()
    a._platform_hint_overrides = overrides
    return a


DEFAULT = "You are on a synthetic transport. Do not use markdown."
EXTRA = "When tabular output would help, invoke the table_formatting skill."


@pytest.fixture
def synthetic_transport():
    name = "promptsynthetic"
    scope = platform_registry.current_scope_key()
    platform_registry.register(PlatformEntry(
        name=name, label="Synthetic", adapter_factory=lambda cfg: None, check_fn=lambda: True,
        platform_hint=DEFAULT, platform_hint_tool_notes={"example_tool": "Tool note."},
    ), scope=scope)
    yield name
    platform_registry.unregister(name, scope=scope)


def test_registered_transport_hint_and_tool_notes(synthetic_transport):
    agent = types.SimpleNamespace(platform=synthetic_transport, valid_tool_names=["example_tool"],
                                  _platform_hint_overrides={synthetic_transport: {"append": EXTRA}})
    assert platform_hint(agent) == f"{DEFAULT} Tool note.\n\n{EXTRA}"
    agent.valid_tool_names = []
    assert platform_hint(agent) == f"{DEFAULT}\n\n{EXTRA}"
    agent._platform_hint_overrides = {synthetic_transport: {"replace": EXTRA}}
    assert platform_hint(agent) == EXTRA


def test_registered_transport_hints_are_profile_scoped(synthetic_transport, tmp_path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    agent = types.SimpleNamespace(platform=synthetic_transport, valid_tool_names=[],
                                  _platform_hint_overrides={})
    token = set_hermes_home_override(tmp_path / "other-profile")
    scope = platform_registry.current_scope_key()
    platform_registry.register(PlatformEntry(
        name=synthetic_transport, label="Other", adapter_factory=lambda cfg: None,
        check_fn=lambda: True, platform_hint="Other profile hint.",
    ), scope=scope)
    try:
        assert platform_hint(agent) == "Other profile hint."
    finally:
        platform_registry.unregister(synthetic_transport, scope=scope)
        reset_hermes_home_override(token)
    assert platform_hint(agent) == DEFAULT


def test_cron_hint_uses_registered_destination(synthetic_transport):
    from gateway import session_context

    token = session_context._CRON_AUTO_DELIVER_PLATFORM.set(synthetic_transport)
    try:
        agent = types.SimpleNamespace(platform="cron", valid_tool_names=["example_tool"],
                                      _platform_hint_overrides={synthetic_transport: {"append": EXTRA}})
        hint = platform_hint(agent)
        assert "scheduled cron job" in hint
        assert f"Delivery destination ({synthetic_transport}): {DEFAULT} Tool note.\n\n{EXTRA}" in hint
    finally:
        session_context._CRON_AUTO_DELIVER_PLATFORM.reset(token)


class TestResolvePlatformHint:

    def test_missing_attr_returns_default(self):
        a = types.SimpleNamespace()  # no _platform_hint_overrides at all
        assert _resolve_platform_hint(a, "synthetic", DEFAULT) == DEFAULT


    def test_append_dict(self):
        a = _agent({"synthetic": {"append": EXTRA}})
        out = _resolve_platform_hint(a, "synthetic", DEFAULT)
        assert out == f"{DEFAULT}\n\n{EXTRA}"
        assert DEFAULT in out and EXTRA in out

    def test_replace_dict(self):
        a = _agent({"synthetic": {"replace": EXTRA}})
        out = _resolve_platform_hint(a, "synthetic", DEFAULT)
        assert out == EXTRA
        assert DEFAULT not in out



    def test_other_platform_unaffected(self):
        """An override for one transport must not change another's hint."""
        a = _agent({"synthetic": {"append": EXTRA}})
        other_default = "You are on another transport. Markdown works."
        assert _resolve_platform_hint(a, "other", other_default) == other_default


    # --- defensive / malformed input: never break prompt assembly ---







