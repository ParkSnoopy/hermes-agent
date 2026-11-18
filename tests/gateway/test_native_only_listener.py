"""In-process admission tests; these do not start a server or a gateway."""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import gateway.run_api as api
import gateway.session_authorities as authorities
import gateway.run_idle_exit as idle
from gateway import owner_identity, session_identity


def _scope(protocol='hermes-gateway-v1, hermes-gateway-ticket.good', host='127.0.0.1:123',
           peer='127.0.0.1', origin=None):
    headers = [(b'host', host.encode()), (b'sec-websocket-protocol', protocol.encode())]
    if origin is not None:
        headers.append((b'origin', origin.encode()))
    return {'type': 'websocket', 'path': '/api/ws', 'headers': headers,
            'client': (peer, 123), 'query_string': b'', 'scheme': 'ws',
            'server': ('127.0.0.1', 123), 'subprotocols': []}


def test_native_admission_and_rejections(monkeypatch):
    grant = {'subject': 'uid:1', 'profile_id': 'profile', 'instance_id': 'instance', 'capabilities': []}
    store = Mock()
    store.redeem.return_value = grant
    runner = SimpleNamespace(session_runtime_descriptor={'state': 'ready'}, _draining=False,
                             session_ticket_store=store)
    monkeypatch.setattr(authorities, 'authority_for_profile_id', lambda runner, profile: 'authority')
    monkeypatch.setattr(authorities, 'owner_scope', lambda authority: nullcontext())
    monkeypatch.setattr(idle, 'attached_client', lambda runner: nullcontext())
    seen = []

    async def handler(ws, **kwargs):
        seen.append(kwargs)

    async def load():
        return handler

    monkeypatch.setattr(api, 'session_socket_handler', load)

    async def invoke(scope):
        sent = []

        async def receive():
            return {'type': 'websocket.connect'}

        async def send(message):
            sent.append(message)

        await api.GatewayRuntimeAPI(runner)(scope, receive, send)
        return sent

    asyncio.run(invoke(_scope()))
    assert seen[0]['subprotocol'] == 'hermes-gateway-v1'
    assert seen[0]['auth_identity']['user_id'] == 'uid:1'
    assert store.redeem.call_count == 1
    for scope in (_scope(origin='https://example.com'), _scope(peer='192.0.2.1'),
                  _scope(host='evil.example'), _scope(host='evil@localhost:123'),
                  _scope(protocol='hermes-gateway-ticket.good'),
                  _scope(protocol='hermes-gateway-v1, hermes-gateway-ticket.a, hermes-gateway-ticket.b')):
        assert asyncio.run(invoke(scope)) == [{'type': 'websocket.close', 'code': 4403, 'reason': ''}]
    assert store.redeem.call_count == 1
    store.redeem.side_effect = PermissionError('expired')
    assert asyncio.run(invoke(_scope())) == [{'type': 'websocket.close', 'code': 4403, 'reason': ''}]
    assert store.redeem.call_count == 3


def test_http_surface_is_not_mounted():
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {'type': 'http.request'}

    asyncio.run(api.GatewayRuntimeAPI(None)({'type': 'http', 'path': '/api/status'}, receive, send))
    assert sent[0]['status'] == 404


def test_owner_and_internal_identities_do_not_need_dashboard(monkeypatch):
    monkeypatch.setattr(owner_identity, 'host_owner_subject', lambda: 'uid:1')
    native = {'provider': 'local', 'user_id': 'uid:1', 'profile_id': 'p',
              'instance_id': 'i', 'capabilities': []}
    assert owner_identity.principal_subject(native) == 'uid:1'
    assert owner_identity.same_owner('basic:old-user', 'uid:1')
    assert not owner_identity.same_owner('basic:old-user', 'uid:2')
    assert not owner_identity.same_owner('unknown:old-user', 'uid:1')
    assert session_identity.is_authenticated_identity(native)
    assert not session_identity.is_authenticated_identity(
        {'provider': 'server-internal', 'user_id': 'server-internal'})