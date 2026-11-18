"""HUD mode reaches the model as a per-turn note, not as a platform hint.

The floating HUD sits over whatever the user is really working in, so a request
typed there is usually about that other app. The note helps identify that app.

The same desktop session can be driven from the app window on one turn and the
HUD on the next, so this cannot live in the system prompt — that has to stay
byte-stable for the life of a conversation. It rides the model-bound message
instead, beside the reaction notes.
"""

import threading
import types

import pytest

from agent.prompt_builder import hud_surface_note
from tui_gateway import server
from tui_gateway.session_notifications import _hud_surface_note

HUD_TOOLS = {"read_window_below"}


def _session(*, tools=HUD_TOOLS, **extra):
    return {
        "agent": types.SimpleNamespace(valid_tool_names=set(tools)),
        "session_key": "session-key",
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "transport": None,
        "attached_images": [],
        **extra,
    }


class TestNoteContents:
    """Every tool the note names has to be one this agent actually has."""

    def test_no_note_at_all_without_the_tool_it_rests_on(self):
        assert hud_surface_note({"browser_exec"}) == ""

    def test_identifies_the_app_with_the_direct_tool(self):
        note = hud_surface_note(HUD_TOOLS)
        assert "read_window_below" in note
        assert "tool_call bridge" not in note

    def test_no_tools_at_all(self):
        assert hud_surface_note(None) == ""

    def test_deferred_tools_count_and_are_routed_through_the_bridge(self):
        note = hud_surface_note({"tool_call"}, HUD_TOOLS)
        assert "read_window_below identifies that app" in note
        assert "Call read_window_below through the tool_call bridge" in note
        direct = hud_surface_note(HUD_TOOLS | {"tool_call"}, HUD_TOOLS)
        assert "tool_call bridge" not in direct


class TestTurnRouting:
    def test_hud_turn_gets_the_note(self):
        assert _hud_surface_note(_session(client_surface="hud")) == hud_surface_note(HUD_TOOLS)

    def test_hud_turn_uses_the_sessions_deferred_tools(self, monkeypatch):
        from agent import tool_executor

        session = _session(tools={"tool_call"}, client_surface="hud")

        def scoped_names(agent):
            assert agent is session["agent"]
            return HUD_TOOLS

        monkeypatch.setattr(tool_executor, "_tool_search_scoped_names", scoped_names)
        note = _hud_surface_note(session)
        assert "Call read_window_below through the tool_call bridge" in note

    def test_app_window_turn_gets_nothing(self):
        assert _hud_surface_note(_session(client_surface="")) == ""

    def test_session_that_never_reported_a_surface_gets_nothing(self):
        """Every other client (TUI, dashboard, messaging) omits the field."""
        assert _hud_surface_note(_session()) == ""

    def test_survives_an_agent_with_no_toolset_at_all(self):
        session = _session(client_surface="hud")
        session["agent"] = types.SimpleNamespace()
        assert _hud_surface_note(session) == ""


class TestPrepending:
    """Notes ride the model input; the persisted prompt stays what was typed."""

    def test_plain_text_turn(self):
        assert server._prepend_note("go to x", "[Note: hi]") == "[Note: hi]\n\ngo to x"

    def test_multimodal_turn_keeps_its_parts(self):
        parts = [{"type": "text", "text": "go to x"}, {"type": "image_url", "image_url": {}}]
        assert server._prepend_note(parts, "[Note: hi]") == [
            {"type": "text", "text": "[Note: hi]"},
            *parts,
        ]

    def test_nothing_to_say_leaves_the_message_untouched(self):
        parts = [{"type": "text", "text": "go to x"}]
        assert server._prepend_note("go to x", "") == "go to x"
        assert server._prepend_note(parts, "") is parts


class TestSurfaceRecording:
    """``prompt.submit`` stamps the window each message was typed into."""

    @pytest.fixture
    def busy_session(self):
        # A running session takes the busy path, which returns before any of
        # the agent/DB machinery — enough to observe what submit recorded.
        session = _session(running=True)
        server._sessions["sid"] = session
        yield session
        server._sessions.pop("sid", None)

    def _submit(self, **params):
        return server._methods["prompt.submit"](
            "r1", {"session_id": "sid", "text": "what is this?", "queued": True, **params}
        )

    def test_hud_submit_is_recorded(self, busy_session):
        self._submit(surface="hud")
        assert busy_session["client_surface"] == "hud"

    def test_the_next_app_window_submit_clears_it(self, busy_session):
        """A stale 'hud' would tell the model the user is still floating."""
        self._submit(surface="hud")
        self._submit()
        assert busy_session["client_surface"] == ""

    def test_an_unknown_surface_is_not_hud(self, busy_session):
        self._submit(surface="pet-overlay")
        assert busy_session["client_surface"] == ""
