"""XDG cache and data cleanup respects the chosen preservation mode."""
from pathlib import Path

import pytest

from hermes_cli import uninstall


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_xdg_cache_removed_and_data_mode_semantics(fake_home):
    cache = fake_home / ".cache" / "hermes"
    data = fake_home / ".local" / "share" / "hermes"
    cache.mkdir(parents=True)
    data.mkdir(parents=True)

    assert uninstall._xdg_leftover_paths(full_uninstall=False) == ([cache], [data])
    assert uninstall._xdg_leftover_paths(full_uninstall=True) == ([cache, data], [])

    removed = uninstall.remove_desktop_app_leftovers(full_uninstall=False)
    assert cache in removed and not cache.exists()
    assert data.exists()


def test_xdg_data_removed_on_full_uninstall(fake_home):
    cache = fake_home / ".cache" / "hermes"
    data = fake_home / ".local" / "share" / "hermes"
    cache.mkdir(parents=True)
    data.mkdir(parents=True)

    removed = uninstall.remove_desktop_app_leftovers(full_uninstall=True)
    assert not cache.exists() and not data.exists()
    assert cache in removed and data in removed
