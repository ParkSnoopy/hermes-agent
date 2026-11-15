"""Generic reconnect escalation and runtime-status classification."""
import asyncio
import time
from unittest.mock import MagicMock, patch
import pytest
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner, _reconnect_needs_attention

def _set_attention_after(value) -> None:
    """Write ``agent.reconnect_attention_after`` into the test's isolated HERMES_HOME config."""
    import hermes_yaml as yaml
    from hermes_constants import get_hermes_home
    (get_hermes_home() / "config.yaml").write_text(
        yaml.safe_dump({"agent": {"reconnect_attention_after": value}}), encoding="utf-8")



class TestReconnectNeedsAttention:
    def test_fresh_entry_is_not_flagged_and_gets_stamped(self):
        # In-flight upgrade path: entries queued before queued_at existed are
        # treated as newly queued, not instantly escalated.
        info = {"attempts": 3}
        now = time.monotonic()
        assert _reconnect_needs_attention(info, now) is False
        assert info["queued_at"] == now

    def test_below_threshold_is_not_flagged(self):
        now = time.monotonic()
        info = {"queued_at": now - 60}
        assert _reconnect_needs_attention(info, now) is False

    def test_past_threshold_is_flagged(self):
        _set_attention_after(10)
        now = time.monotonic()
        info = {"queued_at": now - 11}
        assert _reconnect_needs_attention(info, now) is True

    def test_zero_threshold_disables_escalation(self):
        _set_attention_after(0)
        info = {"queued_at": time.monotonic() - 999999}
        assert _reconnect_needs_attention(info, time.monotonic()) is False



def _make_runner():
    """Minimal GatewayRunner via object.__new__ (same pattern as
    test_platform_reconnect.py)."""
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.API_SERVER: PlatformConfig(enabled=True, token="test")}
    )
    runner._running = True
    runner._shutdown_event = asyncio.Event()
    runner._exit_reason = None
    runner._exit_with_failure = False
    runner._exit_cleanly = False
    runner._failed_platforms = {}
    runner.adapters = {}
    runner.delivery_router = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner.session_store = MagicMock()
    return runner



class TestWatcherAttentionEscalation:
    @pytest.mark.asyncio
    async def test_watcher_flags_long_queued_platform_and_keeps_retrying(self, monkeypatch):
        runner = _make_runner()
        status_writes = []
        monkeypatch.setattr(
            runner,
            "_update_platform_runtime_status",
            lambda platform, **kw: status_writes.append((platform, kw)),
        )

        threshold = 10
        _set_attention_after(threshold)
        runner._failed_platforms[Platform.API_SERVER] = {
            "config": PlatformConfig(enabled=True, token="test"),
            "attempts": 40,
            # Not yet due for a retry — escalation must not depend on the
            # backoff schedule lining up.
            "next_retry": time.monotonic() + 300,
            "queued_at": time.monotonic() - threshold - 10,
        }

        real_sleep = asyncio.sleep
        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                runner._running = False
            await real_sleep(0)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await runner._platform_reconnect_watcher()

        attention = [kw for _p, kw in status_writes if kw.get("needs_attention")]
        assert attention, f"expected a needs_attention status write, got {status_writes!r}"
        assert attention[0]["platform_state"] == "retrying"
        assert attention[0].get("retrying_since")
        # Platform must STILL be queued — escalation is a signal, never a
        # circuit breaker.
        assert Platform.API_SERVER in runner._failed_platforms
        assert runner._failed_platforms[Platform.API_SERVER].get("attention_flagged") is True

    @pytest.mark.asyncio
    async def test_watcher_flags_only_once(self, monkeypatch):
        runner = _make_runner()
        status_writes = []
        monkeypatch.setattr(
            runner,
            "_update_platform_runtime_status",
            lambda platform, **kw: status_writes.append((platform, kw)),
        )

        threshold = 10
        _set_attention_after(threshold)
        runner._failed_platforms[Platform.API_SERVER] = {
            "config": PlatformConfig(enabled=True, token="test"),
            "attempts": 40,
            "next_retry": time.monotonic() + 300,
            "queued_at": time.monotonic() - threshold - 10,
        }

        real_sleep = asyncio.sleep
        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                runner._running = False
            await real_sleep(0)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await runner._platform_reconnect_watcher()

        attention = [kw for _p, kw in status_writes if kw.get("needs_attention")]
        assert len(attention) == 1, (
            f"needs_attention must be written once per episode, got {len(attention)}"
        )



class TestRuntimeStatusAttentionFields:
    def test_needs_attention_and_retrying_since_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from gateway import status as status_module

        status_module.write_runtime_status(
            platform="api_server",
            platform_state="retrying",
            error_code="api_server_connect_error",
            error_message="boom",
            needs_attention=True,
            retrying_since="2026-08-11T00:00:00+00:00",
        )
        payload = status_module.read_runtime_status()
        platform = payload["platforms"]["api_server"]
        assert platform["needs_attention"] is True
        assert platform["retrying_since"] == "2026-08-11T00:00:00+00:00"

        # Reconnect clears both.
        status_module.write_runtime_status(
            platform="api_server",
            platform_state="connected",
            error_code=None,
            error_message=None,
            needs_attention=False,
            retrying_since=None,
        )
        payload = status_module.read_runtime_status()
        platform = payload["platforms"]["api_server"]
        assert platform["needs_attention"] is False
        assert platform["retrying_since"] is None

