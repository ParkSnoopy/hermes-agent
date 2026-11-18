"""``auxiliary.skills_hub`` is gone: no picker offers it and a leftover block is inert.

Skills Hub search/install never called an LLM, so the slot was a silent no-op (#40703,
#142403).
"""

from __future__ import annotations

import hermes_yaml as yaml


def test_no_backend_surface_offers_skills_hub_slot():
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_cli.main_provider_setup import _AUX_TASKS

    assert "skills_hub" not in DEFAULT_CONFIG["auxiliary"]
    assert "skills_hub" not in {key for key, _name, _desc in _AUX_TASKS}


def test_leftover_skills_hub_block_is_kept_but_never_acted_on(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    leftover = {"provider": "openrouter", "model": "some/model"}
    (home / "config.yaml").write_text(yaml.safe_dump({"auxiliary": {"skills_hub": leftover}}))
    monkeypatch.setenv("HERMES_HOME", str(home))
    from hermes_cli.config import load_config
    from hermes_cli.main_provider_setup import _reset_aux_to_auto

    cfg = load_config()
    assert cfg["auxiliary"]["skills_hub"]["model"] == "some/model"
    # Resetting the configured tasks leaves unknown legacy blocks untouched.
    _reset_aux_to_auto()
    assert load_config()["auxiliary"]["skills_hub"] == leftover
