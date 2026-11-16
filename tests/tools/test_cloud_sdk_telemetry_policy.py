"""Cloud SDK telemetry must be disabled before clients initialize."""

import sys
import types


import pytest

from tools.environments import vercel_sandbox


@pytest.mark.parametrize("initial", [None, "0", "1"])
def test_vercel_disables_before_lazy_import(monkeypatch, initial):
    if initial is None:
        monkeypatch.delenv("VERCEL_TELEMETRY_DISABLED", raising=False)
    else:
        monkeypatch.setenv("VERCEL_TELEMETRY_DISABLED", initial)
    monkeypatch.delitem(sys.modules, "vercel.internal.telemetry.client", raising=False)
    monkeypatch.delitem(sys.modules, "vercel.internal.telemetry.tracker", raising=False)
    import pm

    seen = []
    monkeypatch.setattr(pm, "ensure_import", lambda name: seen.append((name, vercel_sandbox.os.environ["VERCEL_TELEMETRY_DISABLED"])))
    vercel_sandbox._ensure_vercel_sdk()
    assert seen == [("vercel", "1")]


def test_vercel_rejects_preimported_enabled_telemetry(monkeypatch):
    client = types.ModuleType("vercel.internal.telemetry.client")
    client._TELEMETRY_ENABLED = True
    monkeypatch.setitem(sys.modules, client.__name__, client)
    with pytest.raises(RuntimeError, match="already enabled"):
        vercel_sandbox._ensure_vercel_sdk()


def test_vercel_rejects_preexisting_enabled_client(monkeypatch):
    client = types.ModuleType("vercel.internal.telemetry.client")
    client._TELEMETRY_ENABLED = False
    tracker = types.ModuleType("vercel.internal.telemetry.tracker")
    tracker._telemetry_client = types.SimpleNamespace(_enabled=True)
    monkeypatch.setitem(sys.modules, client.__name__, client)
    monkeypatch.setitem(sys.modules, tracker.__name__, tracker)
    with pytest.raises(RuntimeError, match="already enabled"):
        vercel_sandbox._ensure_vercel_sdk()
