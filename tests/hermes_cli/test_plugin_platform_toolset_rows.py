"""Platform toolset rows recognize registered plugins and warn on invalid names."""

import logging

import pytest

_SEEDED_ROWS = {
    "cli": ["hermes-cli"],
    "example_plugin": ["hermes-example_plugin"],
}


def _toolset_errors(caplog):
    return [r.getMessage() for r in caplog.records if "#38798" in r.getMessage()]


@pytest.fixture(autouse=True)
def _fresh_warning_latch():
    import hermes_cli.tools_config as tc

    tc._warned_invalid_platform_toolsets.clear()
    yield
    tc._warned_invalid_platform_toolsets.clear()


@pytest.fixture
def plugin_platform():
    from gateway.platform_registry import PlatformEntry, platform_registry

    platform_registry.register(PlatformEntry(
        name="example_plugin", label="Example", adapter_factory=lambda _config: object(),
        check_fn=lambda: True,
    ))
    yield
    platform_registry.unregister("example_plugin")


def test_seeded_plugin_platform_rows_log_nothing_on_schema_build(caplog, plugin_platform):
    from hermes_cli.tools_config import _get_platform_tools

    with caplog.at_level(logging.WARNING, logger="hermes_cli.tools_config"):
        for platform in _SEEDED_ROWS:
            _get_platform_tools({"platform_toolsets": _SEEDED_ROWS}, platform)

    assert _toolset_errors(caplog) == []


def test_invalid_plugin_platform_row_still_warns(caplog, plugin_platform):
    from hermes_cli.tools_config import _get_platform_tools

    rows = {"cli": ["hermes-cli"], "example_plugin": ["hermes-exampel"]}
    with caplog.at_level(logging.WARNING, logger="hermes_cli.tools_config"):
        _get_platform_tools({"platform_toolsets": rows}, "example_plugin")

    errors = _toolset_errors(caplog)
    assert len(errors) == 1 and "'example_plugin'" in errors[0] and "hermes-exampel" in errors[0], errors


def test_seeded_config_rows_need_no_plugin():
    """Installer-seeded toolsets resolve without any plugin loaded."""
    from hermes_cli.config import read_raw_config, seed_config_file
    from hermes_constants import get_hermes_home
    from toolsets import validate_toolset

    assert seed_config_file(get_hermes_home() / "config.yaml") is True
    rows = read_raw_config().get("platform_toolsets") or {}
    unresolved = {p: names for p, names in rows.items() if not all(validate_toolset(n) for n in names)}
    assert unresolved == {}


def test_config_check_accepts_registered_plugin_platforms(plugin_platform):
    """Config validation accepts a registered platform's default toolset."""
    from hermes_cli.toolset_validation import saved_toolset_resolver, validate_platform_toolsets

    config = {"platform_toolsets": _SEEDED_ROWS}
    assert validate_platform_toolsets(_SEEDED_ROWS, saved_toolset_resolver(config)) == []

    typo = {"platform_toolsets": {"cli": ["hermes-cli"], "example_plugin": ["hermes-exampel"]}}
    warnings = validate_platform_toolsets(typo["platform_toolsets"], saved_toolset_resolver(typo))
    assert any("unknown toolset 'hermes-exampel'" in w for w in warnings), warnings


def test_misspelt_platform_key_naming_its_own_bundle_still_warns(caplog):
    """Only a KNOWN platform's own bundle is exempt: a misspelt key
    and both the session-time check and `hermes config check` still report it."""
    from hermes_cli.tools_config import _get_platform_tools
    from hermes_cli.toolset_validation import saved_toolset_resolver, validate_platform_toolsets

    rows = {"cli": ["hermes-cli"], "unknown_platform": ["hermes-unknown_platform"]}
    config = {"platform_toolsets": rows}
    with caplog.at_level(logging.WARNING, logger="hermes_cli.tools_config"):
        _get_platform_tools(config, "unknown_platform")

    errors = _toolset_errors(caplog)
    assert len(errors) == 1 and "'unknown_platform'" in errors[0], errors
    warnings = validate_platform_toolsets(rows, saved_toolset_resolver(config))
    assert any("unknown toolset 'hermes-unknown_platform'" in w for w in warnings), warnings
