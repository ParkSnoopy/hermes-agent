"""The shared access-policy gate remains fail-closed across profile scopes."""

import contextlib

import pytest

from agent.secret_scope import reset_secret_scope, set_secret_scope
from gateway.platforms.access_policy_mixin import OwnAccessPolicyMixin


@contextlib.contextmanager
def _scope(secrets):
    token = set_secret_scope(secrets)
    try:
        yield
    finally:
        reset_secret_scope(token)


def test_mixin_host_without_prefix_is_rejected_at_class_creation():
    with pytest.raises(TypeError, match="ALLOW_ALL_ENV_PREFIX"):
        class Host(OwnAccessPolicyMixin):
            _dm_policy = "open"


def test_platform_prefix_env_name_is_scoped_and_fail_closed(monkeypatch):
    class Host(OwnAccessPolicyMixin):
        ALLOW_ALL_ENV_PREFIX = "DEMO"
        _dm_policy, _group_policy, _allow_from, _group_allow_from = "open", "open", [], []

    monkeypatch.setattr("agent.secret_scope._MULTIPLEX_ACTIVE", True)
    monkeypatch.setenv("DEMO_ALLOW_ALL_USERS", "true")
    assert Host()._allow_all_env_names() == ("GATEWAY_ALLOW_ALL_USERS", "DEMO_ALLOW_ALL_USERS")
    with _scope({}):
        assert Host()._is_dm_allowed("anyone") is False
    with _scope({"DEMO_ALLOW_ALL_USERS": "yes"}):
        assert Host()._is_dm_allowed("anyone") is True
