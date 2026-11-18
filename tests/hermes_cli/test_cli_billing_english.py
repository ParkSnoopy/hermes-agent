"""English billing and subscription presentation."""

from __future__ import annotations


import pytest

from cli import HermesCLI
from hermes_cli import cli_billing_mixin as bm


@pytest.fixture
def cli():
    obj = HermesCLI.__new__(HermesCLI)
    obj._app = None
    return obj










def test_logged_out_block_uses_english_copy(cli, capsys):
    class _State:
        error = ""

    cli._print_logged_out(_State(), 'Could not load subscription', "/subscription")
    out = capsys.readouterr().out
    assert 'Not logged into Nous Portal.' in out
    assert "Run `hermes portal` to log in, then /subscription." in out


def test_topup_menu_english_choices():
    assert bm._topup_menu_choices()[0][1] == 'Add funds'
    assert [c[0] for c in bm._topup_menu_choices()] == ["buy", "auto", "limit", "portal", "cancel"]
