"""The shared media dispatcher forwards the ``is_voice`` keyword."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_media_dispatch_delivers_audio_with_voice_intent(tmp_path) -> None:
    """The base dispatcher forwards the voice flag and records delivery."""
    from unittest.mock import AsyncMock

    from gateway.platforms.api_server import APIServerAdapter
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import MessageEvent
    from gateway.session import Platform, SessionSource

    adapter = object.__new__(APIServerAdapter)
    adapter.platform = Platform.API_SERVER
    adapter.send_voice = AsyncMock(return_value=SendResult(success=True, message_id="post-1"))
    adapter._notify_media_delivery_failure = AsyncMock()
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"ID3")
    event = MessageEvent(text="", source=SessionSource(platform=Platform.API_SERVER, chat_id="chan-1"))
    results = []

    await adapter._deliver_media_attachments(
        event, [(str(clip), False)], [], force_document_attachments=False, human_delay=0,
        metadata={}, record_delivery=results.append)

    assert [r.success for r in results] == [True]
    adapter._notify_media_delivery_failure.assert_not_awaited()
    adapter.send_voice.assert_awaited_once()
    assert adapter.send_voice.await_args.kwargs["audio_path"] == str(clip)
    assert adapter.send_voice.await_args.kwargs["is_voice"] is False
