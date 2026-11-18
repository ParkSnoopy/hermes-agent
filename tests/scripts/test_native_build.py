"""PM Bundle shares preparation, not the desktop product dependency closure."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from tests.pm._fixtures import stage_host_python

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/bundles/native_build.py"


def test_payload_transport_does_not_require_or_export_desktop_inputs(tmp_path):
    from scripts.ci.native_build_cache import describe_cache

    source = tmp_path / "source"
    source.mkdir()
    cache = tmp_path / "cache"
    description = describe_cache(source, cache, "payload-test")
    assert set(description["paths"]) == {str(cache / name) for name in ("tools", "python/runtime", "native")}
    assert "payload-test" in description["key"]
    assert not cache.exists()


def test_native_worker_passes_shared_toolchain_to_native_owner(tmp_path, monkeypatch):
    from scripts.bundles import native_build
    from scripts.bundles import toolchain, native

    request = {name: str(tmp_path / name) for name in ("source", "work", "cache", "out")}
    request["ref"] = "a" * 40
    selected = {"UV_CACHE_DIR": str(tmp_path / "native-wheel-identity"), "CC": "prepared-compiler"}
    monkeypatch.setattr("scripts.bundles.source.require_source", lambda *args: None)
    prepared_tools = []
    def tools(source, work, cache, inherited):
        prepared_tools.append((source, work, cache))
        return Path("python"), Path("node"), selected
    monkeypatch.setattr(toolchain, "prepare_tools", tools)
    calls = []
    def prepare_native(**kwargs):
        calls.append(kwargs)
        return tmp_path / "out.prepared.json"
    monkeypatch.setattr(native, "prepare_native", prepare_native)
    assert native_build.prepare_in_worker(request) == tmp_path / "out.prepared.json"
    assert prepared_tools == [(tmp_path / "source", tmp_path / "work", tmp_path / "cache")]
    assert calls == [{"source": tmp_path / "source", "out": tmp_path / "out", "ref": request["ref"],
                      "cache": Path(selected["UV_CACHE_DIR"]), "tools": tmp_path / "cache/tools", "env": selected}]


@pytest.mark.parametrize("target", ["darwin-arm64", "win32-x64"])
def test_native_rejects_retired_target_before_preparation_or_finish(tmp_path, monkeypatch, target):
    from scripts.bundles import native

    out = tmp_path / "payload"
    monkeypatch.setattr(native, "current_target", lambda: target)
    with pytest.raises(ValueError, match="unsupported target"):
        native.prepare_native(out=out, ref="HEAD", source=tmp_path, cache=tmp_path / "cache")
    assert not out.exists()
    prepared = tmp_path / "payload.prepared.json"
    prepared.write_text(json.dumps({"inputs": {"target": target}}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported target"):
        native.finish_native(prepared, {})
    assert not out.exists()
    monkeypatch.setattr(native, "current_target", lambda: "linux-x64")
    with pytest.raises(ValueError, match="unsupported target"):
        native.finish_native(prepared, {})
    assert not out.exists()


@pytest.mark.parametrize("target", ["darwin-arm64", "darwin-x64", "win32-x64", "win32-arm64"])
@pytest.mark.parametrize("existing", [False, True])
def test_outer_build_entrypoints_reject_retired_targets_before_writes(tmp_path, monkeypatch, target, existing):
    from pm import store
    from scripts.bundles import native_build, source, stage, toolchain

    out = tmp_path / "payload"
    manifest = out / "manifest.json"
    if existing:
        out.mkdir()
        manifest.write_text("previous accepted payload", encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("retired target reached source preparation or staging")

    monkeypatch.setattr(store, "current_target", lambda: target)
    monkeypatch.setattr(stage, "current_target", lambda: target)
    monkeypatch.setattr(source, "git", forbidden)
    monkeypatch.setattr(source, "require_source", forbidden)
    monkeypatch.setattr(toolchain, "run_preparation", forbidden)
    monkeypatch.setattr(toolchain, "prepare_tools", forbidden)
    monkeypatch.setattr(stage, "stage_native", forbidden)
    request = {name: str(tmp_path / name) for name in ("source", "work", "cache")}
    request.update(out=str(out), ref="a" * 40)
    with pytest.raises(ValueError, match="unsupported target"):
        native_build.prepare(*(Path(request[name]) for name in ("source", "work", "cache", "out")), request["ref"])
    with pytest.raises(ValueError, match="unsupported target"):
        native_build.prepare_in_worker(request)
    for products in ([], ["--tui", str(tmp_path / "tui")]):
        with pytest.raises(ValueError, match="unsupported target"):
            stage.main(["--out", str(out), *products])
    assert not (tmp_path / "work").exists()
    assert not (tmp_path / "cache").exists()
    if existing:
        assert manifest.read_text(encoding="utf-8") == "previous accepted payload"
        assert list(out.iterdir()) == [manifest]
    else:
        assert not out.exists()


@pytest.mark.platforms("posix")
def test_native_cli_consumes_real_minimal_preparation_without_bootstrap(tmp_path):
    from scripts.build.inputs import AgentInputs, RESOURCE_ENV
    from scripts.bundles.native_prepared import publish_prepared
    from pm.store import current_target

    out = tmp_path / "payload"
    code = out / "hermes-agent"
    code.mkdir(parents=True)
    (code / "pyproject.toml").write_text('[project]\nname="native-fixture"\nversion="1.0"\n[project.scripts]\nprobe="entry:main"\n')
    (code / "entry.py").write_text('def main():\n print("native fixture")\n return 0\n')
    for name in RESOURCE_ENV:
        (code / name).mkdir()
    python = stage_host_python(out / "tools/python/bin/python3")
    site = out / "venv/lib/site-packages"
    site.mkdir(parents=True)
    runtime = out / "pm-runtime"
    (runtime / "lib/site-packages").mkdir(parents=True)
    (runtime / "pm-runtime.json").write_text(json.dumps({"python": "../tools/python/bin/python3", "sitePackages": "lib/site-packages"}))
    features = out / "enabled-features.json"
    features.write_text('{"extras":[]}')
    (out / "uv-cache").mkdir()
    prepared = publish_prepared(out, ROOT, "a" * 40, AgentInputs(
        project=code / "pyproject.toml", code=code, repo="hermes-agent", placement="contained",
        target=current_target(), python=python, site_packages=site, environment=out / "venv",
        tools=out / "tools", pm_runtime=runtime, features=features, ref="fixture",
        resources={name: code / name for name in RESOURCE_ENV},
    ))
    env = {**os.environ, "UV_OFFLINE": "1", "HERMES_HOME": str(tmp_path / "private")}
    command = [sys.executable, "-S", "-B", str(SCRIPT), "--prepared", str(prepared)]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["runtime"]["commands"] == {"probe": "bin/probe"}
    assert not (code / "hermes_cli/tui_dist").exists()
    assert not (code / "hermes_cli/web_dist").exists()
    probe = subprocess.run([str(out / "bin/probe")], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "native fixture"
    assert not (tmp_path / "private").exists()
    (site / "changed.py").write_text("changed = True")
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode != 0
    assert "prepare" in result.stderr
    assert not (out / "manifest.json").exists()


def test_source_custody_rejects_changed_revision_and_dirty_checkout(tmp_path, monkeypatch):
    from scripts.bundles import source

    calls = []
    def git(checkout, *args):
        calls.append((checkout, args))
        return responses.pop(0)

    monkeypatch.setattr(source, "git", git)
    responses = ["admitted", ""]
    source.require_source(tmp_path, "admitted")
    assert len(calls) == 2
    responses = ["different"]
    with pytest.raises(ValueError, match="changed revision"):
        source.require_source(tmp_path, "admitted")
    responses = ["admitted", "?? untracked"]
    with pytest.raises(ValueError, match="clean source checkout"):
        source.require_source(tmp_path, "admitted")
