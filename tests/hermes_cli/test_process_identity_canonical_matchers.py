"""Process-identity contract: the kill predicate and the profile liveness probe
defer to the canonical matchers instead of argv substrings (root AGENTS.md process-identity rule).
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from hermes_cli.process_identity import _hermes_holder_subcommand


LOOPBACK = "--host 127.0.0.1 --port 0"

# Substring scanners get the trap rows wrong: "serve" appears inside a flag or script name.
CMDLINES = [
    ("python -m hermes_cli.main serve " + LOOPBACK, "serve"),
    ("python -m hermes_cli.main dashboard", "dashboard"),
    ("/venv/bin/hermes serve --host=127.0.0.1 --port=0", "serve"),
    ("/venv/bin/hermes serve --host 100.106.105.2 --port 9119", "serve"),
    ("hermes dashboard", "dashboard"),
    ("hermes --profile ops serve " + LOOPBACK, "serve"),
    ("hermes -m serve kanban --preserve-cache " + LOOPBACK, "kanban"),
    ("python -m hermes_cli.main kanban --preserve-cache " + LOOPBACK, "kanban"),
    ("hermes --reasoning high dashboard " + LOOPBACK, "dashboard"),
    ("hermes gateway run --replace", "gateway"),
    ("hermes chat --model serve", "chat"),
    ("python observer.py serve " + LOOPBACK, None),
]


@pytest.mark.parametrize("cmdline,subcommand", CMDLINES)
def test_canonical_holder_matcher_requires_entrypoint_and_subcommand(cmdline, subcommand):
    assert _hermes_holder_subcommand(cmdline) == subcommand


def test_profile_liveness_is_the_shared_ladder(tmp_path, monkeypatch):
    """``_check_gateway_running`` is ``resolve_gateway_liveness`` scoped to the profile dir, with the
    PID rung reading (never cleaning) THAT profile's ``gateway.pid``."""
    import gateway.status as gw_status
    from hermes_cli.profiles import _check_gateway_running

    seen: dict = {}

    def fake_resolve(**kwargs):
        seen.update(kwargs)
        return gw_status.GatewayLiveness(running=True, pid=1, source="pid")

    monkeypatch.setattr(gw_status, "resolve_gateway_liveness", fake_resolve)
    calls: list = []
    monkeypatch.setattr(gw_status, "get_running_pid",
                        lambda path, cleanup_stale=True: calls.append((path, cleanup_stale)))
    assert _check_gateway_running(tmp_path) is True
    assert seen["profile_dir"] == tmp_path
    seen["pid_probe"](tmp_path / "gateway.pid")
    assert calls == [(tmp_path / "gateway.pid", False)]


# ``hermes serve`` is a substring of ``hermes server``: a terminal multiplexer started as
# ``herdr --session hermes server`` was SIGTERMed by ``hermes update`` and its unit restarted (#121156).
DECOY = "tool --name hermes server 30"
BACKENDS = [
    "/opt/hermes/venv/bin/python -m hermes_cli.main serve --port 0",
    "/usr/bin/python3 /opt/hermes/hermes_cli/main.py dashboard --no-open",
]


def test_dashboard_scan_selects_entrypoint_plus_subcommand_tokens_never_substrings(monkeypatch):
    from hermes_cli import dashboard_procs
    from hermes_cli import process_identity

    output = "\n".join([f"4242 {DECOY}", *(f"{5000 + i} {cmd}" for i, cmd in enumerate(BACKENDS))])
    monkeypatch.setattr(dashboard_procs.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=output))
    monkeypatch.setattr(process_identity, "ledger_entries", list)
    assert dashboard_procs._scan_dashboard_processes() == [(5000, BACKENDS[0]), (5001, BACKENDS[1])]


def test_dashboard_runtime_parse_refuses_the_decoy_and_reads_a_real_backend():
    from hermes_cli.main_dashboard import _parse_dashboard_runtime

    assert _parse_dashboard_runtime(DECOY) is None
    assert _parse_dashboard_runtime(BACKENDS[0]) == ("serve", "127.0.0.1", 0)
    assert _parse_dashboard_runtime("'/opt/a b/venv/bin/hermes' dashboard --port 9200") == ("dashboard", "127.0.0.1", 9200)
