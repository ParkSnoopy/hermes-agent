"""MoA model edits through config set retain unrelated user settings."""

import hermes_yaml as yaml


def test_moa_model_edit_preserves_extra_keys_and_unrelated_sections(tmp_path, monkeypatch):
    from hermes_cli.config import load_config, read_raw_config, set_config_value

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    original = {
        "moa": {"save_traces": True, "trace_dir": "/custom/traces",
                "presets": {"default": {"reference_models": [{"provider": "openrouter", "model": "old"}],
                                        "operator_note": "keep"}}},
        "fallback_providers": [{"provider": "custom", "model": "fallback"}],
    }
    path.write_text(yaml.safe_dump(original), encoding="utf-8")
    stale = load_config()
    stale["fallback_providers"] = []
    set_config_value("moa.presets.default.reference_models",
                     '[{provider: openrouter, model: updated}]')
    saved = read_raw_config()
    assert saved["moa"]["presets"]["default"]["reference_models"][0]["model"] == "updated"
    assert saved["moa"]["presets"]["default"]["operator_note"] == "keep"
    assert saved["moa"]["save_traces"] is True
    assert saved["moa"]["trace_dir"] == "/custom/traces"
    assert saved["fallback_providers"] == original["fallback_providers"]


def test_new_preset_does_not_replace_existing_presets(tmp_path, monkeypatch):
    from hermes_cli.config import read_raw_config, set_config_value

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text("moa:\n  presets:\n    existing:\n      enabled: true\n", encoding="utf-8")
    set_config_value("moa.presets.fresh", "{enabled: true, reference_models: []}")
    presets = read_raw_config()["moa"]["presets"]
    assert set(presets) == {"existing", "fresh"}
    assert presets["fresh"]["enabled"] is True