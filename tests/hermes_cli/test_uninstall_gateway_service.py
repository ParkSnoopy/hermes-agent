"""Gateway cleanup uses Linux systemd and only touches owned units."""
from types import ModuleType
import sys

import pytest

from hermes_cli import uninstall


@pytest.fixture
def gateway_stub(monkeypatch):
    gateway = ModuleType("hermes_cli.gateway")
    gateway.find_gateway_pids = list
    gateway.kill_gateway_processes = lambda: pytest.fail("no gateway to kill")
    monkeypatch.setitem(sys.modules, "hermes_cli.gateway", gateway)
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setenv("PREFIX", "")
    return gateway


def test_gateway_calls_systemd_remover(gateway_stub, monkeypatch):
    calls = []
    monkeypatch.setattr(uninstall, "_remove_systemd_gateway", lambda: calls.append(True) or True)
    assert uninstall.uninstall_gateway_service() is True
    assert calls == [True]


def test_gateway_reports_remover_failure(gateway_stub, monkeypatch, capsys):
    def fail():
        raise RuntimeError("fixture failure")
    monkeypatch.setattr(uninstall, "_remove_systemd_gateway", fail)
    assert uninstall.uninstall_gateway_service() is False
    assert "Could not check systemd gateway services" in capsys.readouterr().out


def test_termux_skips_systemd_but_reports_stopped_process(gateway_stub, monkeypatch):
    gateway_stub.find_gateway_pids = lambda: [123]
    gateway_stub.kill_gateway_processes = lambda: 1
    monkeypatch.setenv("TERMUX_VERSION", "fixture")
    monkeypatch.setattr(uninstall, "_remove_systemd_gateway", lambda: pytest.fail("no systemd in Termux"))
    assert uninstall.uninstall_gateway_service() is True


def test_systemd_removes_only_owned_units(gateway_stub, monkeypatch, tmp_path):
    user = tmp_path / "user.service"
    system = tmp_path / "system.service"
    user.write_text("owned", encoding="utf-8")
    system.write_text("foreign", encoding="utf-8")
    gateway_stub.get_service_name = lambda: "hermes-gateway.service"
    gateway_stub.get_systemd_unit_path = lambda system: tmp_path / ("system.service" if system else "user.service")
    gateway_stub._systemd_unit_belongs_to_current_home = lambda system: not system
    gateway_stub._systemctl_cmd = lambda system: ["systemctl", "--system" if system else "--user"]
    calls = []
    monkeypatch.setattr(uninstall.subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))
    assert uninstall._remove_systemd_gateway() is True
    assert not user.exists()
    assert system.read_text(encoding="utf-8") == "foreign"
    assert [cmd[2] for cmd in calls] == ["stop", "disable", "daemon-reload"]
