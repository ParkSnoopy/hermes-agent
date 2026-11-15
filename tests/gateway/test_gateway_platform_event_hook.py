"""The gateway platform-event hook authorizes and scopes normalized events."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.profile_routing import ProfileRoute
from gateway.run import GatewayRunner
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from tests.gateway.test_authority_busy_plaintext_approval import _human_adapter


EVENT = {
    "platform": "human_fixture",
    "event_type": "reaction",
    "payload": {"chat_id": "123", "message_id": "456", "emojis": ["x"]},
}


@pytest.fixture
def transport(monkeypatch):
    return _human_adapter(monkeypatch)


def source_for(adapter, user="human-42"):
    return adapter.build_source(chat_id="123", user_id=user, chat_type="dm")


def test_registered_plugin_receives_only_authorized_event(transport):
    manager = PluginManager()
    context = PluginContext(PluginManifest(name="event-fixture", source="user"), manager)
    seen = []
    context.register_hook("gateway_platform_event", lambda **event: seen.append(event))
    runner = object.__new__(GatewayRunner)
    transport.set_platform_event_handler(runner._handle_gateway_platform_event)

    with patch("hermes_cli.plugins.get_plugin_manager", return_value=manager):
        asyncio.run(transport._platform_event_handler(EVENT, source_for(transport)))
        asyncio.run(transport._platform_event_handler(EVENT, source_for(transport, "intruder")))

    assert seen == [EVENT]


def test_hook_failure_is_isolated(transport):
    runner = object.__new__(GatewayRunner)
    runner._is_user_authorized = lambda source: True
    with patch("hermes_cli.lifecycle.has_hook", return_value=True), patch(
        "hermes_cli.lifecycle.invoke_hook", side_effect=RuntimeError("plugin boom")
    ) as invoke:
        asyncio.run(runner._handle_gateway_platform_event(EVENT, source_for(transport)))
    invoke.assert_called_once_with("gateway_platform_event", **EVENT)


def test_primary_profile_route_scopes_dispatch_and_rejects_unserved(transport, tmp_path):
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(multiplex_profiles=True, profile_routes=[
        ProfileRoute(name="work-chat", platform="human_fixture", profile="work", chat_id="123")
    ])
    transport.gateway_runner = runner
    work_home = tmp_path / "work"
    captured = []

    @contextmanager
    def scope(home):
        captured.append(("scope", home))
        yield

    async def dispatch(event, source):
        captured.append((event, source.profile))

    runner._handle_gateway_platform_event = dispatch
    runner._primary_profile_name = "default"
    runner._transport_owner = lambda source: (transport, None)
    runner._resolve_profile_home_for_source = lambda source: work_home
    with patch("gateway.run._multiplex_profile_homes", return_value=[("work", work_home)]), \
         patch("gateway.run._profile_runtime_scope", side_effect=scope):
        source = source_for(transport)
        assert source.profile == "work"
        assert source._transport_adapter_ref() is transport
        handler = runner._make_default_profile_platform_event_handler()
        asyncio.run(handler(EVENT, source))

    assert captured == [("scope", work_home), (EVENT, "work")]
    with patch("gateway.run._multiplex_profile_homes", return_value=[]):
        rejected = source_for(transport)
        assert rejected.profile_route_rejected
        asyncio.run(handler(EVENT, rejected))
    assert captured == [("scope", work_home), (EVENT, "work")]


def test_secondary_handler_scopes_auth_and_dispatch(transport, tmp_path):
    runner = object.__new__(GatewayRunner)
    work_home = tmp_path / "work"
    captured = []
    active = []

    @contextmanager
    def scope(home):
        active.append(home)
        try:
            yield
        finally:
            active.pop()

    def authorize(source):
        captured.append(("auth", source.profile, active[-1]))
        return source.user_id == "human-42"

    manager = PluginManager()
    context = PluginContext(PluginManifest(name="scoped-event", source="user"), manager)
    context.register_hook("gateway_platform_event", lambda **event: captured.append(("hook", event, active[-1])))
    runner._is_user_authorized = authorize
    with patch("hermes_cli.profiles.get_profile_dir", return_value=work_home), \
         patch("gateway.run._profile_runtime_scope", side_effect=scope), \
         patch("hermes_cli.plugins.get_plugin_manager", return_value=manager):
        handler = runner._make_profile_platform_event_handler("work")
        asyncio.run(handler(EVENT, source_for(transport)))
        asyncio.run(handler(EVENT, source_for(transport, "intruder")))

    assert captured == [
        ("auth", "work", work_home), ("hook", EVENT, work_home),
        ("auth", "work", work_home),
    ]
