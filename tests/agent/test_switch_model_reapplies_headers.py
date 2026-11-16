"""Regression tests for #61099: switch_model must reapply provider-specific
default headers when it rebuilds _client_kwargs from scratch.

Without _apply_client_headers_for_base_url() in the rebuild path, a /model
switch drops functional headers like Kimi's User-Agent sentinel (403 without it).
"""

from unittest.mock import MagicMock, patch

from run_agent import AIAgent
from agent.context_compressor import ContextCompressor


def _make_agent(provider="copilot", base_url="https://api.githubcopilot.com") -> AIAgent:
    """Minimal AIAgent with a context_compressor, skipping __init__."""
    agent = AIAgent.__new__(AIAgent)

    agent.model = "claude-opus-4.8"
    agent.provider = provider
    agent.base_url = base_url
    agent.api_key = "sk-primary"
    agent.api_mode = "chat_completions"
    agent.client = MagicMock()
    agent.quiet_mode = True
    agent._config_context_length = None
    agent._client_kwargs = {"api_key": "sk-primary", "base_url": base_url}

    compressor = ContextCompressor(
        model=agent.model,
        threshold_percent=0.50,
        base_url=base_url,
        api_key="sk-primary",
        provider=provider,
        quiet_mode=True,
        config_context_length=None,
    )
    agent.context_compressor = compressor
    agent._primary_runtime = {}

    return agent


@patch("agent.model_metadata.get_model_context_length", return_value=131_072)
def test_switch_to_openrouter_reapplies_cache_headers(mock_ctx_len):
    """Rebuilt clients retain functional OpenRouter cache settings."""
    agent = _make_agent(provider="copilot", base_url="https://api.githubcopilot.com")

    with patch("agent.auxiliary_client.build_or_headers", return_value={"X-OpenRouter-Cache": "true"}):
        agent.switch_model(
            "deepseek/deepseek-chat",
            "openrouter",
            api_key="sk-or-new",
            base_url="https://openrouter.ai/api/v1",
        )

    headers = agent._client_kwargs.get("default_headers") or {}
    assert headers["X-OpenRouter-Cache"] == "true"
    assert "HTTP-Referer" not in headers
    assert "X-Title" not in headers




@patch("agent.model_metadata.get_model_context_length", return_value=131_072)
def test_switch_away_from_headered_provider_clears_stale_headers(mock_ctx_len):
    """Switching FROM a headered provider TO one with no URL-specific headers
    must not carry the old provider's headers along."""
    agent = _make_agent(provider="openrouter", base_url="https://openrouter.ai/api/v1")
    agent._client_kwargs["default_headers"] = {
        "HTTP-Referer": "https://hermes-agent.nousresearch.com",
        "X-Title": "Hermes Agent",
    }

    agent.switch_model(
        "MiniMax-M3",
        "custom:minimax",
        api_key="sk-minimax",
        base_url="https://api.minimax.io/v1",
    )

    headers = agent._client_kwargs.get("default_headers") or {}
    assert "HTTP-Referer" not in headers
    assert "X-Title" not in headers
