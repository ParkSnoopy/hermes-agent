"""The model migration remains available without external-agent import routes."""

import argparse

import pytest

from hermes_cli.migrate import cmd_migrate, cmd_migrate_xai
from hermes_cli.subcommands.migrate import build_migrate_parser


def _parser():
    parser = argparse.ArgumentParser(prog="hermes")
    build_migrate_parser(parser.add_subparsers(dest="command"))
    return parser


def test_xai_model_migration_remains_available():
    args = _parser().parse_args(["migrate", "xai", "--apply", "--no-backup"])
    assert args.func is cmd_migrate_xai
    assert args.apply and args.no_backup


@pytest.mark.parametrize("agent", ["openclaw", "claude-code", "codex", "cursor", "grok-bot", "openbot"])
def test_external_agent_migration_is_not_a_route(agent):
    with pytest.raises(SystemExit) as error:
        _parser().parse_args(["migrate", agent])
    assert error.value.code == 2


def test_sessions_rejects_external_import_and_keeps_list():
    from hermes_cli.subcommands.sessions import build_sessions_parser

    parser = argparse.ArgumentParser(prog="hermes")
    build_sessions_parser(parser.add_subparsers(dest="command"), cmd_sessions=lambda args, **kwargs: None)
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["sessions", "import", "--from", "claude"])
    assert error.value.code == 2
    assert parser.parse_args(["sessions", "list"]).sessions_action == "list"


def test_retired_import_commands_have_no_install_hint():
    from hermes_cli.left_core_migration import moved_command_hint

    assert moved_command_hint("hermes", "import-agent") == ""
    assert moved_command_hint("hermes", "claw") == ""


def test_bare_migrate_shows_usage_without_starting_import(capsys):
    args = _parser().parse_args(["migrate"])
    assert cmd_migrate(args) == 2
    assert "hermes migrate xai" in capsys.readouterr().err
