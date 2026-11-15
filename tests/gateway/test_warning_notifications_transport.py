"""Generic plugin warning delivery and profile-scoped effective warning policy."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.status_output import StatusOutputMixin
from gateway.config import Platform, PlatformConfig
from gateway.platforms.event import MessageEvent, MessageType

from gateway.run import GatewayRunner, _load_gateway_config, _profile_runtime_scope
from gateway.run_turn_runner import TurnRunner
from gateway.session import SessionSource
from gateway.turn_context import TurnContext





@pytest.mark.parametrize("thread_id", [None, "thread"])
@pytest.mark.parametrize("enabled", [False, True])
def test_warning_policy_reaches_plugin_transport(tmp_path, monkeypatch, thread_id, enabled):
    from gateway import run
    from tests.gateway.test_platform_reconnect import StubAdapter, register_test_platform

    (tmp_path / "config.yaml").write_text(
        f"display: {{suppress_warning_notifications: {str(not enabled).lower()}}}")
    monkeypatch.setattr(run, "_hermes_home", tmp_path)
    platform = register_test_platform(monkeypatch)
    adapter = StubAdapter(platform=platform)
    adapter.send = AsyncMock(return_value=SimpleNamespace(success=True, message_id="1"))
    source = SessionSource(platform=platform, chat_id="chat", chat_type="dm", user_id="user", thread_id=thread_id)
    metadata = {"thread_id": thread_id} if thread_id else {}
    gateway = object.__new__(GatewayRunner)
    gateway.config = None
    gateway._delivery_adapter_for = lambda source: adapter
    gateway._thread_metadata_for_source = lambda source: metadata
    ctx = TurnContext(source=source, user_config=_load_gateway_config(), _run_still_current=lambda: True,
                      _status_adapter=adapter, _status_chat_id="chat", _status_thread_metadata=metadata)
    turn = TurnRunner(gateway, ctx)
    monkeypatch.setattr(run, "safe_schedule_threadsafe", lambda coro, *a, **k: asyncio.run(coro))
    agent = StatusOutputMixin()
    agent.suppress_status_output = True
    agent.status_callback = turn._status_callback_sync
    for _ in range(2):
        agent._emit_warning("⚠ Context is over the limit; compression blocked")
    assert adapter.send.await_count == (2 if enabled else 0)
    result = asyncio.run(adapter.send("chat", "⚠ The requested task failed", metadata=metadata))
    assert result.success
    assert adapter.send.await_count == (2 if enabled else 0) + 1
    assert adapter.send.await_args.kwargs["metadata"] == metadata


def test_profile_config_isolated_for_callbacks_and_direct_warnings(tmp_path, monkeypatch):
    from gateway import run
    from gateway.warning_notifications import warning_notifications_enabled

    homes = [tmp_path / name for name in ("a", "b")]
    for home, value in zip(homes, ("true", "false")):
        home.mkdir()
        (home / "config.yaml").write_text(f"display: {{suppress_warning_notifications: {value}}}")
    # The ambient default must never override the context-bound owner.
    monkeypatch.setattr(run, "_hermes_home", homes[1])
    source = SessionSource(platform=Platform.SLACK, chat_id="D1", chat_type="dm", user_id="U1")
    for home, expected in ((homes[0], False), (homes[1], True), (homes[0], False)):
        with _profile_runtime_scope(home, prepared_secret_scope={}):
            config = _load_gateway_config()
            assert warning_notifications_enabled(source.platform, config) is expected
            from tests.gateway.test_warning_notifications import RecordingAdapter
            adapter = RecordingAdapter()
            adapter.send = AsyncMock()
            gateway = object.__new__(GatewayRunner)
            gateway._delivery_adapter_for = lambda source: adapter
            asyncio.run(gateway._hmwa_hygiene_notify(source, {}, "Compression failed", "failure"))
            assert adapter.send.await_count == int(expected)
