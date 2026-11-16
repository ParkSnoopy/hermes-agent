"""Unit tests for tools/tool_backend_helpers.py.

Tests cover:
- managed_nous_tools_enabled() subscription-based gate
- normalize_browser_cloud_provider() coercion
- resolve_openai_audio_api_key() priority chain
"""

from __future__ import annotations


import pytest

from hermes_cli.nous_account import NousPaidServiceAccessInfo, NousPortalAccountInfo
from tools.tool_backend_helpers import (
    managed_nous_tools_enabled,
    nous_tool_gateway_unavailable_message,
    normalize_browser_cloud_provider,
    prefers_gateway,
    resolve_openai_audio_api_key,
)


def _raise_import():
    raise ImportError("simulated missing module")


# ---------------------------------------------------------------------------
# managed_nous_tools_enabled
# ---------------------------------------------------------------------------
class TestManagedNousToolsEnabled:
    """Subscription-based gate: True for paid Nous subscribers."""

    def test_disabled_when_not_logged_in(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.nous_account.get_nous_portal_account_info",
            lambda: NousPortalAccountInfo(logged_in=False, source="none", fresh=False),
        )
        assert managed_nous_tools_enabled() is False


    def test_returns_false_on_exception(self, monkeypatch):
        """Should never crash — returns False on any exception."""
        monkeypatch.setattr(
            "hermes_cli.nous_account.get_nous_portal_account_info",
            _raise_import,
        )
        assert managed_nous_tools_enabled() is False


class TestNousToolGatewayUnavailableMessage:
    def test_uses_entitlement_reason_for_logged_in_user(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.nous_account.get_nous_portal_account_info",
            lambda force_fresh=False: NousPortalAccountInfo(
                logged_in=True,
                source="account_api",
                fresh=True,
                paid_service_access=False,
                portal_base_url="https://portal.example.test",
                paid_service_access_info=NousPaidServiceAccessInfo(
                    allowed=False,
                    reason="no_usable_credits",
                    has_active_subscription=True,
                    active_subscription_is_paid=True,
                    subscription_credits_remaining=0,
                    purchased_credits_remaining=0,
                    total_usable_credits=0,
                ),
            ),
        )

        message = nous_tool_gateway_unavailable_message("managed image generation")

        assert "credits are exhausted" in message
        assert "managed image generation" in message
        assert "https://portal.example.test/billing" in message


# ---------------------------------------------------------------------------
# normalize_browser_cloud_provider
# ---------------------------------------------------------------------------
class TestNormalizeBrowserCloudProvider:
    """Coerce arbitrary input to a lowercase browser provider key."""

    def test_none_returns_default(self):
        assert normalize_browser_cloud_provider(None) == "local"




# ---------------------------------------------------------------------------
# prefers_gateway
# ---------------------------------------------------------------------------
class TestPrefersGateway:
    """Honor bool-ish config values for tool gateway routing."""

    def test_returns_false_for_quoted_false(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.config.load_config",
            lambda: {"web": {"use_gateway": "false"}},
        )
        assert prefers_gateway("web") is False

    def test_returns_true_for_quoted_true(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.config.load_config",
            lambda: {"web": {"use_gateway": "true"}},
        )
        assert prefers_gateway("web") is True


# ---------------------------------------------------------------------------
# resolve_openai_audio_api_key
# ---------------------------------------------------------------------------
class TestResolveOpenaiAudioApiKey:
    """Priority: VOICE_TOOLS_OPENAI_KEY > OPENAI_API_KEY."""

    def test_voice_key_preferred(self, monkeypatch):
        monkeypatch.setenv("VOICE_TOOLS_OPENAI_KEY", "voice-key")
        monkeypatch.setenv("OPENAI_API_KEY", "general-key")
        assert resolve_openai_audio_api_key() == "voice-key"


    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("VOICE_TOOLS_OPENAI_KEY", "  voice-key  ")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert resolve_openai_audio_api_key() == "voice-key"


# ---------------------------------------------------------------------------
# resolve_openai_audio_api_key — profile secret scope
# ---------------------------------------------------------------------------
class TestResolveOpenaiAudioApiKeyIsProfileScoped:
    """The key this returns authenticates the TTS/STT client.

    In a multiplex gateway ``os.environ`` holds whichever profile's ``.env``
    loaded at boot, not the profile the current turn belongs to — so a raw
    read here would let one profile's voice reply or voice-note transcription
    run on (and be billed to) another profile's OpenAI account. Same contract
    ``agent/vertex_adapter`` and the WeChat send path already follow.
    """

    @pytest.fixture(autouse=True)
    def _reset_multiplex(self):
        from agent import secret_scope as ss

        ss.set_multiplex_active(False)
        yield
        ss.set_multiplex_active(False)

    def test_scope_wins_over_another_profiles_environ(self, monkeypatch):
        from agent import secret_scope as ss

        monkeypatch.delenv("VOICE_TOOLS_OPENAI_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-other-profile")
        ss.set_multiplex_active(True)
        token = ss.set_secret_scope({"OPENAI_API_KEY": "sk-this-profile"})
        try:
            assert resolve_openai_audio_api_key() == "sk-this-profile", (
                "voice/STT authenticated with another profile's OpenAI key"
            )
        finally:
            ss.reset_secret_scope(token)


    def test_single_profile_still_reads_environ(self, monkeypatch):
        """Control: no multiplexing, no scope — unchanged behaviour."""
        monkeypatch.delenv("VOICE_TOOLS_OPENAI_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-plain")
        assert resolve_openai_audio_api_key() == "sk-plain"
