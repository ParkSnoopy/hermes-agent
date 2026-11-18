"""Local endpoint classification distinguishes LAN addresses from public routes."""

import pytest

from agent.model_metadata import is_local_endpoint


@pytest.mark.parametrize(("url", "local"), [
    ("http://byron.local:11434/v1", True),  # RFC 6762 mDNS, LAN-only
    ("http://[fd00::1]:11434", True),  # IPv6 ULA
    ("http://[2607:f8b0::1]:11434", False),  # global IPv6 must not ride the "no dots" rule
])
def test_is_local_endpoint_mdns_and_ipv6_scope(url, local):
    assert is_local_endpoint(url) is local
