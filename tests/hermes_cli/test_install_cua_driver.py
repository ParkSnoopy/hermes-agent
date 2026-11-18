"""CUA runtime validation, explicit PM setup, and native host integration."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _runtime_manifest(version="0.20.0", *, omit=()):
    required = {
        "mcp": {"--socket", "--grant"},
        "serve": {"--socket", "--permission-mode", "--capability-manifest",
                  "--approve-capability-manifest", "--embedded"},
        "stop": {"--socket"},
    }
    return {
        "binary_version": version,
        "mcp_invocation": {"command": "cua-driver", "args": ["mcp"]},
        "subcommands": [
            {"name": command, "args": [{"name": arg} for arg in sorted(args - set(omit))]}
            for command, args in required.items()
        ],
    }


@pytest.mark.parametrize("version,omit,ready", [
    ("0.20.0", (), True),
    ("0.19.4", (), False),
    ("bad-version", (), False),
    ("0.20.0", ("--approve-capability-manifest",), False),
])
def test_runtime_contract(version, omit, ready, tmp_path):
    from hermes_cli import tools_config_cua as cua

    result = SimpleNamespace(returncode=0, stderr="",
                             stdout=json.dumps(_runtime_manifest(version, omit=omit)))
    binary = str(tmp_path / "cua-driver")
    with patch("subprocess.run", return_value=result):
        state = cua._cua_driver_contract_status(binary)
    assert state["ready"] is ready
    assert state["binary"] == binary
    assert bool(state["reason"]) is not ready
    if omit:
        assert "serve --approve-capability-manifest" in state["reason"]


@pytest.mark.parametrize("upgrade", [False, True])
def test_pm_failure_is_reported_without_vendor_fallback(monkeypatch, capsys, upgrade):
    import pm
    from hermes_cli import tools_config_cua as cua

    monkeypatch.delenv("HERMES_CUA_DRIVER_CMD", raising=False)
    monkeypatch.setattr(cua, "_resolved_cua_driver_cmd", lambda: None)
    with patch.object(pm, "ensure", side_effect=pm.InstallError("cua-driver", "offline")) as ensure, \
         patch.object(cua.subprocess, "Popen", side_effect=AssertionError("vendor installer")):
        assert not cua.install_cua_driver(upgrade=upgrade)
    ensure.assert_called_once_with("cua-driver", explicit=True)
    assert "offline" in capsys.readouterr().out


@pytest.mark.parametrize("upgrade", [False, True])
def test_broken_override_never_acquires_standard_driver(tmp_path, monkeypatch, upgrade):
    import pm
    from hermes_cli import tools_config_cua as cua

    monkeypatch.setenv("HERMES_CUA_DRIVER_CMD", str(tmp_path / "missing-driver"))
    with patch.object(pm, "ensure", side_effect=AssertionError("override was replaced")):
        assert not cua.install_cua_driver(upgrade=upgrade)


@pytest.mark.parametrize("raw,expected", [
    ("cua-driver 0.20.0", "cua-driver 0.20.0"),
    ("banner\nsecond line", "banner"),
    ("\n\n  cua-driver 0.20.0  ", "cua-driver 0.20.0"),
    ("x" * 500, "x" * 120),
    ("", ""),
    ("   \n \n", ""),
])
def test_version_summary(raw, expected):
    from hermes_cli.tools_config_cua import _cua_version_summary

    assert _cua_version_summary(raw) == expected
