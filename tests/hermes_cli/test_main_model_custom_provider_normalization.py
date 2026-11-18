"""Configured providers retain their declared or canonical custom identities."""

import pytest

from hermes_cli.providers import resolve_custom_provider, resolve_user_provider


def test_providers_block_keeps_declared_bare_slug():
    result = resolve_user_provider(
        "commandcode", {"commandcode": {"base_url": "http://localhost:55990/v1"}},
    )

    assert result is not None
    assert result.id == "commandcode"
    assert result.base_url == "http://localhost:55990/v1"


@pytest.mark.parametrize("provider", ["US Azure", "custom:us-azure"])
def test_custom_provider_name_canonicalizes_to_durable_slug(provider):
    config = {
        "custom_providers": [
            {"name": "US Azure", "base_url": "http://localhost:18025/v1"}
        ]
    }

    result = resolve_custom_provider(provider, config["custom_providers"])

    assert result is not None
    assert result.id == "custom:us-azure"
    assert result.base_url == "http://localhost:18025/v1"