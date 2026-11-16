"""English CLI help, status labels and error copy."""

from __future__ import annotations


from hermes_cli.commands import CommandDef
from hermes_cli import cli_info_mixin
from hermes_cli.cli_chat_error_copy import agent_init_failure_message, chat_error_response
from hermes_cli.cli_unknown_command import unknown_command_lines




def test_describe_uses_english_field():
    cmd = CommandDef("no-such-command-zz", "Does a thing", "Info")
    assert cmd.describe() == "Does a thing"




def test_tool_progress_label_for_each_mode_and_empty_for_unknown():
    for mode in ("off", "new", "all", "verbose"):
        line = cli_info_mixin._tool_progress_label(mode)
        assert {'off': 'Tool progress: OFF', 'new': 'Tool progress: NEW', 'all': 'Tool progress: ALL', 'verbose': 'Tool progress: VERBOSE'}[mode] in line
        assert {'off': 'silent mode, just the final response.', 'new': 'show each new tool (skip repeats).', 'all': 'show every tool call.', 'verbose': 'full args, results, and think blocks.'}[mode] in line
    assert cli_info_mixin._tool_progress_label("nope") == ""


def test_reload_mcp_choices_carry_stable_ids_and_labels():
    choices = cli_info_mixin._reload_mcp_choices()
    assert [c[0] for c in choices] == ["once", "always", "cancel"]
    assert choices[0][1] == 'Approve Once'


def test_help_section_titles_preserve_known_and_unknown_categories():
    assert cli_info_mixin._help_section_title("Tools & Skills") == 'Tools & Skills'
    assert cli_info_mixin._help_section_title("Custom Plugin Category") == "Custom Plugin Category"


def test_chat_error_copy_uses_english_lead_and_details():
    text = chat_error_response("upstream said no", provider="openrouter", model="m", failure_reason="rate_limit")
    assert text == ('{lead}\nDetails: {details}').format(lead=('Rate limited by {provider}; wait a minute or /model to switch.').format(provider="openrouter", ), details="upstream said no")
    assert agent_init_failure_message(RuntimeError("boom")) == ("Hermes couldn't start the model connection: {error}. Your message was not sent. Run `hermes doctor` to check the setup, or /model to pick a different provider.").format(error="boom")


def test_unknown_command_lines_show_lead_and_pointer():
    lead, pointer = unknown_command_lines("/modle", {"/model", "/help"})
    hint = " " + ('Did you mean {suggestion}?').format(suggestion="/model")
    assert lead == ('Unknown command {command} — nothing was sent.{hint}').format(command="/modle", hint=hint)
    assert pointer == 'Type /help for the full list.'
