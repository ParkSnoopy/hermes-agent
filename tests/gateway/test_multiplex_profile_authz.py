"""Profile-aware authorization for registered messaging transports."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.session import SessionSource


@pytest.fixture
def transport(monkeypatch):
    from gateway.platform_registry import PlatformEntry, platform_registry

    entry = PlatformEntry(name='profile_auth_test', label='Profile auth test',
                          adapter_factory=lambda _: None, check_fn=lambda: True,
                          allowed_users_env='PROFILE_AUTH_TEST_ALLOWED_USERS',
                          own_policy_env=('PROFILE_AUTH_TEST_DM_POLICY', 'PROFILE_AUTH_TEST_GROUP_POLICY'),
                          allow_all_env='PROFILE_AUTH_TEST_ALLOW_ALL_USERS')
    monkeypatch.setitem(platform_registry._entries, entry.name, entry)
    monkeypatch.setattr('hermes_cli.plugins.discover_plugins', lambda: None)
    for key in ('PROFILE_AUTH_TEST_ALLOWED_USERS', 'PROFILE_AUTH_TEST_ALLOW_ALL_USERS',
                'GATEWAY_ALLOWED_USERS', 'GATEWAY_ALLOW_ALL_USERS'):
        monkeypatch.delenv(key, raising=False)
    return Platform(entry.name)


def _runner(platform, extra=None):
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    default = SimpleNamespace(send=AsyncMock(), enforces_own_access_policy=True,
                              _dm_policy='allowlist', _group_policy='pairing')
    secondary = SimpleNamespace(send=AsyncMock(), enforces_own_access_policy=False,
                                config=PlatformConfig(enabled=True, extra=extra or {}),
                                normalize_user_id=lambda value: value.lower().removeprefix('id:'))
    runner.adapters = {platform: default}
    runner._profile_adapters = {'coder': {platform: secondary}}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    return runner, default, secondary


def _source(platform, user='alice', profile='coder'):
    return SessionSource(platform=platform, user_id=user, chat_id='chat',
                         user_name=user, chat_type='dm', profile=profile)


def test_profile_adapter_routing_and_default_policy(transport):
    runner, default, secondary = _runner(transport)
    assert runner._is_user_authorized(_source(transport, profile=None)) is True
    runner._active_profile_name = lambda: 'coder'
    assert runner._authorization_adapter(transport, profile='coder') is secondary
    runner._profile_adapters = {'coder': {}}
    assert runner._authorization_adapter(transport, profile='coder') is None
    runner._profile_adapters = {}
    runner._active_profile_name = lambda: 'dev'
    assert runner._authorization_adapter(transport, profile='dev') is default


def test_upstream_auth_in_secondary_profile(transport):
    runner, _, secondary = _runner(transport)
    secondary.authorization_is_upstream = True
    runner._active_profile_name = lambda: 'coder'
    assert runner._is_user_authorized(_source(transport)) is True


def test_secondary_allowlist_dm_behavior(transport):
    runner, _, secondary = _runner(transport)
    secondary.enforces_own_access_policy = True
    secondary._dm_policy = 'allowlist'
    assert runner._get_unauthorized_dm_behavior(transport, profile='coder') == 'ignore'
    assert runner._get_unauthorized_dm_behavior(transport) == 'ignore'


def test_adapter_auth_check_stamps_profile(transport):
    runner, _, _ = _runner(transport)
    runner._profile_adapters = {}
    seen = []
    runner._is_user_authorized = lambda source: seen.append(source.profile) or True
    assert runner._make_adapter_auth_check(transport, profile_name='coder')('user', 'dm', 'chat')
    assert seen == ['coder']


@pytest.mark.parametrize('extra,user,expected', [
    ({'allowed_users': ['ID:ALICE']}, 'alice', True),
    ({'allowed_users': ['ID:BOB']}, 'alice', False),
    ({}, 'alice', False),
    ({'allowed_users': ['*']}, 'alice', True),
    ({'allowed_users': ['', '   ', ',']}, 'alice', False),
])
def test_secondary_profile_extra_allowlist(transport, extra, user, expected):
    runner, _, _ = _runner(transport, extra)
    assert runner._is_user_authorized(_source(transport, user)) is expected


def test_extra_allowlist_requires_registry_declaration(transport, monkeypatch):
    from gateway.platform_registry import platform_registry
    entry = platform_registry.get(transport.value)
    monkeypatch.setattr(entry, 'allowed_users_env', '')
    runner, _, _ = _runner(transport, {'allowed_users': ['alice']})
    assert runner._is_user_authorized(_source(transport)) is False


def test_open_policy_guard_respects_profile_secret_scope(transport, monkeypatch):
    from agent import secret_scope
    from gateway.run import _own_policy_open_startup_violation
    cfg = GatewayConfig(multiplex_profiles=True, platforms={
        transport: PlatformConfig(enabled=True, extra={'dm_policy': 'open'})})
    previous = secret_scope.is_multiplex_active()
    secret_scope.set_multiplex_active(True)
    monkeypatch.setenv('GATEWAY_ALLOW_ALL_USERS', 'true')
    try:
        token = secret_scope.set_secret_scope({})
        try:
            assert _own_policy_open_startup_violation(cfg) is not None
        finally:
            secret_scope.reset_secret_scope(token)
        token = secret_scope.set_secret_scope({'GATEWAY_ALLOW_ALL_USERS': 'true'})
        try:
            assert _own_policy_open_startup_violation(cfg) is None
        finally:
            secret_scope.reset_secret_scope(token)
    finally:
        secret_scope.set_multiplex_active(previous)
