"""Browser-exec and computer_use backend caches are namespaced by the served profile.

Regression for #110032: both process-global caches were keyed by the caller's session/task id
alone, so under gateway.multiplex_profiles two profiles using the same id (``"default"``, a shared
named browser session, a matching DISPLAY) resolved to the FIRST profile's browser / cua-driver.
Outside a served-profile scope every key stays byte-identical to the legacy shape."""

from __future__ import annotations

import pytest

from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@pytest.fixture
def two_homes(tmp_path):
    a = tmp_path / "profiles" / "a"
    b = tmp_path / "profiles" / "b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    return a, b


def _under(home):
    return set_hermes_home_override(str(home))


def test_browser_exec_cache_key_differs_per_served_profile_and_is_legacy_when_unscoped(two_homes):
    import tools.browser_use_cli as bu

    a, b = two_homes
    assert bu._backend_cache_key("t1", "work") == "bu-named-work"
    assert bu._backend_cache_key(None) == "browser-exec-default"
    tok = _under(a)
    try:
        key_a = bu._backend_cache_key("t1", "work")
    finally:
        reset_hermes_home_override(tok)
    tok = _under(b)
    try:
        key_b = bu._backend_cache_key("t1", "work")
        key_b_again = bu._backend_cache_key("t1", "work")
    finally:
        reset_hermes_home_override(tok)
    assert key_a != key_b and key_b == key_b_again
    assert key_a.startswith("bu-named-work") and key_b.startswith("bu-named-work")
