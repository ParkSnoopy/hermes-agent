"""Compatibility of registered standalone transport sender signatures."""

from enum import Enum

import pytest

from gateway.config import PlatformConfig
from gateway.outbound import _send_to_platform
from gateway.platform_registry import PlatformEntry, platform_registry


class FakePlatform(Enum):
    TRANSPORT = "test_transport"


@pytest.fixture
def standalone(monkeypatch):
    from gateway import outbound_senders

    entry = PlatformEntry(
        name=FakePlatform.TRANSPORT.value, label="Test transport",
        adapter_factory=lambda config: None, check_fn=lambda: True,
    )
    scope = platform_registry.current_scope_key()
    previous = platform_registry.snapshot_registration(entry.name, scope=scope)
    platform_registry.register(entry, scope=scope)
    monkeypatch.setattr(outbound_senders, "_live_adapter", lambda platform: (None, None))
    monkeypatch.setattr("gateway.outbound._live_adapter", lambda platform: (None, None))
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    yield entry
    platform_registry.restore_registration(entry.name, (entry, None), previous, scope=scope)


@pytest.mark.asyncio
async def test_a_sender_written_to_the_earlier_contract_still_delivers(standalone):
    calls = []

    async def documented(pconfig, chat_id, message, *, thread_id=None, media_files=None, force_document=False):
        calls.append(message)
        return {"success": True, "message_id": "user-plugin"}

    standalone.standalone_sender_fn = documented
    pconfig = PlatformConfig(enabled=True)
    result = await _send_to_platform(FakePlatform.TRANSPORT, pconfig, "123", "daily brief", notify=True)
    assert result == {"success": True, "message_id": "user-plugin"}

    async def open_kwargs(pconfig, chat_id, message, **kwargs):
        calls.append(kwargs.get("notify"))
        return {"success": True, "message_id": "kw"}

    standalone.standalone_sender_fn = open_kwargs
    result = await _send_to_platform(FakePlatform.TRANSPORT, pconfig, "123", "daily brief", notify=True)
    assert result["success"] is True
    assert calls == ["daily brief", True]


@pytest.mark.asyncio
async def test_an_unhashable_callable_sender_still_delivers(standalone):
    class Sender:
        def __init__(self):
            self.calls = []

        def __eq__(self, other):
            return self is other

        async def __call__(self, pconfig, chat_id, message, *, thread_id=None, media_files=None,
                           force_document=False, notify=None):
            self.calls.append((message, notify))
            return {"success": True, "message_id": "obj"}

    sender = Sender()
    standalone.standalone_sender_fn = sender
    result = await _send_to_platform(
        FakePlatform.TRANSPORT, PlatformConfig(enabled=True), "123", "daily brief", notify=True,
    )
    assert result == {"success": True, "message_id": "obj"}
    assert sender.calls == [("daily brief", True)]