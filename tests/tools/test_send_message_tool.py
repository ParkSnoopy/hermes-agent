"""Generic outbound delivery, policy and error regressions."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform
from gateway.outbound import send_outbound


def _config():
    cfg = SimpleNamespace(enabled=True, token=None, extra={})
    return SimpleNamespace(platforms={Platform.API_SERVER: cfg}, get_home_channel=lambda _: None), cfg


def test_media_tag_outside_allowed_roots_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "1")
    monkeypatch.setenv("HERMES_MEDIA_TRUST_RECENT_FILES", "0")
    config, cfg = _config()
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF secret")
    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("model_tools._run_async", side_effect=asyncio.run), \
         patch("gateway.outbound._send_to_platform", new=AsyncMock(return_value={"success": True})) as send, \
         patch("gateway.mirror.mirror_to_session", return_value=True):
        result = json.loads(send_outbound({"target": "api_server:12345", "message": f"hello\nMEDIA:{secret}"}))
    assert result["success"] is False
    assert result["partial_success"] is True
    assert result["media_dropped"] == [{"path": str(secret), "reason": "denied by the delivery policy"}]
    send.assert_awaited_once_with(Platform.API_SERVER, cfg, "12345", "hello", thread_id=None,
                                  media_files=[], force_document=False)


def test_missing_media_is_reported_and_cli_exits_nonzero(tmp_path, monkeypatch):
    from hermes_cli.send_cmd import _emit_result

    monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "0")
    config, cfg = _config()
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF report")
    missing = tmp_path / "missing.pdf"
    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("model_tools._run_async", side_effect=asyncio.run), \
         patch("gateway.outbound._send_to_platform", new=AsyncMock(return_value={"success": True})) as send, \
         patch("gateway.mirror.mirror_to_session", return_value=True):
        raw = send_outbound({"target": "api_server:12345", "message": f"report\nMEDIA:{report}\nMEDIA:{missing}"})
    result = json.loads(raw)
    assert result["success"] is False
    assert result["partial_success"] is True
    assert result["media_dropped"] == [{"path": str(missing), "reason": "not found on this host"}]
    assert result["error"]
    send.assert_awaited_once_with(Platform.API_SERVER, cfg, "12345", "report", thread_id=None,
                                  media_files=[(str(report.resolve()), False)], force_document=False)
    assert _emit_result(raw, json_mode=True, quiet=True) != 0


def test_top_level_send_failure_redacts_query_token():
    config, _ = _config()
    leaked = "very-secret-query-token-123456"

    def fail(coro):
        coro.close()
        raise RuntimeError(f"transport error: https://api.example.com/send?access_token={leaked}")

    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("model_tools._run_async", side_effect=fail):
        result = json.loads(send_outbound({"target": "api_server:12345", "message": "hello"}))
    assert leaked not in result["error"]
    assert "access_token=***" in result["error"]


@pytest.mark.asyncio
async def test_standalone_sender_failure_is_reported(monkeypatch):
    from gateway.outbound import _send_via_adapter
    from gateway.platform_registry import PlatformEntry, platform_registry

    async def boom(pconfig, chat_id, message, **kwargs):
        raise ValueError("boom!")

    entry = PlatformEntry(name="fakeplatform", label="Fake", adapter_factory=lambda cfg: None,
                          check_fn=lambda: True, standalone_sender_fn=boom)
    platform_registry.register(entry)
    monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: None)
    try:
        result = await _send_via_adapter(SimpleNamespace(value="fakeplatform"), SimpleNamespace(extra={}), "chat-1", "hi")
    finally:
        platform_registry.unregister("fakeplatform")
    assert set(result) == {"error"}
    assert "boom!" in result["error"]


@pytest.mark.asyncio
async def test_fresh_process_discovers_standalone_sender(monkeypatch):
    from gateway.outbound import _send_via_adapter
    from gateway.platform_registry import PlatformEntry, platform_registry

    sent = []

    async def send(pconfig, chat_id, message, **kwargs):
        sent.append((chat_id, message))
        return {"success": True, "platform": "fakeplatform", "chat_id": chat_id}

    entry = PlatformEntry(name="fakeplatform", label="Fake", adapter_factory=lambda cfg: None,
                          check_fn=lambda: True, standalone_sender_fn=send)
    monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: None)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda *a, **k: platform_registry.register(entry))
    try:
        result = await _send_via_adapter(SimpleNamespace(value="fakeplatform"), SimpleNamespace(extra={}), "chat-1", "hi")
    finally:
        platform_registry.unregister("fakeplatform")
    assert result["success"] is True and sent == [("chat-1", "hi")]


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_kind", ["caption", "text_direct", "live"])
async def test_notify_reaches_all_delivery_routes(monkeypatch, policy_kind):
    from gateway.outbound import _send_to_platform
    from gateway.platform_registry import PlatformEntry, platform_registry
    from gateway.platforms.send_targets import StandaloneSendPolicy

    calls = []

    async def send(pconfig, chat_id, message, **kwargs):
        calls.append(kwargs)
        return {"success": True}

    class Adapter:
        async def send(self, *, chat_id, content, metadata):
            calls.append(metadata)
            return SimpleNamespace(success=True, message_id="sent")

    policy = (StandaloneSendPolicy(media=True, captionable=True) if policy_kind == "caption"
              else StandaloneSendPolicy(text_direct=True) if policy_kind == "text_direct" else None)
    entry = PlatformEntry(name="notify-test", label="Notify test", adapter_factory=lambda cfg: None,
                          check_fn=lambda: True, standalone_sender_fn=send, standalone_send=policy)
    platform_registry.register(entry)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    monkeypatch.setattr("gateway.outbound._live_adapter", lambda _: (SimpleNamespace(), Adapter())
                        if policy_kind == "live" else (None, None))
    try:
        result = await _send_to_platform(Platform("notify-test"), SimpleNamespace(extra={}), "123", "hello",
                                         media_files=[("report.pdf", False)] if policy_kind == "caption" else [],
                                         notify=False)
    finally:
        platform_registry.unregister(entry.name)
    assert result["success"] is True
    assert calls[0]["notify"] is False


@pytest.mark.asyncio
async def test_long_plugin_message_is_chunked(monkeypatch):
    from gateway.outbound import _send_to_platform
    from gateway.platform_registry import PlatformEntry, platform_registry
    from gateway.platforms.send_targets import StandaloneSendPolicy

    chunks = []

    async def send(pconfig, chat_id, message, **kwargs):
        chunks.append(message)
        return {"success": True}

    entry = PlatformEntry(name="chunk-test", label="Chunk test", adapter_factory=lambda cfg: None,
                          check_fn=lambda: True, max_message_length=100,
                          standalone_sender_fn=send, standalone_send=StandaloneSendPolicy(text_direct=True))
    platform_registry.register(entry)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    try:
        result = await _send_to_platform(Platform("chunk-test"), SimpleNamespace(extra={}), "123", "word " * 300)
    finally:
        platform_registry.unregister(entry.name)
    assert result["success"] is True
    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_unconfigured_error_reports_resolved_paths(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig
    from gateway.outbound import _resolve_platform_config

    home = tmp_path / "profile"
    home.mkdir()
    (home / ".env").write_text("FIRECRAWL_API_KEY=x\n", encoding="utf-8")
    (home / "config.yaml").write_text("platforms:\n  api_server:\n    enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    _, _, _, error = _resolve_platform_config("api_server", GatewayConfig())
    assert error is not None
    assert str(home / ".env") in error
    assert str(home / "config.yaml") in error
    assert "~/.hermes" not in error


def test_unconfigured_error_never_exposes_secret_source_values(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig
    from gateway.outbound import _resolve_platform_config

    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        "secrets:\n  bitwarden:\n    enabled: false\n    session_token: SECRET-VALUE\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    _, _, _, error = _resolve_platform_config("api_server", GatewayConfig())
    assert error is not None
    assert "bitwarden" in error
    assert "SECRET-VALUE" not in error