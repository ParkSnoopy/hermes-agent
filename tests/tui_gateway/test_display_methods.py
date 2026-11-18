"""Linux screen installation, profile scope, and lease privacy boundaries."""

from __future__ import annotations

import hashlib
import json
import threading

import pytest


def test_install_worker_keeps_the_requested_profile_scope(tmp_path, monkeypatch):
    from hermes_constants import get_hermes_home
    from tools.bot_desktop import install, runtime
    from tui_gateway import server

    named = tmp_path / "profiles" / "named"
    named.mkdir(parents=True)
    monkeypatch.setattr(server, "_profile_home", lambda name: str(named) if name == "named" else None)
    monkeypatch.setattr(runtime, "is_supported_host", lambda: True)
    monkeypatch.setattr(runtime, "install_command", lambda: "sudo apt-get install -y x")
    seen = {}
    done = threading.Event()

    def fake_install(*, ask_password, on_line, timeout_seconds=900.0, claimed=False):
        seen["home"] = str(get_hermes_home())
        done.set()
        return 0

    monkeypatch.setattr(install, "install_packages", fake_install)
    monkeypatch.setattr(server, "_broadcast_global_event", lambda *a, **k: None)
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "display.install", "params": {"profile": "named"}})
    assert resp["result"]["started"], resp
    assert done.wait(5)
    assert seen["home"] == str(named)


@pytest.fixture
def _fresh_lease():
    from tools.bot_desktop import lease
    lease._reset_for_tests()
    yield lease
    lease._reset_for_tests()


def _call(server, method, params):
    return server.handle_request({"jsonrpc": "2.0", "id": 7, "method": method, "params": params})


def test_thumbnail_is_suppressed_while_a_human_holds_the_lease(monkeypatch, _fresh_lease):
    """The Desktop polls thumbnails on a timer; while a human drives the screen that grab would ship
    whatever they are typing to every connected client, so it must not touch the framebuffer at all."""
    from tui_gateway import server
    from tools.bot_desktop import thumbnail

    grabs = []
    monkeypatch.setattr(thumbnail, "thumbnail_data_url", lambda: grabs.append(1) or "data:image/jpeg;base64,SECRET")
    _fresh_lease.acquire("viewer-1")
    result = _call(server, "display.thumbnail", {})["result"]
    assert result["data_url"] is None and result["suppressed"] == "human_has_control"
    assert grabs == [], "the framebuffer was grabbed while a human held the lease"
    _fresh_lease.release("viewer-1")
    assert _call(server, "display.thumbnail", {})["result"]["data_url"].endswith("SECRET")



def _rpc(server, method, params):
    return server.handle_request({"jsonrpc": "2.0", "id": 7, "method": method, "params": params})


def test_status_never_discloses_the_lease_holder(monkeypatch):
    """No snapshot or broadcast carries the holder capability, only its hash."""
    from tools.bot_desktop import lease
    from tui_gateway import server

    lease._reset_for_tests()
    broadcasts = []
    monkeypatch.setattr(server, "_broadcast_global_event", lambda ev, payload=None: broadcasts.append((ev, payload)))
    try:
        holder = "private-viewer-capability"
        _rpc(server, "display.status", {})  # installs the broadcast listener
        lease.acquire(holder)
        status = _rpc(server, "display.status", {})["result"]
        assert status["lease"]["holder"] == lease.HUMAN
        assert holder not in json.dumps(status)
        assert status["lease"]["viewer_hash"] == hashlib.sha256(holder.encode()).hexdigest()[:12]
        lease_events = [p for ev, p in broadcasts if ev == "display.lease"]
        assert lease_events and all(holder not in json.dumps(p) for p in lease_events)
    finally:
        lease._reset_for_tests()


def test_stop_cannot_kill_the_screen_under_a_human_without_force(monkeypatch, _fresh_lease):
    """display.stop released the lease unconditionally before stopping Xvnc: any authenticated caller
    could yank a human mid-login and kill the screen under them."""
    from tui_gateway import server
    from tools.bot_desktop import runtime

    stops = []
    monkeypatch.setattr(runtime, "stop", lambda: stops.append(1) or True)
    _fresh_lease.acquire("viewer-1")
    refused = _call(server, "display.stop", {})
    assert refused["error"]["data"]["code"] == "viewer_mismatch"
    assert _fresh_lease.get().holder == _fresh_lease.HUMAN and stops == []
    forced = _call(server, "display.stop", {"force": True})["result"]
    assert forced["stopped"] is True and stops == [1]
    assert _fresh_lease.get().holder == _fresh_lease.AGENT

    # The refusal must be decided in the SAME transition as the release: a takeover that lands after a
    # separate "is a human holding?" read but before the release would be acknowledged, then revoked.
    # Simulate it by making the release transition itself see a human (a write raced in under the lock).
    real_transition = _fresh_lease._transition

    def transition_after_takeover(profile_key, mutate):
        def mutate_seeing_human(lease_now):
            lease_now.holder, lease_now.viewer_id = _fresh_lease.HUMAN, "late-viewer"
            return mutate(lease_now)
        return real_transition(profile_key, mutate_seeing_human)

    monkeypatch.setattr(_fresh_lease, "_transition", transition_after_takeover)
    stops.clear()
    res = _call(server, "display.stop", {})
    assert "error" in res and res["error"]["data"]["code"] == "viewer_mismatch", res
    assert stops == []


def test_install_sudo_card_ignores_a_client_supplied_session_id(monkeypatch):
    """The sudo card is app-level: it goes to the connection that clicked Install (copy_context
    pins the transport) with an EMPTY session. Honouring params.session_id let a caller route the
    masked password card into ANOTHER window's chat; the wire contract now refuses the key outright
    (4000), and the request the handler sends carries no session either way."""
    from tui_gateway import server
    from tools.bot_desktop import install, runtime

    monkeypatch.setattr(runtime, "is_supported_host", lambda: True)
    monkeypatch.setattr(runtime, "install_command", lambda: "sudo apt-get install -y x")
    monkeypatch.setattr(server, "_broadcast_global_event", lambda *a, **k: None)
    asked = threading.Event()
    blocks = []

    def fake_block(event, sid, payload, timeout=300):
        blocks.append((event, sid))
        asked.set()
        return ""

    def fake_install(*, ask_password, on_line, timeout_seconds=900.0, claimed=False):
        ask_password()
        return -1

    monkeypatch.setattr(server, "_ask", fake_block)
    monkeypatch.setattr(install, "install_packages", fake_install)
    refused = _call(server, "display.install", {"session_id": "victim-session"})
    assert refused["error"]["code"] == 4000 and "session_id" in refused["error"]["message"], refused
    resp = _call(server, "display.install", {})
    assert resp["result"]["started"], resp
    assert asked.wait(5)
    assert blocks == [("display.install.sudo", "")], blocks


def test_thumbnail_grabbed_across_a_takeover_is_suppressed(monkeypatch, _fresh_lease):
    """The human_holds() check happens before the grab; a takeover that lands while the framebuffer
    is being read means the returned frame may already show the human's session. The lease epoch
    must match before and after the grab or the frame is dropped."""
    from tui_gateway import server
    from tools.bot_desktop import thumbnail

    def grab_while_human_takes_over():
        _fresh_lease.acquire("viewer-1")
        return "data:image/jpeg;base64,SECRET"

    monkeypatch.setattr(thumbnail, "thumbnail_data_url", grab_while_human_takes_over)
    result = _call(server, "display.thumbnail", {})["result"]
    assert result["data_url"] is None and result["suppressed"] == "human_has_control", result
