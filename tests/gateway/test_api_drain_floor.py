"""The API drain floor is independent of the chat-turn drain budget."""

import asyncio
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.restart import (API_DRAIN_CLEANUP_RESERVE_S, DEFAULT_GATEWAY_API_DRAIN_TIMEOUT,
                             parse_api_drain_timeout, resolve_api_drain_budget, resolve_systemd_timeout_stop_sec)
from tests.gateway.restart_test_helpers import make_restart_runner


@pytest.mark.asyncio
async def test_zero_chat_budget_waits_for_api_work():
    runner, _adapter = make_restart_runner()
    live = [1]
    runner.adapters = {Platform.API_SERVER: SimpleNamespace(active_agent_work_count=lambda: live[0])}

    async def finish():
        await asyncio.sleep(0.12)
        live[0] = 0

    task = asyncio.create_task(finish())
    _, timed_out = await runner._drain_active_agents(0.0, 2.0)
    await task
    assert not timed_out


@pytest.mark.asyncio
async def test_api_floor_does_not_extend_chat_only_work():
    runner, _adapter = make_restart_runner()
    runner._running_agents = {"sess-1": object()}
    before = asyncio.get_running_loop().time()
    _, timed_out = await runner._drain_active_agents(0.0, 30.0)
    assert timed_out
    assert asyncio.get_running_loop().time() - before < 1.0


def test_api_budget_clamps_to_watchdog_and_never_shortens_chat_budget():
    assert resolve_api_drain_budget(0, 30, watchdog_delay=60, elapsed=1) == 30
    assert resolve_api_drain_budget(0, 300, watchdog_delay=60, elapsed=5) == 60 - 5 - API_DRAIN_CLEANUP_RESERVE_S
    assert resolve_api_drain_budget(120, 30, watchdog_delay=180) == 120
    assert resolve_api_drain_budget(0, 30, watchdog_delay=60, elapsed=59) == 0
    assert resolve_api_drain_budget(0, 0, watchdog_delay=60) == 0
    assert resolve_api_drain_budget(None, "30", watchdog_delay=60, elapsed=None) == 30


def test_api_timeout_parser_preserves_zero_and_default():
    assert parse_api_drain_timeout(None) == DEFAULT_GATEWAY_API_DRAIN_TIMEOUT == 30
    assert parse_api_drain_timeout('0') == 0
    assert parse_api_drain_timeout('45') == 45
    assert parse_api_drain_timeout('invalid') == 30
    assert parse_api_drain_timeout(-1) == 0


def test_systemd_budget_covers_api_floor_and_longer_chat_drain():
    assert resolve_systemd_timeout_stop_sec(0, 60) == 100
    assert resolve_systemd_timeout_stop_sec(0, 0) == 60
    assert resolve_systemd_timeout_stop_sec(60, 30) == 90
    assert resolve_systemd_timeout_stop_sec(180, 30) == 210
    assert resolve_systemd_timeout_stop_sec("soon", None) == 60