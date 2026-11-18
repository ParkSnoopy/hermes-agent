"""Idle work accounting and background-task classification."""
import asyncio
import time
import pytest
from gateway.run import GatewayRunner

def test_bg_work_blocks_idle_via_background_tasks(monkeypatch):
    """_scale_to_zero_has_live_background_work() reports True when a tracked
    background task is still live (D3/F7) — the guard that keeps a gateway with
    an in-flight backgrounded subagent/terminal awake."""
    r = GatewayRunner.__new__(GatewayRunner)

    async def _never():
        await asyncio.sleep(0.2)

    loop = asyncio.new_event_loop()
    try:
        t = loop.create_task(_never())
        r._background_tasks = {t}
        # process_registry has nothing active in this fresh process.
        assert r._scale_to_zero_has_live_background_work() is True
        t.cancel()
    finally:
        loop.run_until_complete(asyncio.gather(t, return_exceptions=True))
        loop.close()



@pytest.mark.asyncio
async def test_supervised_watchers_do_not_block_idle():
    r = GatewayRunner.__new__(GatewayRunner)
    r._running = True
    r._background_tasks = set()

    async def _forever():
        await asyncio.sleep(3600)

    # Spawn like production does — through _spawn_supervised.
    for name in ("session_expiry", "reconnect", "idle_exit"):
        r._spawn_supervised(lambda: _forever(), name)
    await asyncio.sleep(0)  # let tasks start
    try:
        assert r._scale_to_zero_has_live_background_work() is False
    finally:
        for t in r._background_tasks:
            t.cancel()
        await asyncio.gather(*r._background_tasks, return_exceptions=True)



@pytest.mark.asyncio
async def test_transient_background_task_still_blocks_idle():
    """A plain (untagged) task in _background_tasks — startup-resume events,
    ad-hoc work — must still count as live background work."""
    r = GatewayRunner.__new__(GatewayRunner)
    r._running = True

    async def _work():
        await asyncio.sleep(3600)

    t = asyncio.create_task(_work())
    r._background_tasks = {t}
    try:
        assert r._scale_to_zero_has_live_background_work() is True
    finally:
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)



@pytest.mark.asyncio
async def test_done_supervised_watcher_is_ignored_either_way():
    r = GatewayRunner.__new__(GatewayRunner)
    r._running = True

    async def _quick():
        return None

    t = asyncio.create_task(_quick())
    await t
    r._background_tasks = {t}
    assert r._scale_to_zero_has_live_background_work() is False



@pytest.mark.asyncio
async def test_loop_heartbeat_task_does_not_block_idle():
    r = GatewayRunner.__new__(GatewayRunner)
    r._running = True
    r._background_tasks = set()
    r._loop_heartbeat_task = None
    r._gateway_started_at = time.time()

    r._start_loop_heartbeat_task()
    await asyncio.sleep(0)  # let the task start
    try:
        assert r._scale_to_zero_has_live_background_work() is False
    finally:
        r._loop_heartbeat_task.cancel()
        await asyncio.gather(r._loop_heartbeat_task, return_exceptions=True)



@pytest.mark.asyncio
async def test_heartbeat_poll_task_does_not_block_idle():
    r = GatewayRunner.__new__(GatewayRunner)
    r._running = True
    r._background_tasks = set()
    r._heartbeat_poll_task = None
    r._heartbeat_watch = {}
    r._running_agents = {}

    r._start_heartbeat_poller()
    await asyncio.sleep(0)  # let the task start
    try:
        assert r._scale_to_zero_has_live_background_work() is False
    finally:
        r._heartbeat_poll_task.cancel()
        await asyncio.gather(r._heartbeat_poll_task, return_exceptions=True)



def _work_count_runner(monkeypatch, *, agents=0, api_runs=0):
    from types import SimpleNamespace

    r = GatewayRunner.__new__(GatewayRunner)
    r._running = True
    r._draining = False
    r._running_agents = {f"a{i}": object() for i in range(agents)}
    r._background_tasks = set()
    r._last_inbound_at = 0.0  # inbound-quiet for hours
    monkeypatch.setattr(
        r, "_scale_to_zero_idle_timeout_seconds", lambda: 300.0, raising=False
    )

    api_adapter = SimpleNamespace(active_agent_work_count=lambda: api_runs)
    from gateway.platforms.base import Platform

    r.adapters = {Platform.API_SERVER: api_adapter}
    monkeypatch.setattr('gateway.run_idle_exit._served_homes', lambda runner: [])
    monkeypatch.setattr('gateway.run_idle_exit._messaging_busy', lambda runner: None)
    monkeypatch.setattr('gateway.run_idle_exit._ledger_busy', lambda runner: None)
    monkeypatch.setattr('gateway.run_idle_exit._profile_start_busy', lambda runner: None)
    monkeypatch.setattr('gateway.run_idle_exit._store_gates_busy', lambda homes: None)
    return r


def _is_idle(runner):
    from gateway.run_idle_exit import busy_reason
    return busy_reason(runner, window=0, count_clients=False) is None





def test_active_api_run_blocks_idle(monkeypatch):
    r = _work_count_runner(monkeypatch, api_runs=1)
    assert _is_idle(r) is False



def test_idle_true_when_all_work_sources_quiet(monkeypatch):
    r = _work_count_runner(monkeypatch)
    assert _is_idle(r) is True





def test_unreadable_api_source_fails_awake(monkeypatch):
    from types import SimpleNamespace

    def _boom():
        raise RuntimeError("adapter wedged")

    r = _work_count_runner(monkeypatch)
    from gateway.platforms.base import Platform

    r.adapters = {Platform.API_SERVER: SimpleNamespace(active_agent_work_count=_boom)}
    assert _is_idle(r) is False



def test_missing_api_adapter_is_not_work(monkeypatch):
    """A missing API adapter is normal, not an unreadable work source."""
    r = _work_count_runner(monkeypatch)
    r.adapters = {}
    assert _is_idle(r) is True

