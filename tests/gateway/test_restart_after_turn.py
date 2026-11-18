"""Unit tests for in-band restart after-turn deferral helpers (#77184)."""

import math

from gateway.restart import (
    DEFAULT_GATEWAY_RESTART_AFTER_TURN_TIMEOUT,
    parse_restart_after_turn_timeout,
    resolve_restart_exit_wait_budget,
    resolve_systemd_timeout_stop_sec,
)
from gateway.run import GatewayRunner


def test_parse_restart_after_turn_timeout_defaults_and_clamps():
    assert parse_restart_after_turn_timeout("") == DEFAULT_GATEWAY_RESTART_AFTER_TURN_TIMEOUT
    assert parse_restart_after_turn_timeout(None) == DEFAULT_GATEWAY_RESTART_AFTER_TURN_TIMEOUT
    assert parse_restart_after_turn_timeout("bogus") == DEFAULT_GATEWAY_RESTART_AFTER_TURN_TIMEOUT
    assert parse_restart_after_turn_timeout(0) == 0.0
    assert parse_restart_after_turn_timeout("-5") == 0.0
    assert parse_restart_after_turn_timeout("120") == 120.0


def test_restart_exit_wait_budget_outlasts_deferral_plus_stop_envelope():
    for chat, after_turn, cron in ((0, 0, 0), (0, 1800, 30), (2, 3, 80), (80, 3, 2), (180, 21600, 0)):
        # The observer must never give up before the supervisor's own stop deadline would.
        assert resolve_restart_exit_wait_budget(chat, after_turn, cron) > after_turn + resolve_systemd_timeout_stop_sec(chat, cron)
    # A non-finite drain waits indefinitely instead of crashing the integer envelope.
    assert resolve_restart_exit_wait_budget(float("inf"), 0, 0) == math.inf
    assert resolve_restart_exit_wait_budget(0, 0, float("inf")) == math.inf


def test_cli_restart_wait_covers_api_drain(tmp_path, monkeypatch):
    import hermes_cli.gateway as gateway_cli

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for key in ("HERMES_RESTART_DRAIN_TIMEOUT", "HERMES_RESTART_AFTER_TURN_TIMEOUT", "HERMES_CRON_DRAIN_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    config = tmp_path / "config.yaml"
    config.write_text("agent:\n  restart_drain_timeout: 2\n  restart_after_turn_timeout: 3\n")
    assert gateway_cli._get_restart_exit_wait_budget() > 3 + resolve_systemd_timeout_stop_sec(2)


def test_cli_restart_wait_uses_configured_api_drain(tmp_path, monkeypatch):
    import hermes_cli.gateway as gateway_cli

    monkeypatch.delenv("HERMES_API_DRAIN_TIMEOUT", raising=False)
    monkeypatch.setattr(gateway_cli, "read_raw_config", lambda: {"agent": {"api_drain_timeout": 120}})
    monkeypatch.setattr(gateway_cli, "_get_restart_drain_timeout", lambda: 0)
    assert gateway_cli._get_api_drain_timeout() == 120
    assert gateway_cli._get_restart_exit_wait_budget() > resolve_systemd_timeout_stop_sec(0, 120)
    monkeypatch.setenv("HERMES_API_DRAIN_TIMEOUT", "0")
    assert gateway_cli._get_api_drain_timeout() == 0


def test_load_restart_after_turn_timeout_preserves_zero(tmp_path, monkeypatch):
    """Config/env ``0`` must disable after-turn wait, not fall back to default."""
    import gateway.run as gateway_run

    monkeypatch.delenv("HERMES_RESTART_AFTER_TURN_TIMEOUT", raising=False)
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    (tmp_path / "config.yaml").write_text(
        "agent:\n  restart_after_turn_timeout: 0\n",
        encoding="utf-8",
    )
    assert GatewayRunner._load_restart_after_turn_timeout() == 0.0

    monkeypatch.setenv("HERMES_RESTART_AFTER_TURN_TIMEOUT", "0")
    assert GatewayRunner._load_restart_after_turn_timeout() == 0.0
