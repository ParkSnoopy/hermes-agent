"""Incomplete-fleet-restart hints for systemd units."""

from __future__ import annotations

import pytest

from hermes_cli.update_cmd_fleet import _warn_incomplete_gateway_fleet_restart

pytestmark = pytest.mark.platforms("linux")



def test_systemd_units_keep_systemctl_hint(capsys):
    _warn_incomplete_gateway_fleet_restart(["hermes-gateway-coder"])
    out = capsys.readouterr().out
    assert "systemctl" in out
    assert "launchctl" not in out
