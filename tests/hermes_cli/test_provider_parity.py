"""Provider catalog stays aligned with the native model picker."""

from hermes_cli.models_catalog_static import listed_canonical_providers
from hermes_cli.provider_catalog import provider_catalog, tab_for_auth_type


def test_catalog_covers_exactly_the_listed_picker_providers():
    listed = [entry.slug for entry in listed_canonical_providers()]
    descriptors = provider_catalog()
    assert [entry.slug for entry in descriptors] == listed
    assert len({entry.slug for entry in descriptors}) == len(descriptors)
    assert all(entry.tab == tab_for_auth_type(entry.auth_type) for entry in descriptors)


def test_shared_api_key_preserves_each_provider_profile():
    from providers import get_provider_profile

    international = get_provider_profile("alibaba")
    china = get_provider_profile("alibaba-cn")
    assert international.name != china.name
    assert international.base_url != china.base_url
    assert "DASHSCOPE_API_KEY" in international.env_vars
    assert "DASHSCOPE_API_KEY" in china.env_vars
