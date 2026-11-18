"""The gateway-restart flow refuses BEFORE it stops anything (#125394, #77276).

The Desktop backend's ``_spawn_gateway_restart`` used to reap "orphan" gateways
before spawning ``hermes gateway restart``; when that child then refused (a
named profile under the host multiplexer) the profile had already lost its
gateway. The reap belongs to the child, after its refusal guard.
"""
from __future__ import annotations

from types import SimpleNamespace

from hermes_cli import gateway as gw






def test_cli_restart_reaps_after_the_refusal_guard(monkeypatch):
    from hermes_cli import gateway_lifecycle_guards

    order: list[str] = []
    monkeypatch.setattr(gw, "_refuse_from_inside_gateway", lambda *a, **k: None)
    monkeypatch.setattr(gw, "_guard_named_profile_under_multiplexer", lambda **k: order.append("guard"))
    monkeypatch.setattr(gateway_lifecycle_guards, "refuse_while_sessions_live", lambda *a: order.append("drain"))
    monkeypatch.setattr(gw, "_installed_service_kind", lambda: "systemd")
    monkeypatch.setattr(gw, "_reap_unsupervised_gateway_orphans", lambda *a, **k: order.append("reap"))
    monkeypatch.setattr(gw, "_service_call", lambda *a, **k: order.append("restart"))

    gw._cmd_restart(SimpleNamespace(system=False, all=False, force=False))

    assert order == ["guard", "drain", "reap", "restart"]


def test_cli_restart_reap_failure_does_not_block_restart(monkeypatch):
    """The reap is best-effort: a scan error must not abort the restart it precedes."""
    from hermes_cli import gateway_lifecycle_guards

    order: list[str] = []
    monkeypatch.setattr(gw, "_refuse_from_inside_gateway", lambda *a, **k: None)
    monkeypatch.setattr(gw, "_guard_named_profile_under_multiplexer", lambda **k: None)
    monkeypatch.setattr(gateway_lifecycle_guards, "refuse_while_sessions_live", lambda *a: None)
    monkeypatch.setattr(gw, "_installed_service_kind", lambda: "systemd")

    def _boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(gw, "_reap_unsupervised_gateway_orphans", _boom)
    monkeypatch.setattr(gw, "_service_call", lambda *a, **k: order.append("restart"))

    gw._cmd_restart(SimpleNamespace(system=False, all=False, force=False))

    assert order == ["restart"]
