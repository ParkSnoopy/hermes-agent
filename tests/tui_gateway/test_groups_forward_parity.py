"""Local Group Chat refusal and owner transport frame handling."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

import tui_gateway.server as srv
from tui_gateway import methods_groups, owner_forward

MEMBERS = [{"member_id": "default", "profile": "default", "handle": "hermes"},
           {"member_id": "ops", "profile": "ops", "handle": "ops"}]


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / ".hermes"
    (path / "profiles" / "ops").mkdir(parents=True)
    (path / "profiles" / "ops" / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("HERMES_HOME", str(path))
    methods_groups.stop_hosted_room_service(timeout=1.0)
    methods_groups.start_hosted_room_service()
    yield path
    methods_groups.stop_hosted_room_service(timeout=1.0)


def test_discard_on_a_room_this_backend_drives_is_refused_locally(home):
    srv._methods["groups.create"](1, {"room_id": "room-desk", "name": "Desk", "members": MEMBERS})

    reply = srv._methods["groups.discard"](2, {
        "room_id": "room-desk", "member_id": "ops", "task_id": "task-1", "execution_generation": 1})

    assert reply["error"]["code"] == 4127, reply


def test_a_download_larger_than_the_default_frame_crosses_the_owner_socket(tmp_path, monkeypatch):
    """A real loopback WebSocket owner answers ``groups.attachment.download`` with a file above
    websockets' 1 MiB default frame cap; ``forward_rpc`` must accept the whole answer."""
    import asyncio
    import json
    import threading

    from websockets.asyncio.server import serve

    from hermes_cli import gateway_client

    data = base64.b64encode(bytes(range(256)) * (3 * 2 ** 20 // 256)).decode()
    ready, stop = threading.Event(), None
    port = []

    async def answer(ws):
        async for raw in ws:
            request = json.loads(raw)
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"data": data}}))

    async def run():
        nonlocal stop
        stop = asyncio.get_running_loop().create_future()
        async with serve(answer, "127.0.0.1", 0, subprotocols=[gateway_client.GATEWAY_WS_PROTOCOL]) as server:
            port.append(server.sockets[0].getsockname()[1])
            ready.set()
            await stop

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_until_complete, args=(run(),), daemon=True)
    thread.start()
    assert ready.wait(10)
    monkeypatch.setattr(gateway_client, "_session_ticket", lambda *_a, **_k: "ticket")
    endpoint = SimpleNamespace(api_origin=f"http://127.0.0.1:{port[0]}")
    try:
        result = owner_forward.forward_rpc(tmp_path, endpoint, "groups.attachment.download",
                                           {"room_id": "room-desk", "attachment_id": "att-1"})
    finally:
        loop.call_soon_threadsafe(stop.set_result, None)
        thread.join(10)
    assert len(data) > 2 ** 20
    assert result["data"] == data
