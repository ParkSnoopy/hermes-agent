"""Plugin auxiliary-task discovery, inheritance and CLI config across profiles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import hermes_cli.plugins as plugins_mod
from agent.auxiliary_client import _get_auxiliary_task_config
from hermes_cli.plugins import PluginManager
from hermes_cli.main_provider_setup import _AUX_TASKS, _all_aux_tasks, _reset_aux_to_auto, _save_aux_choice
from tui_gateway.model_switch import _session_profile_runtime_scope


def _write_plugin(home: Path, name: str, register_call: str) -> None:
    plugin_dir = home / "plugins" / name
    plugin_dir.mkdir(parents=True)
    # JSON is valid YAML; keeps the test free of a yaml dependency.
    (plugin_dir / "plugin.yaml").write_text(
        json.dumps({"name": name, "version": "0.1.0", "description": f"{name} probe"}))
    (plugin_dir / "__init__.py").write_text(f"def register(ctx):\n    ctx.{register_call}\n")


def _write_home(home: Path, plugins: dict, compression_model: str) -> None:
    for name, call in plugins.items():
        _write_plugin(home, name, call)
    (home / "config.yaml").write_text(json.dumps({
        "plugins": {"enabled": list(plugins)},
        "auxiliary": {"compression": {"provider": "openrouter", "model": compression_model}},
    }))


_SIDE = ("register_auxiliary_task('side_task', display_name='Side model', "
         "description='side model for side', inherit_from='compression')")
_ALPHA = ("register_auxiliary_task('alpha_task', display_name='Alpha side model', "
          "description='side model for alpha_plugin', defaults={'timeout': 7})")


@pytest.fixture
def two_profile_homes(tmp_path, monkeypatch):
    """Process home ``a`` (plugins side + alpha) and named profile ``b`` (plugin side only), each
    with its own ``auxiliary.compression`` model."""
    home_a = tmp_path / ".hermes"
    home_b = home_a / "profiles" / "b"
    _write_home(home_a, {"side": _SIDE, "alpha_plugin": _ALPHA}, "vendor/model-a")
    _write_home(home_b, {"side": _SIDE}, "vendor/model-b")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home_a))
    monkeypatch.setattr(plugins_mod, "get_bundled_plugins_dir", lambda: tmp_path / "empty-bundled")
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])
    plugins_mod._reset_plugin_managers_for_tests()
    yield home_a, home_b
    plugins_mod._reset_plugin_managers_for_tests()


def _resolved_model(profile):
    with _session_profile_runtime_scope({"profile_home": profile}, hydrate_secrets=False):
        return _get_auxiliary_task_config("side_task").get("model")


def test_listing_is_per_profile_and_carries_inheritance(two_profile_homes):
    home_a, home_b = two_profile_homes
    with _session_profile_runtime_scope({"profile_home": home_a}, hydrate_secrets=False):
        tasks_a = _all_aux_tasks()
        assert tasks_a[:len(_AUX_TASKS)] == list(_AUX_TASKS)
        assert ("side_task", "Side model", "side model for side") in tasks_a
        assert any(row[0] == "alpha_task" for row in tasks_a)
    with _session_profile_runtime_scope({"profile_home": home_b}, hydrate_secrets=False):
        assert "alpha_task" not in {row[0] for row in _all_aux_tasks()}
    assert (_resolved_model(home_a), _resolved_model(home_b)) == ("vendor/model-a", "vendor/model-b")


def test_pin_persists_per_profile_and_is_honored_by_resolution(two_profile_homes):
    home_a, home_b = two_profile_homes
    with _session_profile_runtime_scope({"profile_home": home_b}, hydrate_secrets=False):
        _save_aux_choice("side_task", provider="nous", model="hermes-4")
    assert "side_task" not in (home_a / "config.yaml").read_text()
    assert (_resolved_model(home_b), _resolved_model(home_a)) == ("hermes-4", "vendor/model-a")
    with _session_profile_runtime_scope({"profile_home": home_b}, hydrate_secrets=False):
        _save_aux_choice("side_task", provider="auto")
    assert _resolved_model(home_b) == "vendor/model-b"
    with _session_profile_runtime_scope({"profile_home": home_b}, hydrate_secrets=False):
        _save_aux_choice("compression", provider="openrouter", model="vendor/model-b2")
    assert (_resolved_model(home_b), _resolved_model(home_a)) == ("vendor/model-b2", "vendor/model-a")


def test_assignment_reset_covers_plugin_tasks(two_profile_homes):
    from hermes_cli.config import load_config

    home_a, home_b = two_profile_homes
    with _session_profile_runtime_scope({"profile_home": home_a}, hydrate_secrets=False):
        _save_aux_choice("alpha_task", provider="openrouter", model="fast-model")
        assert load_config()["auxiliary"]["alpha_task"]["model"] == "fast-model"
        assert _reset_aux_to_auto() > 0
        entry = load_config()["auxiliary"]["alpha_task"]
        assert entry["provider"] == "auto" and entry["model"] == ""
    assert _resolved_model(home_b) == "vendor/model-b"


def test_plugin_discovery_failure_leaves_builtins_working(two_profile_homes, monkeypatch):
    def _boom():
        raise RuntimeError("plugin scan exploded")

    # Patch where production reads: the resolver imports the name from hermes_cli.plugins at call time.
    monkeypatch.setattr(plugins_mod, "get_plugin_auxiliary_tasks", _boom)
    assert _all_aux_tasks() == list(_AUX_TASKS)
