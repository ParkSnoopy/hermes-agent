"""The default installation does not acquire the optional Node toolchain."""

from pm.install import _lockfile, drift
from pm.registry import source_install_packages, walk


def test_default_source_install_excludes_node_toolchain():
    roots = source_install_packages(_lockfile().names())
    closure = {package.name for package in walk(roots)}
    assert not {"node", "npm", "pnpm"} & closure


def test_drift_does_not_require_optional_node_toolchain(monkeypatch):
    from pm import install

    monkeypatch.setattr(install.paths, "facts_path", lambda: install.paths.lockfile_path())
    monkeypatch.setattr(install, "_installed_location", lambda *args: None)
    problems = drift(include_venv=False)
    assert "ffmpeg" in problems
    assert not {"node", "npm", "pnpm"} & problems.keys()
