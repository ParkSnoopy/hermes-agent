"""MoA preset edits through the retained config interface preserve user state."""

import errno
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import hermes_yaml as yaml


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text(
        '# Operator rationale\noperator_settings:\n  keep: null  # explicit null\n  label: "off"\n\n'
        'moa:\n  default_preset: keep_a\n  privacy_filter: full\n  save_traces: true\n'
        '  trace_dir: /custom/traces\n  presets:\n    keep_a:\n'
        '      reference_models: [{provider: openrouter, model: first}]\n'
        '      operator_note: retain me\n    doomed:\n'
        '      reference_models: [{provider: openrouter, model: second}]\n'
        '    keep_b:\n      reference_models: [{provider: openrouter, model: third}]\n',
        encoding="utf-8",
    )
    return path


def test_delete_preset_survives_other_edit_restart_and_backup(tmp_path, monkeypatch):
    from hermes_cli import config_backups
    from hermes_cli.config import load_config, read_raw_config, set_config_value, unset_config_value

    path = _seed(tmp_path, monkeypatch)
    with monkeypatch.context() as clock:
        clock.setattr(config_backups.time, "strftime", lambda _fmt: "20000101-000000")
        assert "doomed" in load_config()["moa"]["presets"]
    unset_config_value("moa.presets.doomed")
    set_config_value("display.skin", "mono")
    saved = read_raw_config()
    assert set(saved["moa"]["presets"]) == {"keep_a", "keep_b"}
    assert saved["moa"]["presets"]["keep_a"]["operator_note"] == "retain me"
    assert saved["moa"]["privacy_filter"] == "full"
    assert saved["moa"]["save_traces"] is True
    assert saved["moa"]["trace_dir"] == "/custom/traces"
    assert saved["operator_settings"] == {"keep": None, "label": "off"}
    assert saved["display"]["skin"] == "mono"
    assert "terminal" not in saved
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Operator rationale\noperator_settings:\n")
    assert "# explicit null" in text
    assert 'label: "off"' in text

    # A fresh load creates a last-known-good backup of the edited document.
    probe = ("import json; from hermes_cli.config import load_config; "
             "print(json.dumps(sorted(load_config()['moa']['presets'])))")
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    for broken in (False, True):
        if broken:
            path.write_text("moa: [unterminated\n", encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", "-c", probe],
                                cwd=Path(__file__).resolve().parents[2], env=env,
                                capture_output=True, text=True, check=True, timeout=30)
        assert json.loads(result.stdout.splitlines()[-1]) == ["keep_a", "keep_b"]


def test_failed_read_refuses_delete_and_retry_succeeds(tmp_path, monkeypatch):
    from hermes_cli.config import unset_config_value
    import utils

    path = _seed(tmp_path, monkeypatch)
    before = path.read_bytes()
    real_load = utils.fast_safe_load
    failures = []

    def fail_once(stream):
        if getattr(stream, "name", None) == str(path) and not failures:
            failures.append(True)
            raise OSError(errno.EMFILE, "Too many open files")
        return real_load(stream)

    monkeypatch.setattr(utils, "fast_safe_load", fail_once)
    with pytest.raises(RuntimeError, match="change was not saved"):
        unset_config_value("moa.presets.doomed")
    assert failures == [True]
    assert path.read_bytes() == before
    unset_config_value("moa.presets.doomed")
    assert "doomed" not in yaml.safe_load(path.read_text(encoding="utf-8"))["moa"]["presets"]


def test_explicit_full_config_replacement_deletes_omitted_preset(tmp_path, monkeypatch):
    from hermes_cli.config import atomic_config_replace, require_readable_config_before_write

    path = _seed(tmp_path, monkeypatch)
    raw = require_readable_config_before_write(path)
    del raw["moa"]["presets"]["doomed"]
    atomic_config_replace(path, raw)
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(saved["moa"]["presets"]) == {"keep_a", "keep_b"}
    assert saved["moa"]["presets"]["keep_a"]["operator_note"] == "retain me"
    assert saved["moa"]["save_traces"] is True
    assert saved["operator_settings"] == {"keep": None, "label": "off"}


def test_named_presets_and_missing_map_load_correctly(tmp_path, monkeypatch):
    from hermes_cli.config import DEFAULT_CONFIG, load_config
    from hermes_cli.moa_config import normalize_moa_config

    path = _seed(tmp_path, monkeypatch)
    assert set(load_config()["moa"]["presets"]) == {"keep_a", "doomed", "keep_b"}
    path.write_text("model: {}\n", encoding="utf-8")
    assert normalize_moa_config(load_config()["moa"]) == normalize_moa_config(DEFAULT_CONFIG["moa"])