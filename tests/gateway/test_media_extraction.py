"""
Tests for MEDIA tag extraction from tool results.

Verifies that MEDIA tags are only extracted from messages in the CURRENT
turn, not from the full conversation history. This prevents messages from
accumulating and being sent multiple times per reply.
"""

from unittest.mock import MagicMock

import pytest


class TestMediaExtraction:
    """Tests for MEDIA tag extraction from tool results."""

    def test_gateway_auto_append_ignores_media_examples_in_skill_docs(self):
        """Skill/documentation examples must not be appended as real attachments."""
        from gateway.run import _collect_auto_append_media_tags

        messages = [
            {"role": "user", "content": "How should I format gateway media?"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "call_skill", "function": {"name": "skill_view"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_skill",
                "content": """
Recommended pattern:
```text
MEDIA:/absolute/path/to/image.png
```
Second message:
```text
caption
```
""",
            },
            {"role": "assistant", "content": "Use a standalone media message."},
        ]

        tags, voice = _collect_auto_append_media_tags(messages, history_offset=0)
        assert tags == []
        assert voice is False

    def test_collect_history_media_paths_includes_image_generate_json(self):
        """Regression for #46627: the history media-path collector must pick up
        image_generate JSON-payload paths (no MEDIA: tag), not just MEDIA:
        text tags. Otherwise, after a compression boundary the auto-append
        fallback rescans full history, finds the generated path absent from
        the dedup set, and re-emits the same MEDIA tag every turn.
        """
        from gateway.run import _collect_history_media_paths

        history = [
            {"role": "user", "content": "make a cat"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "c", "function": {"name": "image_generate"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "c",
                "content": '{"success": true, "image": "/tmp/gen/cat.png"}',
            },
            # A separate MEDIA: text tag from another tool, to confirm both shapes.
            {
                "role": "tool",
                "tool_call_id": "d",
                "content": "Saved MEDIA:/tmp/voice/note.ogg done",
            },
        ]
        paths = _collect_history_media_paths(history)
        assert "/tmp/gen/cat.png" in paths  # JSON-payload path (the bug)
        assert "/tmp/voice/note.ogg" in paths  # MEDIA: text path (already worked)

    def test_non_streaming_dedup_excludes_current_turn_tool_output(self):
        from gateway.platforms.base import BasePlatformAdapter

        old_path = "/tmp/gen/old.png"
        current_path = "/tmp/gen/current.png"
        transcript = [
            {"role": "user", "content": "make the old image"},
            {"role": "assistant", "content": f"MEDIA:{old_path}"},
            {"role": "user", "content": "make a new image"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "current", "function": {"name": "image_generate"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "current",
                "content": f'{{"success": true, "image": "{current_path}"}}',
            },
            {"role": "assistant", "content": f"MEDIA:{current_path}"},
        ]
        adapter = MagicMock()
        adapter._session_store.peek_session_id.return_value = "session-id"
        adapter._session_store.load_transcript.return_value = transcript

        paths = BasePlatformAdapter._history_media_paths_for_session(
            adapter, "session-key"
        )
        assert paths == {old_path}

    @pytest.mark.parametrize(
        "current_path",
        ["/tmp/tts/current.ogg", "/tmp/tts/already-delivered.ogg"],
    )
    def test_non_streaming_dedup_scopes_tts_paths_to_prior_turns(
        self, current_path
    ):
        from gateway.platforms.base import BasePlatformAdapter

        old_path = "/tmp/tts/already-delivered.ogg"
        transcript = [
            {"role": "user", "content": "say the old message"},
            {"role": "assistant", "content": f"MEDIA:{old_path}"},
            {"role": "user", "content": "say the current message"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "tts", "function": {"name": "text_to_speech"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tts",
                "content": f"[[audio_as_voice]]\\nMEDIA:{current_path}",
            },
            {
                "role": "assistant",
                "content": f"[[audio_as_voice]]\\nMEDIA:{current_path}",
            },
        ]
        adapter = MagicMock()
        adapter._session_store.peek_session_id.return_value = "session-id"
        adapter._session_store.load_transcript.return_value = transcript

        paths = BasePlatformAdapter._history_media_paths_for_session(
            adapter, "session-key"
        )
        assert paths == {old_path}

    def test_image_generate_not_reemitted_after_compression(self):
        """End-to-end of the #46627 fix: collect history paths, then the
        compression-fallback rescan (history_offset stale) must dedup the
        generated image against them — no re-emission."""
        from gateway.run import (
            _collect_auto_append_media_tags,
            _collect_history_media_paths,
        )

        history = [
            {
                "role": "assistant",
                "tool_calls": [{"id": "c", "function": {"name": "image_generate"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "c",
                "content": '{"success": true, "image": "/tmp/gen/dog.png"}',
            },
        ]
        history_paths = _collect_history_media_paths(history)

        # Simulate the post-compression fallback: history_offset is stale
        # (larger than the shrunken message list), so the collector rescans
        # the full list. With the dedup set populated, the already-delivered
        # image must NOT be re-emitted.
        tags, _ = _collect_auto_append_media_tags(
            history, history_offset=9999, history_media_paths=history_paths
        )
        assert tags == [], f"generated image re-emitted after compression: {tags}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
