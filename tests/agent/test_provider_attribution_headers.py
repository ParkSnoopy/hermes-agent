"""Provider client assembly preserves functional headers without attribution."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.client_lifecycle import ClientLifecycleMixin


class ClientDouble(ClientLifecycleMixin):
    def __init__(self, provider):
        self.provider = provider
        self.api_mode = "chat_completions"
        self._client_kwargs = {"api_key": "test-key"}


@pytest.mark.parametrize("provider,url", [
    ("ai-gateway", "https://ai-gateway.vercel.sh/v1"),
    ("opencode-go", "https://opencode.ai/zen/go/v1"),
    ("fireworks", "https://api.fireworks.ai/inference/v1"),
    ("openrouter", "https://openrouter.ai/api/v1"),
])
def test_main_and_auxiliary_no_automatic_attribution(provider, url):
    from agent.auxiliary_client import _endpoint_default_headers

    agent = ClientDouble(provider)
    with patch("hermes_cli.config.load_config", return_value={}), \
         patch("hermes_cli.config.load_config_readonly", return_value={}):
        agent._apply_client_headers_for_base_url(url)
        auxiliary = _endpoint_default_headers(url, provider) or {}
    forbidden = {"http-referer", "x-title", "x-openrouter-categories", "user-agent"}
    for headers in (agent._client_kwargs.get("default_headers") or {}, auxiliary):
        assert not forbidden.intersection(k.lower() for k in headers)


def test_cache_and_user_overrides_survive():
    config = {"openrouter": {"response_cache": True, "response_cache_ttl": 600},
              "model": {"default_headers": {"HTTP-Referer": "https://user.example", "User-Agent": "user-client"}}}
    agent = ClientDouble("openrouter")
    from agent.auxiliary_client import _endpoint_default_headers

    with patch("hermes_cli.config.load_config", return_value=config), \
         patch("hermes_cli.config.load_config_readonly", return_value=config):
        agent._apply_client_headers_for_base_url("https://openrouter.ai/api/v1")
        auxiliary = _endpoint_default_headers("https://openrouter.ai/api/v1", "openrouter")
    assert auxiliary["HTTP-Referer"] == "https://user.example"
    assert auxiliary["User-Agent"] == "user-client"
    headers = agent._client_kwargs["default_headers"]
    assert headers["X-OpenRouter-Cache"] == "true"
    assert headers["X-OpenRouter-Cache-TTL"] == "600"
    assert headers["HTTP-Referer"] == "https://user.example"
    assert headers["User-Agent"] == "user-client"


def test_nvidia_billing_and_kimi_protocol_headers_survive():
    for provider, url, expected in [
        ("nvidia", "https://integrate.api.nvidia.com/v1", {"X-BILLING-INVOKE-ORIGIN": "HermesAgent"}),
        ("kimi-coding", "https://api.kimi.com/coding/v1", {"User-Agent": "claude-code/0.1.0"}),
    ]:
        agent = ClientDouble(provider)
        with patch("hermes_cli.config.load_config", return_value={}):
            agent._apply_client_headers_for_base_url(url)
        assert expected.items() <= agent._client_kwargs["default_headers"].items()


def test_routed_client_preserves_sdk_custom_headers():
    from agent.agent_init import _client_kwargs_from_routed

    routed = SimpleNamespace(api_key="test-key", base_url="https://integrate.api.nvidia.com/v1",
                             _custom_headers={"X-BILLING-INVOKE-ORIGIN": "HermesAgent"})
    assert _client_kwargs_from_routed(routed, None)["default_headers"] == routed._custom_headers
