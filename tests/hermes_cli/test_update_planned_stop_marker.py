"""The updater's planned-stop marker keeps the gateway identity contract."""

import json
import os
from datetime import datetime

from hermes_cli.update_cmd_common import _planned_stop_marker_path, _write_update_planned_stop_marker


def test_planned_stop_marker_carries_target_identity(tmp_path, monkeypatch):
    from gateway import status

    monkeypatch.setattr(status, "_get_process_start_time", lambda pid: 123.0)
    assert _write_update_planned_stop_marker(tmp_path, 456)
    payload = json.loads(_planned_stop_marker_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["target_pid"] == 456
    assert payload["target_start_time"] == 123.0
    assert payload["stopper_pid"] == os.getpid()
    assert datetime.fromisoformat(payload["written_at"]).tzinfo is not None


def test_planned_stop_marker_reports_write_failure(tmp_path, monkeypatch):
    import utils
    from gateway import status

    monkeypatch.setattr(status, "_get_process_start_time", lambda pid: 123.0)
    def fail(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(utils, "atomic_json_write", fail)
    assert not _write_update_planned_stop_marker(tmp_path, 456)
