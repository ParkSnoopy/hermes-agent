"""System uv is selected independently of PM's pinned Python store."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from pm import _uv
from pm.package import InstallError


def test_toolchain_uses_path_uv_and_managed_python(tmp_path, monkeypatch):
    from pm import install

    uv = tmp_path / "external" / "uv"
    python = tmp_path / "store" / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.touch()
    calls = []
    monkeypatch.setattr(_uv.shutil, "which", lambda name: str(uv) if name == "uv" else None)
    monkeypatch.setattr(install, "ensure", lambda name, **kwargs: calls.append((name, kwargs)))
    monkeypatch.setattr(install, "_lockfile", lambda: object())
    monkeypatch.setattr(_uv, "current_target", lambda: "linux-x64")
    monkeypatch.setattr(_uv, "get_package", lambda name: SimpleNamespace(binary=lambda entry, target: python))
    facts = SimpleNamespace(get=lambda name: {"entry": "python"})
    store = SimpleNamespace(entry=lambda name: tmp_path / "store" / name)
    monkeypatch.setattr(install, "_installed_location", lambda *args: (facts, store))
    assert _uv._toolchain(realize=False) == (uv, python)
    assert calls == []
    assert _uv._toolchain(explicit=True) == (uv, python)
    assert calls == [("python", {"explicit": True})]


def test_missing_uv_fails_without_acquisition(monkeypatch):
    from pm import install

    monkeypatch.setattr(_uv.shutil, "which", lambda name: None)
    monkeypatch.setattr(install, "ensure", lambda *args, **kwargs: pytest.fail("unexpected tool acquisition"))
    assert _uv._toolchain(realize=False) is None
    with pytest.raises(InstallError, match="uv was not found on PATH"):
        _uv._toolchain()
