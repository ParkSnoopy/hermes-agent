"""Smoke tests for the final batch of subcommand builders extracted from main().

These groups either imported their handler from a sibling module inside the
parser block (moa, fallback, migrate, bundles, checkpoints, curator, pets,
journey, secrets, egress) or carried a closure handler that only closed over
its own parser (worktree, browser, computer-use, sessions, completion). The
closures moved verbatim into the builder; sessions/completion take the handler
by injection.
"""

from __future__ import annotations

import argparse


from hermes_cli.subcommands.screen import build_screen_parser
from hermes_cli.subcommands._shared import add_json_flag
from hermes_cli.subcommands.worktree import build_worktree_parser


def _tree():
    parser = argparse.ArgumentParser(prog="hermes")
    return parser, parser.add_subparsers(dest="command")






def test_worktree_aliases_normalize_to_list(monkeypatch):
    parser, sub = _tree()
    build_worktree_parser(sub)
    seen = {}
    monkeypatch.setattr("hermes_cli.worktree_cmd.cmd_worktree", lambda a: seen.setdefault("action", a.worktree_action))
    ns = parser.parse_args(["worktree", "audit"])
    ns.func(ns)
    assert seen["action"] == "list"








def test_screen_no_action_prints_help(capsys):
    parser, sub = _tree()
    build_screen_parser(sub, add_json_flag)
    ns = parser.parse_args(["screen"])
    ns.func(ns)
    assert "install" in capsys.readouterr().out
