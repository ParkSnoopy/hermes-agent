"""Transport liveness and capability handlers."""

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method


def _caller_transport():
    return current_transport() or _stdio_transport


@method("gateway.capabilities")
def _(rid, params: dict) -> dict:
    """What THIS BUILD enforces (a client withholds unless advertised), sourced from the enforcing
    module, never config: a believed-but-absent capability is worse."""
    from hermes_cli.active_sessions import PER_SESSION_EXCLUSIVE_SUBMIT
    return _ok(rid, {"per_session_exclusive_submit": bool(PER_SESSION_EXCLUSIVE_SUBMIT)})


@method("client.capabilities")
def _(rid, params: dict) -> dict:
    """What the calling client handles. ``server_requests: true`` marks this connection as one that answers
    server→client requests; a WebSocket client that never sends it gets every such request failed fast
    instead of stalling the agent for the deadline (#112548)."""

    from tui_gateway import server_requests
    from tui_gateway.contracts import registry as contracts
    transport = _caller_transport()
    if "server_requests" in params:
        server_requests.advertise(transport, bool(params.get("server_requests")))
    return _ok(rid, {"server_requests": sorted(contracts.SERVER_REQUESTS),
                     "canonical_clarify_batch": True})


@method("ping")
def _(rid, params: dict) -> dict:
    """Cheapest liveness probe, answered on the WS reader thread (works while every agent is mid-turn)
    so the desktop can tell a half-open socket after sleep/wake."""
    return _ok(rid, {"pong": True})


def register(server) -> None:
    bind_module(globals(), server, skip=("_",))
