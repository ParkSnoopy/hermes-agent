"""Native memory setup resolves declarations, not ambient importability."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from hermes_cli import memory_setup


@pytest.mark.parametrize("declaration", ["pyproject", "python_dependencies", "pip_dependencies"])
def test_setup_admits_real_provider_union_and_keeps_selection_on_failure(tmp_path, monkeypatch, declaration):
    import pm
    from pm.environments import venv_python
    from pm.environments import selected_venv
    from tests.pm._fixtures import _wheel

    uv = shutil.which("uv")
    assert uv, "real PM admission test requires uv"
    core, home, wheels = (tmp_path / name for name in ("core", "home", "wheels"))
    for directory in (core, home, wheels):
        directory.mkdir()
    for name in ("existing_dep", "provider_dep"):
        _wheel(wheels, name, "1.0")
    (core / "pyproject.toml").write_text(
        '[project]\nname="core"\nversion="1"\nrequires-python=">=3.14"\n'
        '[tool.uv]\npackage=false\nno-index=true\n'
        f'find-links=[{json.dumps(wheels.as_posix())}]\n')
    incumbent, candidate = (home / "plugins" / name for name in ("incumbent", "candidate"))
    for directory in (incumbent, candidate):
        directory.mkdir(parents=True)
    (incumbent / "plugin.yaml").write_text('name: incumbent\npython_dependencies: ["existing_dep==1.0"]\n')
    manifest = candidate / "plugin.yaml"
    def declare(requirements):
        if declaration == "pyproject":
            manifest.write_text('name: candidate\n')
            (candidate / "pyproject.toml").write_text(
                '[project]\nname="candidate"\nversion="1"\nrequires-python=">=3.14"\n'
                f'dependencies={json.dumps(requirements)}\n[tool.uv]\npackage=false\n')
        else:
            manifest.write_text(f'name: candidate\n{declaration}: {json.dumps(requirements)}\n')
    declare(["provider_dep==1.0", "provider_dep==2.0"])
    config = home / "config.yaml"
    config.write_text('plugins:\n  enabled: [incumbent]\n')
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "tools"))
    monkeypatch.setattr("pm.paths.repo_root", lambda: core)
    monkeypatch.setattr("pm._uv._toolchain", lambda **kwargs: (Path(uv), Path(sys.executable)))
    monkeypatch.setattr("pm.client.is_runtime", lambda: True)
    monkeypatch.setattr("plugins.memory.find_provider_dir", lambda name: candidate)
    # An importable ambient module must not bypass admission or pin checks.
    (tmp_path / "provider_dep.py").write_text('VALUE="ambient, not admitted"\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "provider_dep", raising=False)
    import provider_dep
    assert provider_dep.VALUE == "ambient, not admitted"
    pm.lock_project(core, explicit=True, offline=True)
    pm.sync_venv(explicit=True)
    original = selected_venv(core)
    original_config = config.read_bytes()
    before_listing = set(home.rglob("*"))
    meta, inputs = memory_setup.memory_provider_dependency_inputs("candidate")
    assert meta["name"] == "candidate"
    assert list(inputs["plugins"].dirs) == [candidate]
    assert pm.venv_is_current(**inputs) is False
    assert set(home.rglob("*")) == before_listing, "listing must not prepare dependencies"

    with pytest.raises(pm.InstallError):
        memory_setup._install_dependencies("candidate")
    assert selected_venv(core) == original
    assert config.read_bytes() == original_config

    declare(["provider_dep==1.0"])
    meta, status = memory_setup.prepare_memory_provider_dependencies("candidate")
    assert meta["name"] == "candidate"
    assert status == "restart_required"
    python = venv_python(selected_venv(core))
    result = subprocess.run([str(python), "-I", "-c", "import existing_dep, provider_dep; print('both')"],
                            check=True, capture_output=True, text=True, timeout=30)
    assert result.stdout.strip() == "both"
    assert config.read_bytes() == original_config
    # Preparing an already-current union must not produce another generation.
    selected = selected_venv(core)
    memory_setup._install_dependencies("candidate")
    assert selected_venv(core) == selected
    _, inputs = memory_setup.memory_provider_dependency_inputs("candidate")
    assert pm.venv_is_current(**inputs) is True
    declare(["provider_dep==2.0"])
    _, inputs = memory_setup.memory_provider_dependency_inputs("candidate")
    assert pm.venv_is_current(**inputs) is False
    assert selected_venv(core) == selected


@pytest.mark.parametrize("current", [False, True])
@pytest.mark.parametrize("external_installed", [False, True])
def test_cli_setup_reports_restart_and_external_steps(tmp_path, monkeypatch, capsys, current, external_installed):
    provider = tmp_path / "provider"
    provider.mkdir()
    (provider / "plugin.yaml").write_text(json.dumps({
        "name": "provider", "extra": "mem0", "external_dependencies": [
            {"name": "external-sidecar", "check": "sidecar --version", "install": "install sidecar separately"},
        ],
    }))
    monkeypatch.setattr("plugins.memory.find_provider_dir", lambda name: provider)
    # The resolver seam is isolated; real union behavior is exercised above.
    syncs = []
    checks = []

    def sync(**kwargs):
        syncs.append(kwargs)

    def check(command, **kwargs):
        checks.append((command, kwargs))
        if not external_installed:
            raise FileNotFoundError("sidecar")

    monkeypatch.setattr("pm.sync_venv", sync)
    monkeypatch.setattr("pm.environments.running_from_selected_environment", lambda root: current)
    monkeypatch.setattr(subprocess, "run", check)
    memory_setup._install_dependencies("provider")
    assert syncs == [{"explicit": True, "extras": ["mem0"]}]
    assert checks == [(["sidecar", "--version"], {"check": True, "capture_output": True, "timeout": 5})]
    output = capsys.readouterr().out
    assert "Dependencies prepared for provider" in output
    assert ("Restart Hermes" in output) is not current
    assert ("install sidecar separately" in output) is not external_installed


def test_cli_python_preparation_failure_is_not_reported_as_success(tmp_path, monkeypatch, capsys):
    from pm.package import InstallError

    (tmp_path / "plugin.yaml").write_text('name: provider\nextra: mem0\n')
    monkeypatch.setattr("plugins.memory.find_provider_dir", lambda name: tmp_path)

    def sync(**kwargs):
        raise InstallError("provider", "Python preparation refused")

    monkeypatch.setattr("pm.sync_venv", sync)
    with pytest.raises(InstallError, match="Python preparation refused"):
        memory_setup._install_dependencies("provider")
    assert "Dependencies prepared" not in capsys.readouterr().out


def test_malformed_candidate_manifest_is_not_a_successful_noop(tmp_path, monkeypatch):
    (tmp_path / "plugin.yaml").write_text('name: [unterminated\n')
    monkeypatch.setattr("plugins.memory.find_provider_dir", lambda name: tmp_path)
    from pm.package import InstallError

    with pytest.raises(InstallError, match="Could not read plugin manifest"):
        memory_setup.prepare_memory_provider_dependencies("broken")


@pytest.mark.parametrize("manifest_lists", [False, True])
def test_setup_lists_the_requirements_pm_would_install(tmp_path, monkeypatch, manifest_lists):
    """Authored project requirements, not stale manifest lists, determine admission."""
    (tmp_path / "plugin.yaml").write_text(json.dumps({
        "name": "provider", **({"pip_dependencies": ["stale-manifest-dep"]} if manifest_lists else {}),
    }))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "provider"\nversion = "1"\ndependencies = ["provider_dep>=1,<2", "hermes-agent"]\n')
    monkeypatch.setattr("plugins.memory.find_provider_dir", lambda name: tmp_path)
    from pm.plugin_declarations import read_python_declaration

    _, inputs = memory_setup.memory_provider_dependency_inputs("provider")
    [candidate] = inputs["plugins"].dirs
    assert list(read_python_declaration(candidate).install_requirements) == ["provider_dep>=1,<2"]
