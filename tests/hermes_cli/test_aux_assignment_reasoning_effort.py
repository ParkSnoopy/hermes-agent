"""Auxiliary model choices preserve, set, and clear per-task reasoning overrides."""

import pytest
from hermes_cli.main_provider_setup import _reset_aux_to_auto, _save_aux_choice


@pytest.fixture
def saved(monkeypatch):
    store: dict = {}
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: store.update(cfg))
    return store


def test_reasoning_effort_choice_preserves_sets_and_clears_override(saved, monkeypatch):
    cfg = {"auxiliary": {"vision": {"provider": "openrouter", "model": "m1", "reasoning_effort": "low"}}}
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)

    # A plain provider/model re-assignment leaves an existing override alone.
    _save_aux_choice("vision", provider="openrouter", model="m2")
    assert cfg["auxiliary"]["vision"]["provider"] == "openrouter"
    assert cfg["auxiliary"]["vision"]["model"] == "m2"
    assert cfg["auxiliary"]["vision"]["reasoning_effort"] == "low"

    # The CLI passes the canonical level selected by its reasoning picker.
    _save_aux_choice("vision", provider="openrouter", model="m2", reasoning_effort="high")
    assert cfg["auxiliary"]["vision"]["reasoning_effort"] == "high"
    _save_aux_choice("vision", provider="openrouter", model="m2", reasoning_effort="none")
    assert cfg["auxiliary"]["vision"]["reasoning_effort"] == "none"

    # The provider-default choice clears this override; siblings and the pick survive.
    cfg["auxiliary"]["compression"] = {"provider": "openrouter", "model": "m3", "reasoning_effort": "max"}
    _save_aux_choice("vision", provider="openrouter", model="m2", reasoning_effort="")
    assert cfg["auxiliary"]["vision"]["reasoning_effort"] == ""
    assert cfg["auxiliary"]["vision"]["model"] == "m2"
    assert cfg["auxiliary"]["compression"]["reasoning_effort"] == "max"
    assert saved["auxiliary"] == cfg["auxiliary"]


def test_reset_clears_reasoning_overrides_without_changing_timeout(saved, monkeypatch):
    cfg = {"auxiliary": {"vision": {
        "provider": "openrouter", "model": "m1", "reasoning_effort": "high", "timeout": 17,
    }}}
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)
    monkeypatch.setattr("hermes_cli.main_provider_setup._all_aux_tasks", lambda: [("vision", "Vision", "")])

    _reset_aux_to_auto()

    assert cfg["auxiliary"]["vision"] == {
        "provider": "auto", "model": "", "reasoning_effort": "", "timeout": 17,
    }
    assert saved["auxiliary"] == cfg["auxiliary"]
