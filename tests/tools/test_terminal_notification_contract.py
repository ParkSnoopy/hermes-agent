"""Terminal notification dispatch uses the advertised notify argument only."""

import json
from unittest.mock import patch

import pytest

from tools.terminal_tool import _handle_terminal


@pytest.mark.parametrize("old", [{"notify_on_complete": True}, {"watch_patterns": ["ready"]}])
def test_retired_notification_arguments_are_rejected(old):
    with patch("tools.terminal_tool.terminal_tool") as execute:
        result = json.loads(_handle_terminal({"command": "build", "background": True, **old}))
    assert "notify" in result["error"]
    execute.assert_not_called()


@pytest.mark.parametrize(
    ("notify", "completion", "patterns"),
    [(True, True, None), (False, False, None), (["ready"], False, ["ready"])],
)
def test_notification_dispatch_uses_canonical_argument(notify, completion, patterns):
    with patch("tools.terminal_tool.terminal_tool", return_value="ok") as execute:
        assert _handle_terminal({"command": "build", "background": True, "notify": notify}) == "ok"
    assert execute.call_args.kwargs["notify_on_complete"] is completion
    assert execute.call_args.kwargs["watch_patterns"] == patterns


def test_foreground_notification_is_rejected():
    with patch("tools.terminal_tool.terminal_tool") as execute:
        result = json.loads(_handle_terminal({"command": "build", "notify": True}))
    assert "background" in result["error"]
    execute.assert_not_called()
