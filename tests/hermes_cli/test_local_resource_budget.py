"""Resource-limited prompt cache and residency calculations."""

from types import SimpleNamespace

import pytest

from hermes_cli.local_runtime import hardware as hw
from hermes_cli.local_runtime import presets
from hermes_cli.local_runtime.estimator import HardwareBudget, ModelProfile

G = 1 << 30



def test_prompt_cache_takes_what_the_model_leaves():
    live = HardwareBudget(0, 0, 0, uma=True, commit_room_bytes=int(45.7 * G))
    # 20 GiB estimated -> 25 GiB charged: 20.7 GiB left, capped at llama-server's default.
    assert hw.prompt_cache_bytes(live, 20 * G) == 8 * G
    # 31 GiB estimated -> ~38.75 GiB charged: ~6.9 GiB left for the cache.
    assert hw.prompt_cache_bytes(live, 31 * G) == pytest.approx(45.7 * G - 31 * G / 0.8, rel=1e-6)
    assert hw.prompt_cache_bytes(live, int(36.5 * G)) == 0  # under 512 MiB: off
    assert hw.prompt_cache_bytes(None, 10 * G) is None  # engine default without a live reading


def test_models_the_router_holds_together_share_the_cache_room():
    live = HardwareBudget(0, 0, 0, uma=True, commit_room_bytes=int(45.7 * G))
    # Two 14 GiB models: each charges 17.5 GiB, and 10.7 GiB remain for two caches.
    each = hw.prompt_cache_bytes(live, 14 * G, residents=2)
    assert each == pytest.approx((45.7 - 2 * 17.5) * G / 2, rel=1e-6)
    assert 2 * (each + 17.5 * G) <= 45.7 * G
    # A router that holds one model leaves that model the whole room.
    assert hw.prompt_cache_bytes(live, 14 * G, residents=1) == 8 * G


def test_router_holds_only_the_models_free_commit_fits(tmp_path, monkeypatch):
    """Capacity alone would admit two 12 GiB models; 20 GiB of commit holds one."""
    from hermes_cli.local_runtime import bootstrap

    monkeypatch.setattr(bootstrap, "staged_in", lambda mdir: [tmp_path / "a.gguf", tmp_path / "b.gguf"])
    monkeypatch.setattr(presets, "_launch_footprint", lambda gguf, budget, engine_tag=None: 12 * G)
    capacity = HardwareBudget(int(30 * G), int(50 * G), 0, uma=True)
    live = HardwareBudget(int(16 * G), int(50 * G), 0, uma=True, commit_room_bytes=int(20 * G))
    assert presets.admitted_residency_count(tmp_path, capacity, 4) == 2
    assert presets.admitted_residency_count(tmp_path, capacity, 4, live=live) == 1



def _staged(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    gguf = tmp_path / "model.gguf"
    gguf.touch()
    monkeypatch.setattr(presets, "read_gguf_header", lambda p: SimpleNamespace(path=p, sampling_defaults={}))
    monkeypatch.setattr(presets, "profile_from_gguf", lambda h: ModelProfile(
        name="model", weights_bytes=8 * G, embd_table_bytes=0, n_ctx_train=65536, layers=[]))
    return gguf


def test_preset_writes_the_cache_size_from_live_commit(tmp_path, monkeypatch):
    gguf = _staged(tmp_path, monkeypatch)
    capacity = HardwareBudget(int(16 * G), int(50 * G), 0, uma=True)
    live = HardwareBudget(int(16 * G), int(50 * G), 0, uma=True, commit_room_bytes=int(20 * G))
    entry = presets.preset_for_model(gguf, capacity, set(), live=live)
    assert 0 < int(entry.keys["cache-ram"]) <= 8192
    assert "cache-ram" not in presets.preset_for_model(gguf, capacity, set()).keys


def test_a_busy_desktop_narrows_but_never_refuses(tmp_path, monkeypatch):
    gguf = _staged(tmp_path, monkeypatch)
    capacity = HardwareBudget(int(16 * G), int(50 * G), 0, uma=True)
    live = HardwareBudget(int(1 * G), int(50 * G), 0, uma=True, commit_room_bytes=int(1.25 * G))
    entry = presets.preset_for_model(gguf, capacity, set(), live=live)
    assert entry.refusal is None and entry.window > 0
    assert entry.keys["cache-ram"] == "0"
