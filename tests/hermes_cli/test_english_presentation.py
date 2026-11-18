"""English labels retain the classic CLI's presentation behavior."""
from types import SimpleNamespace

from hermes_cli.cli_modern_layout import hint_pairs
from hermes_cli.cli_process_dock import process_activity
from hermes_cli.commands import category_label, resolve_command


def test_command_labels_use_english_descriptions():
    assert category_label("Tools & Skills") == "Tools & Skills"
    branch = resolve_command("branch")
    assert branch is not None
    assert branch.describe() == (
        "Branch the current session (new thread on Discord/Telegram/Slack/Matrix; --here stays here)"
    )


def test_process_exit_verdicts():
    row = {"status": "failed", "exit_code": 3, "since_exit": 2}
    assert process_activity(row) == "exit 3 · 2s ago"
    row["status"] = "killed"
    assert process_activity(row) == "killed · 2s ago"


def test_busy_input_hints():
    cli = SimpleNamespace(_agent_running=True, busy_input_mode="steer")
    assert hint_pairs(cli) == [("enter", "steer"), ("tab", "queue"), ("ctrl+c", "stop")]
    cli.busy_input_mode = "queue"
    assert hint_pairs(cli) == [("enter", "queue"), ("ctrl+c", "stop")]
