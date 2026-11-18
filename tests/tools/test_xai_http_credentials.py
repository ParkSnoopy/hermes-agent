import pytest


def _set_xai_oauth_unavailable(monkeypatch):
    from hermes_cli import auth
    from hermes_cli import auth_xai

    monkeypatch.setattr(auth, "resolve_xai_oauth_runtime_credentials", lambda **_: {})
    monkeypatch.setattr(auth_xai, "resolve_xai_oauth_runtime_credentials", lambda **_: {})


def test_xai_credentials_fail_closed_without_profile_scope(tmp_path, monkeypatch):
    from agent import secret_scope
    from hermes_cli.config import invalidate_env_cache
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("XAI_API_KEY", "foreign-xai-key")
    monkeypatch.setenv("XAI_BASE_URL", "https://foreign.example/v1")
    _set_xai_oauth_unavailable(monkeypatch)
    invalidate_env_cache()
    previous_multiplex = secret_scope.is_multiplex_active()
    token = secret_scope.set_secret_scope(None)
    secret_scope.set_multiplex_active(True)
    try:
        with pytest.raises(secret_scope.UnscopedSecretError):
            resolve_xai_http_credentials(force_refresh=True)
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(previous_multiplex)
        invalidate_env_cache()


def test_xai_credentials_do_not_fall_back_to_environ_when_scope_has_no_key(
    tmp_path, monkeypatch
):
    from agent import secret_scope
    from hermes_cli.config import invalidate_env_cache
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("XAI_API_KEY", "foreign-xai-key")
    monkeypatch.setenv("XAI_BASE_URL", "https://foreign.example/v1")
    _set_xai_oauth_unavailable(monkeypatch)
    invalidate_env_cache()
    previous_multiplex = secret_scope.is_multiplex_active()
    token = secret_scope.set_secret_scope({})
    secret_scope.set_multiplex_active(True)
    try:
        credentials = resolve_xai_http_credentials(force_refresh=True)
        assert credentials == {
            "provider": "xai",
            "api_key": "",
            "base_url": "https://api.x.ai/v1",
        }
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(previous_multiplex)
        invalidate_env_cache()


def _install_fake_oauth_pool(monkeypatch, oauth_token: str) -> None:
    from types import SimpleNamespace

    entry = SimpleNamespace(
        access_token=oauth_token,
        runtime_api_key=None,
        runtime_base_url=None,
        base_url="https://api.x.ai/v1",
    )

    class _FakePool:
        def select(self):
            return entry

        def try_refresh_matching(self, _hint):
            return entry

    def _fake_load_pool(provider_id):
        if provider_id == "xai-oauth":
            return _FakePool()
        raise KeyError(provider_id)

    monkeypatch.setattr("agent.credential_pool.load_pool", _fake_load_pool)


def test_xai_oauth_wins_over_available_api_key(monkeypatch):
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "hermes_cli.config.get_env_value",
        lambda name, default=None: {"XAI_API_KEY": "paid-key-x1"}.get(name, default),
    )
    _install_fake_oauth_pool(monkeypatch, "oauth-token-x1")

    creds = resolve_xai_http_credentials()
    assert creds["provider"] == "xai-oauth"
    assert creds["api_key"] == "oauth-token-x1"


def test_xai_api_key_fallback_honors_profile_scope(tmp_path, monkeypatch):
    from agent import secret_scope
    from hermes_cli.config import invalidate_env_cache
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_BASE_URL", raising=False)
    monkeypatch.delenv("HERMES_XAI_BASE_URL", raising=False)
    monkeypatch.setattr("agent.credential_pool.load_pool", lambda _: None)
    invalidate_env_cache()
    previous_multiplex = secret_scope.is_multiplex_active()
    token = secret_scope.set_secret_scope({"XAI_API_KEY": "scoped-key-x1"})
    secret_scope.set_multiplex_active(True)
    try:
        creds = resolve_xai_http_credentials()
        assert creds["provider"] == "xai"
        assert creds["api_key"] == "scoped-key-x1"
        assert creds["base_url"] == "https://api.x.ai/v1"
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(previous_multiplex)
        invalidate_env_cache()


def _write_auth_store(tmp_path, monkeypatch, suppressed):
    import json
    import time

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(json.dumps({
        "version": 1,
        "providers": {"xai-oauth": {"tokens": {
            "access_token": "oauth-live-token", "refresh_token": "r",
            "expires_at": int(time.time() * 1000) + 3_600_000}}},
        "credential_pool": {},
        "suppressed_sources": suppressed,
    }), encoding="utf-8")


def test_suppression_is_scoped_to_its_provider(tmp_path, monkeypatch):
    """Suppressing xai's env source must not blind another provider's env read."""
    from tools.tool_backend_helpers import resolve_provider_secret

    _write_auth_store(tmp_path, monkeypatch, {"xai": ["env:XAI_API_KEY"]})
    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    monkeypatch.setenv("XAI_API_KEY", "dead-key")
    assert resolve_provider_secret("FAL_KEY", "fal") == "fal-test-key"
    assert resolve_provider_secret("XAI_API_KEY", "xai") == ""
