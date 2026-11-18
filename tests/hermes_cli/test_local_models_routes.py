"""Local model recommendation contracts independent of transport."""
import pytest


def test_status_lists_staged_models_with_labels(tmp_path, monkeypatch):
    from hermes_cli.local_runtime import local_models

    models = tmp_path / "models"
    models.mkdir()
    model = models / "Some-Model.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * 2048)
    monkeypatch.setattr(local_models, "_runtime_section", dict)
    monkeypatch.setattr(local_models, "_runtime_target", lambda: ("b1", "cpu"))
    monkeypatch.setattr(local_models.binaries, "installed_engine", lambda backend: None)
    monkeypatch.setattr(local_models.bootstrap, "adopt_legacy_models", list)
    monkeypatch.setattr(local_models.bootstrap, "models_dir", lambda: models)
    monkeypatch.setattr(local_models.bootstrap, "staged_models", lambda: [model])
    monkeypatch.setattr(local_models, "_state_endpoint", lambda: None)
    monkeypatch.setattr(local_models, "_active_llamacpp_model_id", lambda: None)
    monkeypatch.setattr(local_models.load_progress, "get_loading_progress", dict)

    status = local_models.local_models_status()
    assert status["server_running"] is False
    assert status["runtime_installed"] is False
    row = status["models"][0]
    assert row["id"] == "Some-Model"
    assert row["size_bytes"] > 0
    assert row["size_label"].endswith("GB")


def test_quickstart_falls_back_past_entries_that_are_never_recommended(monkeypatch):
    """When the recommended entry can't start, quickstart tries the other recommendable entries.
    Nemotron 3.5 Bolt is an option only, so the fallback refuses rather than download it."""
    from hermes_cli.local_runtime import catalog
    from hermes_cli.local_runtime.estimator import HardwareBudget
    from hermes_cli.local_runtime import local_models

    budget = HardwareBudget(44 << 30, 48 << 30, 64 << 30, uma=False)
    bolt = catalog.catalog_by_id()["nemotron-3.5-bolt-74b-a9b"]
    the_27b = catalog.catalog_by_id()["qwen3.8-27b"]
    real_select = catalog.select_variant
    monkeypatch.setattr(local_models, "_engine_too_old", lambda min_engine: False)
    monkeypatch.setattr(catalog, "recommended_entry", lambda *a, **kw: (the_27b, "best-quality-resident"))
    monkeypatch.setattr(catalog, "select_variant",
                        lambda entry, b: real_select(entry, b) if entry.id == bolt.id else None)

    assert real_select(bolt, budget) is not None
    with pytest.raises(local_models.LocalModelError) as refused:
        local_models._quickstart_target(local_models.QuickstartBody(), budget)
    assert 'no catalog model fits' in str(refused.value)
    explicit = local_models.QuickstartBody(model_id=bolt.id)
    assert local_models._quickstart_target(explicit, budget)[0].id == bolt.id
