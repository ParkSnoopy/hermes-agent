"""Private native gateway session WebSocket; no public HTTP surface."""

import asyncio
from dataclasses import dataclass
import importlib
import logging
import socket
import sys
from typing import Any
from urllib.parse import urlsplit

from gateway.session_contract import CANONICAL_GATEWAY_PROTOCOL

logger = logging.getLogger(__name__)
_SESSION_SOCKET_MODULE = "tui_gateway.ws"
_ACTIVE_RUNNER = None


@dataclass
class GatewayAPIHandle:
    api_origin: str
    server: Any
    task: asyncio.Task
    app: Any
    socket: socket.socket


def preload_session_socket():
    if _SESSION_SOCKET_MODULE in sys.modules:
        return None
    task = asyncio.create_task(asyncio.to_thread(importlib.import_module, _SESSION_SOCKET_MODULE),
                               name="gateway-ws-preload")
    task.add_done_callback(lambda done: done.cancelled() or done.exception())
    return task


async def await_session_socket_preload(task, *, timeout=30.0):
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except TimeoutError:
        logger.warning("Session WebSocket handler preload timed out")
    except Exception:
        logger.warning("Session WebSocket handler preload failed", exc_info=True)


async def session_socket_handler():
    return (await asyncio.to_thread(importlib.import_module, _SESSION_SOCKET_MODULE)).handle_ws


def _native_ticket(ws):
    protocols = [part.strip() for part in ws.headers.get('sec-websocket-protocol', '').split(',') if part.strip()]
    prefix = 'hermes-gateway-ticket.'
    tickets = [part[len(prefix):] for part in protocols if part.startswith(prefix)]
    if protocols.count(CANONICAL_GATEWAY_PROTOCOL) != 1 or len(tickets) != 1 or not tickets[0]:
        return None
    return tickets[0]


def _native_peer(ws):
    if not ws.client or ws.client.host not in {'127.0.0.1', '::1'} or ws.headers.get('origin'):
        return False
    try:
        parsed = urlsplit('http://' + ws.headers.get('host', ''))
        port = parsed.port
    except ValueError:
        return False
    return (parsed.hostname in {'127.0.0.1', '::1', 'localhost'} and parsed.username is None
            and parsed.password is None and not parsed.path and not parsed.query and not parsed.fragment
            and (port is None or port > 0))


class GatewayRuntimeAPI:
    """Admit only same-host native grants issued by the private control socket."""

    def __init__(self, runner):
        self.runner = runner

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            await send({'type': 'http.response.start', 'status': 404, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})
            return
        if scope['type'] != 'websocket':
            return
        if scope.get('path') != '/api/ws':
            await send({'type': 'websocket.close', 'code': 4404})
            return
        from starlette.websockets import WebSocket
        from gateway.drain_control import restart_wait_open
        from gateway.run_idle_exit import attached_client
        from gateway.session_authorities import authority_for_profile_id, owner_scope

        scope['hermes.gateway_socket_peer'] = scope.get('client')
        ws = WebSocket(scope, receive, send)
        descriptor = getattr(self.runner, 'session_runtime_descriptor', None)
        if descriptor is None or descriptor['state'] not in {'ready', 'draining'}:
            await ws.close(code=1013)
            return
        ticket = _native_ticket(ws)
        if not ticket or not _native_peer(ws):
            await ws.close(code=4403)
            return
        operator = True
        try:
            grant = self.runner.session_ticket_store.redeem(ticket, profile_id=None, purpose='interactive')
        except PermissionError:
            operator = False
            try:
                grant = self.runner.session_ticket_store.redeem(ticket, profile_id=None, purpose='worker-adoption')
            except PermissionError:
                await ws.close(code=4403)
                return
        allow_draining = not operator
        admitted_in_wait = restart_wait_open(self.runner) and not grant.get('refuse_restart_wait')

        def refused():
            return ((descriptor['state'] != 'ready' or self.runner._draining) and not allow_draining
                    and not admitted_in_wait)

        if refused():
            await ws.close(code=1013)
            return
        authority = authority_for_profile_id(self.runner, grant['profile_id'])
        if authority is None:
            await ws.close(code=4403)
            return
        scope['hermes.session_authority'] = authority
        original_receive = ws._receive

        async def receive_admitted():
            message = await original_receive()
            if ((descriptor['state'] != 'ready' or self.runner._draining) and not allow_draining
                    and not (admitted_in_wait and restart_wait_open(self.runner))):
                return {'type': 'websocket.disconnect', 'code': 1013}
            return message

        ws._receive = receive_admitted
        with attached_client(self.runner):
            handle_ws = await session_socket_handler()
            if refused():
                await ws.close(code=1013)
                return
            with owner_scope(authority):
                await handle_ws(ws, auth_identity={'user_id': grant['subject'], 'provider': 'local',
                    'profile_id': grant['profile_id'], 'instance_id': grant['instance_id'],
                    'capabilities': grant['capabilities'], 'native_bootstrap': True,
                    'profile_scope': grant.get('scope', 'profile')},
                    subprotocol=CANONICAL_GATEWAY_PROTOCOL, operator=operator)


async def start_gateway_api(runner):
    global _ACTIVE_RUNNER
    import uvicorn

    if _ACTIVE_RUNNER is not None:
        raise RuntimeError('gateway API already started')
    app = GatewayRuntimeAPI(runner)
    config = uvicorn.Config(app, host='127.0.0.1', port=0, log_level='warning', log_config=None,
                            proxy_headers=False, ws_ping_interval=None, ws_ping_timeout=None,
                            ws_per_message_deflate=False, timeout_graceful_shutdown=5, lifespan='off',
                            ws_max_size=384 * 1024 * 1024)
    server = uvicorn.Server(config)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _ACTIVE_RUNNER = runner
    try:
        listener.bind(('127.0.0.1', 0))
        listener.setblocking(False)
        config.load()
        server.lifespan = config.lifespan_class(config)
        await server.startup(sockets=[listener])
        if not server.started or server.should_exit:
            raise RuntimeError('gateway API startup failed')
    except BaseException:
        listener.close()
        _ACTIVE_RUNNER = None
        raise

    async def serve():
        global _ACTIVE_RUNNER
        try:
            await server.main_loop()
        finally:
            try:
                await server.shutdown(sockets=[listener])
            finally:
                listener.close()
                _ACTIVE_RUNNER = None

    task = asyncio.create_task(serve(), name='gateway-api')
    return GatewayAPIHandle(f'http://127.0.0.1:{listener.getsockname()[1]}', server, task, app, listener)


async def stop_gateway_api(handle):
    handle.server.should_exit = True
    await asyncio.shield(asyncio.gather(handle.task, return_exceptions=True))