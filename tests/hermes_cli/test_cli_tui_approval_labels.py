"""English TUI approval labels and modal hints."""
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock


from cli import HermesCLI




def _cli_with_approval():
    cli = HermesCLI.__new__(HermesCLI)
    cli._approval_lock = threading.Lock()
    cli._sudo_state = cli._secret_state = cli._slash_confirm_state = cli._connection_state = None
    cli._clarify_state = None
    cli._clarify_freetext = False
    cli._command_running = False
    cli._approval_deadline = time.monotonic() + 30
    cli._approval_state = {
        "command": "rm -rf build",
        "description": "recursive delete",
        "choices": ["once", "deny"],
        "selected": 0,
        "show_full": False,
        "response_queue": queue.Queue(),
    }
    cli._app = SimpleNamespace(invalidate=MagicMock())
    return cli




def test_unknown_choice_id_renders_verbatim():
    from hermes_cli.cli_tui_mixin import _approval_choice_label

    assert _approval_choice_label("custom-id") == "custom-id"
    assert _approval_choice_label("deny") == 'Deny'


def test_approval_panel_and_hint_are_english(monkeypatch):
    import shutil
    monkeypatch.setattr("cli.shutil.get_terminal_size", lambda *_a, **_k: shutil.os.terminal_size((100, 40)))
    cli = _cli_with_approval()
    rendered = "".join(text for _style, text in cli._get_approval_display_fragments())
    assert "Allow once" in rendered and "Deny" in rendered
    hint = "".join(text for _style, text in cli._tui_hint_text())
    assert hint.rstrip().endswith("s)")
