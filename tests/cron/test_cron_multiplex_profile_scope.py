"""Profile isolation for multiplex cron ticks and delivery."""
import asyncio
import threading
from unittest.mock import patch

import pytest



def test_standalone_fallback_pool_keeps_profile_scope(tmp_path, monkeypatch):
    from agent.secret_scope import (
        get_secret,
        set_multiplex_active,
        set_secret_scope,
    )
    from hermes_constants import get_hermes_home, set_hermes_home_override
    import cron.scheduler as sched
    import gateway.outbound as smt

    default_home = tmp_path / "default"
    sec_home = tmp_path / "profiles" / "ops"
    for home in (default_home, sec_home):
        (home / "cron").mkdir(parents=True)
        (home / "config.yaml").write_text("platforms:\n  telegram:\n    enabled: true\n")
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "DEFAULT-TOKEN")
    set_multiplex_active(True)

    seen = {}

    async def fake_send(platform, pconfig, chat_id, message, **kwargs):
        seen["home"] = str(get_hermes_home())
        seen["token"] = get_secret("TELEGRAM_BOT_TOKEN", None)
        return {"success": True, "message_id": "1"}

    job = {"id": "j1", "name": "probe", "deliver": "telegram:12345", "schedule": {"kind": "cron"}}

    async def _inside_running_loop():
        # Emulate the multiplex ticker's per-profile scope on the caller.
        set_hermes_home_override(str(sec_home))
        set_secret_scope({"TELEGRAM_BOT_TOKEN": "OPS-TOKEN"})
        return sched._deliver_result(job, "hello", adapters={}, loop=None)

    try:
        with patch.object(smt, "_send_to_platform", fake_send):
            err = asyncio.run(_inside_running_loop())
    finally:
        set_multiplex_active(False)

    assert err is None, err
    assert seen["home"] == str(sec_home.resolve())
    assert seen["token"] == "OPS-TOKEN"


def test_multiplex_ticker_profile_gate_skips_rejected_profile(tmp_path):
    from cron.scheduler_provider import InProcessCronScheduler
    from hermes_constants import get_hermes_home

    own_gateway = tmp_path / "own-gateway"
    orphan = tmp_path / "orphan"
    for home in (own_gateway, orphan):
        (home / "cron").mkdir(parents=True)

    stop = threading.Event()
    ticked: list[str] = []

    def _tick(*args, **kwargs):
        ticked.append(str(get_hermes_home()))
        if len(ticked) >= 3:
            stop.set()
        return 0

    provider = InProcessCronScheduler()
    with patch("cron.scheduler.tick", side_effect=_tick):
        thread = threading.Thread(
            target=provider.start,
            args=(stop,),
            kwargs={
                "interval": 0,
                "profile_homes": [("own-gateway", own_gateway), ("orphan", orphan)],
                "profile_gate": lambda name, home: name != "own-gateway",
            },
            daemon=True,
        )
        thread.start()
        thread.join(timeout=5)
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert set(ticked) == {str(orphan)}
    # The gated profile gets no tick-loop success marker either: its own
    # gateway owns that status surface.
    assert not (own_gateway / "cron" / "ticker_last_success").exists()
    assert (orphan / "cron" / "ticker_last_success").exists()



def test_a_routed_profile_fire_runs_under_multiplex_semantics_for_exactly_its_scope(tmp_path, monkeypatch):
    """A sibling profile's job is fired from a process that is not a multiplexer.
    The tick only MARKS the fire as routed; multiplex semantics switch on where run_one_job
    installs the profile's secret scope and off with it — so the routed .env stays out of the
    shared os.environ, a scope miss never falls back to the launch profile's credentials, and
    the parent process env is byte-identical after the tick (#107692)."""
    import os

    from cron import scheduler
    from agent import secret_scope
    from cron.scheduler_provider import _profile_cron_scope, routed_profile_fire
    from hermes_cli.env_loader import load_hermes_dotenv

    launch, routed = tmp_path / "launch", tmp_path / "launch" / "profiles" / "ops"
    for home in (launch, routed):
        (home / "cron").mkdir(parents=True)
    (launch / ".env").write_text("XAI_API_KEY=launch-key\nDISCORD_BOT_TOKEN=launch-bot\n", encoding="utf-8")
    (routed / ".env").write_text("XAI_API_KEY=routed-key\nROUTED_ONLY=routed-only\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv("XAI_API_KEY", "launch-key")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "launch-bot")
    monkeypatch.delenv("ROUTED_ONLY", raising=False)
    secret_scope.set_multiplex_active(False)
    environ_before = dict(os.environ)

    with _profile_cron_scope(routed):
        # Before the scope exists — where run_one_job's restart-safe handoff runs — the marker is
        # set, the semantics are not.
        assert routed_profile_fire() is True
        assert secret_scope.is_multiplex_active() is False

        tokens = scheduler._install_fire_secret_scope()
        try:
            assert secret_scope.is_multiplex_active() is True
            # The job's per-run dotenv reload, exactly as cron/scheduler does it: hydrate-only.
            assert load_hermes_dotenv(hermes_home=routed, load_external_secrets=False) == []
            assert secret_scope.get_secret("XAI_API_KEY") == "routed-key"
            assert secret_scope.get_secret("DISCORD_BOT_TOKEN") is None  # never the launch bot
        finally:
            scheduler._reset_fire_secret_scope(tokens)
        assert secret_scope.is_multiplex_active() is False  # off with the scope, not later

    assert routed_profile_fire() is False
    assert dict(os.environ) == environ_before


@pytest.mark.parametrize(
    ("routed_env_line", "expected"),
    [
        ("", "managed-key"),  # managed-only credential: resolvable, not absent
        ("ORG_API_KEY=user-key\n", "managed-key"),  # managed-vs-user collision: policy wins
    ],
    ids=["managed-only", "managed-beats-user"],
)
def test_routed_fire_scope_carries_managed_env_authority(tmp_path, monkeypatch, routed_env_line, expected):
    """Under multiplex semantics get_secret never falls back to os.environ, so the routed fire's
    scope itself must carry the administrator-managed .env with the precedence _apply_managed_env
    gives it in the launch process: a managed-only key is present and a managed value beats the
    routed profile's own (#111187 review)."""
    from cron import scheduler
    from agent import secret_scope
    from cron.scheduler_provider import _profile_cron_scope
    from hermes_cli import managed_scope

    launch, routed = tmp_path / "launch", tmp_path / "launch" / "profiles" / "ops"
    managed = tmp_path / "managed"
    for home in (launch, routed, managed):
        (home / "cron").mkdir(parents=True)
    (routed / ".env").write_text(routed_env_line, encoding="utf-8")
    (managed / ".env").write_text("ORG_API_KEY=managed-key\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    monkeypatch.setenv("ORG_API_KEY", "managed-key")  # what _apply_managed_env left in the launch env
    managed_scope.invalidate_managed_cache()
    secret_scope.set_multiplex_active(False)

    with _profile_cron_scope(routed):
        tokens = scheduler._install_fire_secret_scope()
        try:
            assert secret_scope.is_multiplex_active() is True
            assert secret_scope.get_secret("ORG_API_KEY") == expected
        finally:
            scheduler._reset_fire_secret_scope(tokens)
