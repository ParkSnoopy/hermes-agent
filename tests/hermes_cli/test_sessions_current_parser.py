"""Session management exposes only current commands."""

import argparse

import pytest

from hermes_cli.subcommands.sessions import build_sessions_parser


def test_sessions_parser_rejects_retired_adoption_and_keeps_list():
    parser = argparse.ArgumentParser()
    calls = []
    build_sessions_parser(
        parser.add_subparsers(dest="command"),
        cmd_sessions=lambda args, **kwargs: calls.append((args.sessions_action, kwargs)),
    )

    with pytest.raises(SystemExit):
        parser.parse_args(["sessions", "adopt"])
    args = parser.parse_args(["sessions", "list"])
    args.func(args)
    assert calls[0][0] == "list"
    assert calls[0][1]["sessions_parser"] is not None
