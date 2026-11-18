"""Profile resolution, import, merge, and validation for the machine's UI preferences store."""
from __future__ import annotations

import json
import multiprocessing
import os

import pytest

from hermes_cli import ui_preferences


def test_a_profile_home_and_the_root_home_resolve_the_same_store(monkeypatch, tmp_path):
    root = tmp_path / "custom-root"
    (root / "profiles" / "coder").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    from_root = ui_preferences.store_path()
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "coder"))
    from_profile = ui_preferences.store_path()

    assert from_root == from_profile == root / "ui-preferences.json"


def test_a_custom_home_under_the_native_home_is_its_own_root(monkeypatch, tmp_path):
    sandbox = tmp_path / ".hermes" / "sandbox"
    sandbox.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(sandbox))

    assert ui_preferences.store_path() == sandbox.resolve() / "ui-preferences.json"


def test_the_seed_import_only_adds_keys_and_never_creates_an_empty_store(tmp_path):
    """A client's first contact fills what the store lacks: the client that has data fills a
    store an empty client reached first, and nothing already stored is overwritten."""
    path = tmp_path / "ui-preferences.json"
    empty = ui_preferences.write_preferences({}, seed=True, path=path)
    first = ui_preferences.write_preferences({"hermes.desktop.sessionListDensity": "detailed"},
                                             seed=True, path=path)
    second = ui_preferences.write_preferences({"hermes.desktop.sessionListDensity": "compact",
                                               "hermes-desktop-mode-v1": "dark"}, seed=True, path=path)

    assert empty == {"exists": False, "revision": 0, "values": {}}
    assert first["values"] == {"hermes.desktop.sessionListDensity": "detailed"}
    assert second["values"] == {"hermes.desktop.sessionListDensity": "detailed", "hermes-desktop-mode-v1": "dark"}
    assert second["generation"] == first["generation"]
    assert json.loads(path.read_text())["values"] == second["values"]


@pytest.mark.platforms("linux", "macos")
def test_an_unreadable_or_corrupt_store_is_never_merged_over(tmp_path):
    """Only a missing file is no store. An unreadable one refuses the write (the client retries)
    and stays intact; a corrupt one is kept aside and replaced by a NEW generation, which every
    open window follows whatever revision it had seen."""
    path = tmp_path / "ui-preferences.json"
    kept = ui_preferences.write_preferences({"hermes-desktop-theme-v2": "nous", "hermes.desktop.keybinds": "{}"},
                                            path=path)
    before = path.read_bytes()
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("running as a user that reads mode-000 files (root)")
        with pytest.raises(ui_preferences.PreferencesBusy):
            ui_preferences.write_preferences({"hermes-desktop-mode-v1": "dark"}, path=path)
    finally:
        path.chmod(0o600)
    assert path.read_bytes() == before

    torn = before[:-5]
    path.write_bytes(torn)
    assert ui_preferences.read_preferences(path)["exists"] is False
    after = ui_preferences.write_preferences({"hermes-desktop-mode-v1": "dark"}, path=path)

    backups = list(tmp_path.glob("ui-preferences.json.corrupt-*"))
    assert [backup.read_bytes() for backup in backups] == [torn]
    assert after["values"] == {"hermes-desktop-mode-v1": "dark"}
    assert after["generation"] != kept["generation"]


def test_writes_merge_per_key_and_concurrent_writers_keep_each_others_keys(tmp_path):
    path = tmp_path / "ui-preferences.json"
    ui_preferences.write_preferences({"hermes-desktop-mode-v1": "dark", "hermes.desktop.panesFlipped": "true"},
                                     path=path)

    # The directory lock serializes read-merge-replace across processes.
    keys = [f"hermes.desktop.k{n}" for n in range(8)]
    spawn = multiprocessing.get_context("spawn")
    writers = [spawn.Process(target=ui_preferences.write_preferences, args=({key: "1"},),
                             kwargs={"path": path}) for key in keys]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(60)
    assert [writer.exitcode for writer in writers] == [0] * len(keys)
    after = ui_preferences.write_preferences({"hermes.desktop.panesFlipped": None}, path=path)

    assert after["values"] == {"hermes-desktop-mode-v1": "dark", **{key: "1" for key in keys}}
    assert after["revision"] == 2 + len(keys)
    assert not path.with_name(path.name + ".lock").exists()


@pytest.mark.parametrize("changes", [{"hermes.agent.secret": "x"}, {"hermes.desktop.n": 3}, {"": "x"}])
def test_only_desktop_string_keys_are_stored(tmp_path, changes):
    path = tmp_path / "ui-preferences.json"
    with pytest.raises(ui_preferences.PreferencesError):
        ui_preferences.write_preferences(changes, path=path)
    assert not path.exists()
