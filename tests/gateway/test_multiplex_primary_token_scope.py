"""Multiplex primary configuration and transport scope regressions."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig, Platform


@pytest.fixture(autouse=True)
def _reset_multiplex_flag():
    from agent import secret_scope as ss

    ss.set_multiplex_active(False)
    yield
    ss.set_multiplex_active(False)


class TestLoadGatewayConfigForRunner:
    def test_unscoped_when_multiplex_off(self, tmp_path, monkeypatch):
        from gateway import run as run_mod

        home = tmp_path / "home"
        home.mkdir()

        (home / "config.yaml").write_text("gateway:\n  multiplex_profiles: false\n", encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        # Without multiplex, dotenv is still loaded into os.environ by the
        # normal env loader in real gateways; here we only assert the helper
        # returns a non-multiplex config without requiring a scope.
        cfg = run_mod.load_gateway_config_for_runner()
        assert cfg.multiplex_profiles is False

    def test_scoped_reload_still_sees_container_api_server_env(self, tmp_path, monkeypatch):
        """#69379 — container-env API_SERVER_* visible during the scoped reload.

        Docker/systemd deployments enable the api_server platform via the
        process environment (compose ``environment:`` block), not the profile
        ``.env``. The multiplex runner reload happens inside the default
        profile's secret scope; the listener settings are on the global
        allowlist (deployment config, not profile secrets) so they must stay
        visible there — while API_SERVER_KEY (a credential) still resolves
        through the profile scope.
        """
        from agent import secret_scope as ss
        from gateway import run as run_mod

        home = tmp_path / "home"
        home.mkdir()
        # Credentials belong in the profile .env; listener settings do not.
        (home / ".env").write_text(
            "API_SERVER_KEY=profile-scoped-key-0123456789abcdef\n",
            encoding="utf-8",
        )
        (home / "config.yaml").write_text(
            "gateway:\n  multiplex_profiles: true\n", encoding="utf-8"
        )
        monkeypatch.setenv("HERMES_HOME", str(home))
        # Listener settings live ONLY in os.environ — the Docker compose case.
        monkeypatch.setenv("API_SERVER_ENABLED", "true")
        monkeypatch.setenv("API_SERVER_HOST", "0.0.0.0")
        monkeypatch.setenv("API_SERVER_PORT", "8642")
        monkeypatch.delenv("API_SERVER_KEY", raising=False)
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setattr(run_mod, "get_hermes_home", lambda: home)
        monkeypatch.setattr(run_mod, "_hermes_home", home)
        # Model the real multiplexed gateway: run.py flips the runtime flag
        # before the runner reload, making any installed scope authoritative.
        ss.set_multiplex_active(True)

        cfg = run_mod.load_gateway_config_for_runner()

        assert cfg.multiplex_profiles is True

        # api_server present: key from the profile scope, listener settings
        # from the container environment via the global allowlist.
        api = cfg.platforms.get(Platform.API_SERVER)
        assert api is not None, (
            "api_server should be enabled from container env even inside "
            "the scoped runner reload (#69379)"
        )
        assert api.enabled is True
        assert api.extra.get("key") == "profile-scoped-key-0123456789abcdef"
        assert api.extra.get("host") == "0.0.0.0"
        assert api.extra.get("port") == 8642



class TestPrimaryMessageRuntimeScope:

    @pytest.mark.asyncio
    async def test_primary_busy_path_authorizes_in_transport_scope(
        self, tmp_path, monkeypatch
    ):
        """Routed busy messages must read the admitting transport's profile."""
        from agent import secret_scope
        from gateway import run as run_mod
        from gateway.run import GatewayRunner
        from gateway.session import SessionSource

        home = tmp_path / "home"
        home.mkdir()
        (home / ".env").write_text(
            "API_SERVER_KEY=primary-profile-key\n",
            encoding="utf-8",
        )
        sharik = tmp_path / "profiles" / "sharik"
        sharik.mkdir(parents=True)
        (sharik / ".env").write_text("API_SERVER_KEY=other-profile-key\n", encoding="utf-8")
        (sharik / "config.yaml").write_text("{}\n", encoding="utf-8")

        monkeypatch.setattr(run_mod, "get_hermes_home", lambda: home)
        secret_scope.set_multiplex_active(True)

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = GatewayConfig(multiplex_profiles=True)
        runner._resolve_profile_home_for_source = lambda _source: sharik  # type: ignore[method-assign]
        runner._session_key_for_source = lambda source: f"agent:{source.profile}:api_server:dm:{source.chat_id}"  # type: ignore[method-assign]
        runner._is_user_authorized = lambda source, **kwargs: secret_scope.get_secret("API_SERVER_KEY") == "primary-profile-key"
        runner._is_user_authorized_for_source = GatewayRunner._is_user_authorized_for_source.__get__(runner)  # type: ignore[method-assign]

        async def _busy(event, session_key):
            assert session_key == f"agent:sharik:api_server:dm:{event.source.chat_id}"
            # This is what _handle_active_session_busy_message now calls.
            return runner._is_user_authorized_for_source(event.source)

        runner._handle_active_session_busy_message = _busy  # type: ignore[method-assign]
        handler = runner._primary_busy_session_handler()
        event = SimpleNamespace(
            source=SessionSource(
                platform=Platform.API_SERVER,
                chat_id="+15550001111",
                chat_type="dm",
                user_id="+15550001111",
                user_name="Remi",
                profile="sharik",
            )
        )

        assert await handler(event, "stale-unrouted-key") is True
        with pytest.raises(secret_scope.UnscopedSecretError):
            secret_scope.get_secret("API_SERVER_KEY")
