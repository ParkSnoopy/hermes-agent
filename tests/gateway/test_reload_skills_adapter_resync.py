"""Tests for adapter refresh after `/reload-skills`."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


class TestHandleReloadSkillsCallsRefreshSkillGroup:
    """Gateway-side integration: /reload-skills must call refresh on adapters."""

    def test_orchestrator_calls_refresh_skill_group_on_every_adapter(self):
        """Sync + async refresh_skill_group implementations both get awaited/called.

        The orchestrator iterates ``self.adapters`` and calls
        ``refresh_skill_group`` if it exists. Adapters that don't
        implement it are silently
        skipped without raising.
        """
        import asyncio
        from unittest.mock import patch, MagicMock

        # Import without constructing a real runner — test the method
        # directly against an ``object.__new__`` instance.
        from gateway.run import GatewayRunner
        runner = object.__new__(GatewayRunner)

        sync_refresh = MagicMock(return_value=(5, 0))
        async_called = {"flag": False}

        class AsyncAdapter:
            name = "async-platform"
            async def refresh_skill_group(self):
                async_called["flag"] = True
                return (3, 0)

        class SyncAdapter:
            name = "sync-platform"
            refresh_skill_group = sync_refresh

        class NoOpAdapter:
            name = "other"
            # No refresh_skill_group — must not crash.

        runner.adapters = {
            "async": AsyncAdapter(),
            "sync": SyncAdapter(),
            "other": NoOpAdapter(),
        }

        # Mock reload_skills itself so no disk scan runs.
        fake_result = {"added": [], "removed": [], "total": 7}
        with patch(
            "agent.skill_commands.reload_skills", return_value=fake_result
        ):
            event = MagicMock()
            event.source = MagicMock()
            # _session_key_for_source may be called — make it safe.
            runner._session_key_for_source = lambda src: None
            runner._pending_skills_reload_notes = {}

            asyncio.get_event_loop().run_until_complete(
                runner._handle_reload_skills_command(event)
            )

        assert sync_refresh.called, "sync adapter refresh must be invoked"
        assert async_called["flag"], "async adapter refresh must be awaited"
