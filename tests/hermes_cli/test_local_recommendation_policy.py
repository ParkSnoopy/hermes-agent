"""Automatic recommendations require residency; manual choices may spill."""

from __future__ import annotations

import pytest

from hermes_cli.local_runtime.catalog import recommended_entry, select_variant
from hermes_cli.local_runtime.estimator import HardwareBudget

_GIB = 1 << 30


def _discrete(size_gb: int, ram_gb: int = 64) -> HardwareBudget:
    total = size_gb * _GIB
    margin = max(2 * _GIB, int(total * 0.09))
    return HardwareBudget(
        usable_vram_bytes=max(0, total - margin),
        total_device_bytes=total,
        ram_available_bytes=ram_gb * _GIB,
        uma=False,
    )


def _unified(size_gb: int) -> HardwareBudget:
    total = size_gb * _GIB
    return HardwareBudget(
        usable_vram_bytes=int(total * 0.80),
        total_device_bytes=total,
        ram_available_bytes=0,
        uma=True,
    )


def test_small_discrete_cards_have_no_automatic_recommendation():
    for size_gb in (8, 16):
        assert recommended_entry(_discrete(size_gb)) is None


def test_24gb_discrete_card_recommends_resident_qwen_27b():
    picked = recommended_entry(_discrete(24))
    assert picked is not None
    assert picked[1] == "best-quality-resident"
    choice = select_variant(picked[0], _discrete(24))
    assert choice is not None and choice.zero_spill


def test_spilled_model_remains_explicitly_browseable_on_16gb():
    from hermes_cli.local_runtime.catalog import CATALOG

    entry = next(e for e in CATALOG if e.id == "qwen3.8-27b")
    choice = select_variant(entry, _discrete(16))
    assert choice is not None
    assert not choice.zero_spill
    assert recommended_entry(_discrete(16)) is None


def test_unified_memory_policy_does_not_inherit_discrete_recommendations():
    assert recommended_entry(_unified(24)) is None


@pytest.mark.parametrize("budget", [_discrete(16), _discrete(24), _unified(128)],
                         ids=["explicit-choice-required", "discrete", "unified"])
def test_catalog_and_quickstart_share_resource_recommendation(monkeypatch, budget):
    import subprocess
    import urllib.request

    def no_io(*args, **kwargs):
        raise AssertionError("recommendation must not start processes or perform network I/O")

    monkeypatch.setattr(subprocess, "run", no_io)
    monkeypatch.setattr(subprocess, "Popen", no_io)
    monkeypatch.setattr(urllib.request, "urlopen", no_io)

    from hermes_cli.local_runtime import catalog, local_models

    refreshes = []
    monkeypatch.setattr(catalog, "refresh_catalog_soon", lambda: refreshes.append("requested"))
    monkeypatch.setattr(local_models, "_eligible_entries", lambda: catalog.CATALOG)
    monkeypatch.setattr(local_models, "_engine_too_old", lambda min_engine: False)
    monkeypatch.setattr(local_models.hardware, "probe_budget", lambda *, planning: budget)
    monkeypatch.setattr(local_models.bootstrap, "staged_model_ids", list)

    picked = recommended_entry(budget)
    rows = local_models.local_models_catalog()["models"]
    recommended = [row for row in rows if row["recommended"]]
    assert refreshes == ["requested"]
    if picked is None:
        assert recommended == []
        with pytest.raises(local_models.LocalModelError, match="choose a model explicitly"):
            local_models._quickstart_target(local_models.QuickstartBody(), budget)
    else:
        entry, variant = local_models._quickstart_target(local_models.QuickstartBody(), budget)
        assert entry == picked[0]
        assert variant == select_variant(entry, budget).variant
        assert [row["id"] for row in recommended] == [entry.id]
        assert recommended[0]["recommended_reason"] == picked[1]
