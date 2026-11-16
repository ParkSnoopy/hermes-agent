from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.readiness import collect_runtime_readiness


def test_collect_runtime_readiness_reports_healthy_local_runtime(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  provider: openrouter\n  model: test/model\n",
        encoding="utf-8",
    )
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("HERMES_HOME", str(home))

    result = collect_runtime_readiness(
        configured_model="test/model",
        runtime_status={
            "gateway_state": "running",
            "platforms": {"telegram": {"state": "connected"}},
            "updated_at": "2026-07-09T00:00:00Z",
        },
        active_api_runs=2,
    )

    assert result["status"] == "ok"
    assert result["checks"]["state_db"]["status"] == "ok"
    assert result["checks"]["session_store"]["status"] == "ok"
    assert result["checks"]["config"]["status"] == "ok"
    assert result["checks"]["model"]["status"] == "ok"
    assert result["checks"]["gateway"]["status"] == "ok"
    assert result["checks"]["background_queues"]["active_api_runs"] == 2
    assert result["checks"]["disk"]["status"] in {"ok", "degraded"}


def test_collect_runtime_readiness_degrades_on_invalid_config_and_stopped_gateway(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: [unterminated", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    result = collect_runtime_readiness(
        configured_model="",
        runtime_status={"gateway_state": "stopped", "platforms": {}},
    )

    assert result["status"] == "degraded"
    assert result["checks"]["config"]["status"] == "degraded"
    assert result["checks"]["model"]["status"] == "degraded"
    assert result["checks"]["gateway"]["status"] == "degraded"
    # Readiness is diagnostic data, not an exception or a destructive repair.
    assert (home / "config.yaml").read_text(encoding="utf-8") == "model: [unterminated"


def test_readiness_uses_running_session_store_state_over_independent_probe(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    home.mkdir()
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("HERMES_HOME", str(home))

    unavailable = collect_runtime_readiness(
        configured_model="test/model",
        runtime_status={
            "gateway_state": "running",
            "platforms": {},
            "session_store": {"status": "unavailable"},
        },
    )

    assert unavailable["checks"]["state_db"]["status"] == "ok"
    assert unavailable["checks"]["session_store"] == {"status": "unavailable"}
    assert unavailable["status"] == "degraded"

    recovered = collect_runtime_readiness(
        configured_model="test/model",
        runtime_status={
            "gateway_state": "running",
            "platforms": {},
            "session_store": {"status": "ok"},
        },
    )
    assert recovered["checks"]["session_store"] == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["default", "other", "missing", "../escape"])
async def test_session_readiness_authorizes_profile_before_probe(tmp_path, monkeypatch, profile):
    from gateway.session_readiness import readiness
    from hermes_cli import profiles
    from hermes_state_runtime import RuntimeStoreError

    home = tmp_path / "home"
    home.mkdir()
    other = tmp_path / "profiles" / "other"
    other.mkdir(parents=True)
    (other / "config.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: tmp_path / "profiles")
    probes = []

    async def run_probe(probe):
        probes.append(probe)
        return {"provider_configured": True}

    monkeypatch.setattr("gateway.session_readiness.asyncio.to_thread", run_probe)
    authority = SimpleNamespace(profile_id=str(home))
    actor = SimpleNamespace(profile_id=str(home), capabilities={"session:create"})
    if profile == "default":
        result = await readiness(authority, actor, {"profile": profile}, runtime=False)
        assert result == {"provider_configured": True}
        assert len(probes) == 1
    else:
        with pytest.raises(RuntimeStoreError, match="profile_mismatch"):
            await readiness(authority, actor, {"profile": profile}, runtime=False)
        assert probes == []

