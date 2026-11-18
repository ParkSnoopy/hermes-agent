"""A Linux pause failure retains only obligations rollback could not resume."""

from copy import deepcopy

import pytest

from gateway import status
from hermes_cli import update_cmd_posix_pause as pause
from hermes_cli import update_pause_record as record


@pytest.mark.parametrize("restore_fails", [False, True])
def test_pause_failure_syncs_outstanding_rollback_debt(monkeypatch, restore_fails):
    unit = {"kind": "systemd", "scope": "user", "unit": "hermes-gateway.service", "pid": 123}
    token = pause._empty_token()
    saved = []
    monkeypatch.setattr(pause, "_discover_systemd", lambda: [unit])
    monkeypatch.setattr(pause, "_left_running", lambda units: (units, []))
    monkeypatch.setattr(pause, "_discover_bare", lambda pids: ([], []))
    monkeypatch.setattr(status, "get_process_start_time", lambda pid: 1.0)
    monkeypatch.setattr(record, "identity", lambda pid: {"ct": "ct:1"})
    monkeypatch.setattr(record, "record_pause", lambda *a: None)
    monkeypatch.setattr(record, "mark_stop_requested", lambda *a, **kw: None)
    monkeypatch.setattr(record, "sync", lambda state: saved.append(deepcopy(state)))
    monkeypatch.setattr(pause, "_RESTARTED", {"pids": set(), "units": set(), "labels": set()})

    def stop(*args):
        raise OSError("stop failed")

    def start(_entry):
        if restore_fails:
            raise OSError("start failed")
        return 456

    monkeypatch.setattr(pause, "_stop_gateways", stop)
    monkeypatch.setattr(pause, "_start_unit", start)
    pause._pause(token)
    assert saved[-1]["resume_needed"] is restore_fails
    assert saved[-1]["posix_units"] == ([unit] if restore_fails else [])