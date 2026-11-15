"""Generic platform authorization uses the owning profile's secret scope."""
from contextlib import contextmanager
from types import SimpleNamespace

from agent.secret_scope import get_secret_str, reset_secret_scope, set_secret_scope
from gateway.config import Platform, PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.run import _own_policy_open_startup_violation


@contextmanager
def _scope(values):
    token = set_secret_scope(values)
    try:
        yield
    finally:
        reset_secret_scope(token)


def test_scoped_secret_wins_and_multiplex_misses_do_not_leak(monkeypatch):
    monkeypatch.setenv('PROBE_ALLOWED_USERS', 'default-user')
    monkeypatch.setattr('agent.secret_scope._MULTIPLEX_ACTIVE', True)
    with _scope({'PROBE_ALLOWED_USERS': 'profile-user'}):
        assert get_secret_str('PROBE_ALLOWED_USERS') == 'profile-user'
    with _scope({}):
        assert get_secret_str('PROBE_ALLOWED_USERS', 'closed') == 'closed'
    monkeypatch.setattr('agent.secret_scope._MULTIPLEX_ACTIVE', False)
    assert get_secret_str('PROBE_ALLOWED_USERS') == 'default-user'


def test_open_policy_guard_uses_registered_transport_and_scoped_opt_in(monkeypatch):
    monkeypatch.setattr('agent.secret_scope._MULTIPLEX_ACTIVE', True)
    monkeypatch.setenv('GATEWAY_ALLOW_ALL_USERS', 'true')
    monkeypatch.setattr('hermes_cli.plugins.discover_plugins', lambda: None)
    entry = PlatformEntry(name='scopeprobe', label='Scope probe', adapter_factory=lambda _: None,
                          check_fn=lambda: True, own_policy_env=('PROBE_DM_POLICY', 'PROBE_GROUP_POLICY'),
                          allow_all_env='PROBE_ALLOW_ALL_USERS')
    platform_registry.register(entry)
    try:
        cfg = SimpleNamespace(platforms={Platform('scopeprobe'): PlatformConfig(enabled=True, extra={'dm_policy': 'open'})})
        with _scope({'GATEWAY_ALLOW_ALL_USERS': 'true'}):
            assert _own_policy_open_startup_violation(cfg) is None
        with _scope({}):
            reason = _own_policy_open_startup_violation(cfg)
            assert reason is not None and 'scopeprobe' in reason
        with _scope({'PROBE_ALLOW_ALL_USERS': 'yes'}):
            assert _own_policy_open_startup_violation(cfg) is None
    finally:
        platform_registry.unregister('scopeprobe')