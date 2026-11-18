"""Linux host contracts without starting a gateway or a service."""
import signal
from unittest.mock import Mock

import pytest

from gateway import status
from hermes_cli.gateway_runtime import _endpoint


@pytest.fixture(autouse=True)
def _moa_caches_isolated():
    pass


@pytest.fixture(autouse=True)
def _isolate_computer_use_approval_state():
    pass


@pytest.mark.platforms("linux")
def test_terminate_checks_identity_before_signal(monkeypatch):
    kill = Mock()
    monkeypatch.setattr(status.os, "kill", kill)
    monkeypatch.setattr(status, "_get_process_start_time", lambda pid: 99)
    with pytest.raises(OSError, match="identity changed"):
        status.terminate_pid(123, force=True, expected_start_time=98)
    kill.assert_not_called()
    status.terminate_pid(123, force=True, expected_start_time=99)
    kill.assert_called_once_with(123, signal.SIGKILL)


@pytest.mark.platforms("linux")
def test_exclusive_lock_is_released(tmp_path):
    path = tmp_path / "lock"
    with path.open("w+") as first, path.open("r+") as second:
        assert status._try_acquire_file_lock(first)
        assert not status._try_acquire_file_lock(second)
        status._release_file_lock(first)
        assert status._try_acquire_file_lock(second)
        status._release_file_lock(second)


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("supervisor, state", [("systemd", "ready"), ("external", "ready"),
                                                ("none", "ready"), ("launchd", "inaccessible"),
                                                ("windows", "inaccessible")])
def test_endpoint_supervisor_policy(tmp_path, supervisor, state):
    home = tmp_path.resolve()
    result = _endpoint({"runtime_protocol": 1, "served_profiles": [{"home": str(home), "profile_id": str(home)}],
                        "state": "ready", "capabilities": ["session-authority-v1"],
                        "authority_epoch": 1, "instance_id": "instance",
                        "api_origin": "http://127.0.0.1:1234", "supervisor": supervisor}, home)
    assert result.state == state
    if state == "inaccessible":
        assert result.reason_code == "invalid_supervisor"


@pytest.mark.platforms("linux")
def test_replacement_sends_guarded_term_before_force(tmp_path, monkeypatch):
    import time
    from hermes_cli import gateway_runtime_replace as replace

    monkeypatch.setattr(replace, "_identify", lambda home, timeout: (home, {}))
    monkeypatch.setattr(replace, "_verified_owner", lambda home, payload: (123, 99))
    monkeypatch.setattr(replace, "_write_planned_stop", lambda *args: None)
    monkeypatch.setattr(replace, "_wait_gone", Mock(side_effect=[False, True]))
    terminate = Mock()
    monkeypatch.setattr(status, "terminate_pid", terminate)
    assert replace.replace_incompatible_owner(tmp_path, deadline=time.monotonic() + 30) is None
    assert terminate.call_args_list == [
        ((123,), {"expected_start_time": 99}),
        ((123,), {"force": True, "expected_start_time": 99}),
    ]


@pytest.mark.platforms("linux")
def test_missing_owner_checks_private_lock(tmp_path, monkeypatch):
    from hermes_cli import gateway_runtime_discovery as discovery

    check = Mock()
    monkeypatch.setattr(discovery, "_private_lock", check)
    monkeypatch.setattr(status, "_is_gateway_runtime_lock_active_strict", lambda path: True)
    assert discovery.missing_owner_state(tmp_path) == "starting"
    check.assert_called_once_with(tmp_path / "gateway.lock")
