"""Gateway command help rendering tests."""

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource


def _make_event(text: str, platform: Platform) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=platform,
            chat_id="chat-1",
            user_id="user-1",
            user_name="tester",
            chat_type="dm",
        ),
    )


def _make_runner():
    from gateway.run import GatewayRunner

    return object.__new__(GatewayRunner)


@pytest.mark.asyncio
async def test_help_preserves_skill_command_names(monkeypatch):
    """Help preserves registered skill names without transport-specific rewriting."""
    monkeypatch.setattr(
        "agent.skill_commands.get_skill_commands",
        lambda: {
            "/Linear": {"description": "Open Linear"},
            "/Custom-Thing": {"description": "Run a custom thing"},
        },
    )

    result = await _make_runner()._gateway_plain_command_handlers()["help"](
        _make_event("/help", Platform.API_SERVER)
    )

    assert "`/Linear`" in result
    assert "`/Custom-Thing`" in result


@pytest.mark.asyncio
async def test_commands_preserves_skill_command_names(monkeypatch):
    """Paginated commands preserve the registered skill spelling."""
    monkeypatch.setattr(
        "agent.skill_commands.get_skill_commands",
        lambda: {"/Linear": {"description": "Open Linear"}},
    )

    result = await _make_runner()._gateway_plain_command_handlers()["commands"](
        _make_event("/commands 999", Platform.API_SERVER)
    )

    assert "`/Linear`" in result


