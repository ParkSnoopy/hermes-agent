"""Regression tests: plugin-family credential reads honor the profile secret scope.

Class-closure follow-up to the profile secret-scope cluster (#76462). Memory,
image_gen, and browser plugins, plus a handful of tier-3 tool helpers, read
credentials straight from ``os.environ``. Under a multiplexed gateway the
process environment may hold ANOTHER profile's key (or none), so every
credential read must route through ``agent.secret_scope.get_secret`` and honor
its verdict — a scoped miss under multiplexing returns the default and must
NOT borrow from ``os.environ``.

One representative test pair (scoped-wins / scoped-miss-no-borrow) per plugin
family, plus the behavioral site:

* google_meet caption collection must not inherit or forward retired speech
  credentials, whether they exist in the process environment or profile scope.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from agent.secret_scope import (
    reset_secret_scope,
    set_multiplex_active,
    set_secret_scope,
)


@pytest.fixture
def multiplex_scope():
    """Install a secret scope with multiplexing ON; restore state after."""

    def _install(scope: dict[str, str]):
        set_multiplex_active(True)
        token = set_secret_scope(scope)
        return token

    tokens = []

    def install(scope: dict[str, str]):
        tokens.append(_install(scope))

    yield install

    for token in tokens:
        reset_secret_scope(token)
    set_multiplex_active(False)


# ---------------------------------------------------------------------------
# Family A — memory plugins
# ---------------------------------------------------------------------------

class TestMemoryFamily:
    def test_retaindb_scoped_key_wins(self, multiplex_scope, monkeypatch):
        monkeypatch.setenv("RETAINDB_API_KEY", "env-other-profile")
        multiplex_scope({"RETAINDB_API_KEY": "scoped-key"})

        from plugins.memory.retaindb import RetainDBMemoryProvider

        assert RetainDBMemoryProvider().is_available() is True

    def test_retaindb_scoped_miss_does_not_borrow_environ(
        self, multiplex_scope, monkeypatch
    ):
        # Env holds another profile's key; the active profile's scope has none.
        monkeypatch.setenv("RETAINDB_API_KEY", "env-other-profile")
        multiplex_scope({})

        from plugins.memory.retaindb import RetainDBMemoryProvider

        assert RetainDBMemoryProvider().is_available() is False


# ---------------------------------------------------------------------------
# Family B — image_gen plugins
# ---------------------------------------------------------------------------

class TestImageGenFamily:
    def test_deepinfra_scoped_key_wins(self, multiplex_scope, monkeypatch):
        monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
        multiplex_scope({"DEEPINFRA_API_KEY": "scoped-key"})

        from plugins.image_gen.deepinfra import DeepInfraImageGenProvider

        assert DeepInfraImageGenProvider().is_available() is True

    def test_deepinfra_scoped_miss_does_not_borrow_environ(
        self, multiplex_scope, monkeypatch
    ):
        monkeypatch.setenv("DEEPINFRA_API_KEY", "env-other-profile")
        multiplex_scope({})

        from plugins.image_gen.deepinfra import DeepInfraImageGenProvider

        assert DeepInfraImageGenProvider().is_available() is False


# ---------------------------------------------------------------------------
# Family C — browser/web plugins
# ---------------------------------------------------------------------------

class TestBrowserFamily:
    def test_firecrawl_scoped_key_wins(self, multiplex_scope, monkeypatch):
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        multiplex_scope({"FIRECRAWL_API_KEY": "scoped-key"})

        from plugins.browser.firecrawl.provider import FirecrawlBrowserProvider

        provider = FirecrawlBrowserProvider()
        assert provider.is_available() is True
        assert provider._headers()["Authorization"] == "Bearer scoped-key"

    def test_firecrawl_scoped_miss_does_not_borrow_environ(
        self, multiplex_scope, monkeypatch
    ):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "env-other-profile")
        multiplex_scope({})

        from plugins.browser.firecrawl.provider import FirecrawlBrowserProvider

        assert FirecrawlBrowserProvider().is_available() is False


# ---------------------------------------------------------------------------
# Family E — google_meet spawn-wrap
# ---------------------------------------------------------------------------

class TestGoogleMeetSpawn:
    @pytest.mark.parametrize("inherited", [False, True])
    def test_caption_child_does_not_receive_retired_speech_credentials(
        self, multiplex_scope, monkeypatch, tmp_path, inherited
    ):
        """Caption collection neither needs speech keys nor borrows another profile's."""
        retired = ("OPENAI_API_KEY", "HERMES_MEET_REALTIME_KEY", "HERMES_MEET_REALTIME_MODEL",
                   "HERMES_MEET_REALTIME_VOICE", "HERMES_MEET_REALTIME_INSTRUCTIONS")
        for key in retired:
            if inherited:
                monkeypatch.setenv(key, "other-profile-value")
            else:
                monkeypatch.delenv(key, raising=False)
        multiplex_scope({"OPENAI_API_KEY": "scoped-openai-key"})

        import plugins.google_meet.process_manager as pm

        monkeypatch.setattr(pm, "_root", lambda: tmp_path)
        monkeypatch.setattr(pm, "_pid_alive", lambda pid: False)
        captured: dict[str, Any] = {}

        class _FakeProc:
            pid = 4242

        def fake_popen(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return _FakeProc()

        monkeypatch.setattr(pm.subprocess, "Popen", fake_popen)
        result = pm.start(
            "https://meet.google.com/abc-defg-hij",
            out_dir=tmp_path / "meeting",
            mode="transcribe",
        )
        assert result["ok"] is True
        child_env = captured["env"]
        assert not set(retired) & child_env.keys()
        assert child_env["HERMES_MEET_MODE"] == "transcribe"
        assert child_env["HERMES_MEET_URL"] == "https://meet.google.com/abc-defg-hij"
