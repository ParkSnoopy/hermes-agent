"""Retained in-process config writers serialize their read/modify/write cycles."""

from concurrent.futures import ThreadPoolExecutor
import threading

import hermes_yaml as yaml
import pytest


@pytest.mark.parametrize("writer", ["partial-save", "set-models", "delete-preset"])
def test_model_selection_and_config_edits_serialize(tmp_path, monkeypatch, writer):
    from hermes_cli import config
    from hermes_cli.model_switch import ModelSwitchResult, persist_model_selection

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text(
        "model:\n  default: old\n  provider: old\ndisplay:\n  skin: slate\n"
        "moa:\n  presets:\n    keep:\n      reference_models: []\n    doomed:\n      enabled: true\n",
        encoding="utf-8",
    )
    entered, release, contended = threading.Event(), threading.Event(), threading.Event()
    early_read = threading.Event()
    real_lock = config._CONFIG_LOCK
    real_replace = config.atomic_config_replace
    real_read = config.require_readable_config_before_write

    class ObservedLock:
        def __enter__(self):
            if not real_lock.acquire(blocking=False):
                contended.set()
                assert real_lock.acquire(timeout=30)
            return self

        def __exit__(self, *_exc):
            real_lock.release()

    def observed_read(*args, **kwargs):
        if entered.is_set() and not release.is_set():
            early_read.set()
        return real_read(*args, **kwargs)

    def gated_replace(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(30)
        return real_replace(*args, **kwargs)

    def second_write():
        if writer == "partial-save":
            config.save_config({"display": {"skin": "mono"}}, merge_existing=True)
        elif writer == "set-models":
            config.set_config_value("moa.presets.keep.reference_models", "[{model: updated}]")
        else:
            config.unset_config_value("moa.presets.doomed")

    monkeypatch.setattr(config, "_CONFIG_LOCK", ObservedLock())
    monkeypatch.setattr(config, "require_readable_config_before_write", observed_read)
    monkeypatch.setattr(config, "atomic_config_replace", gated_replace)
    result = ModelSwitchResult(success=True, new_model="new-model", target_provider="openrouter")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(persist_model_selection, result)
        try:
            assert entered.wait(30)
            second = pool.submit(second_write)
            assert contended.wait(30), "the second writer never waited on the held config lock"
            assert not early_read.is_set(), "the second writer read a stale snapshot before locking"
            assert not second.done()
        finally:
            release.set()
        first.result(timeout=60)
        second.result(timeout=60)

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["model"]["default"] == "new-model"
    assert saved["model"]["provider"] == "openrouter"
    assert saved["display"]["skin"] == ("mono" if writer == "partial-save" else "slate")
    presets = saved["moa"]["presets"]
    assert presets["keep"]["reference_models"] == ([{"model": "updated"}] if writer == "set-models" else [])
    assert ("doomed" in presets) is (writer != "delete-preset")