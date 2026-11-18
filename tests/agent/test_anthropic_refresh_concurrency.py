"""Claude credential refresh concurrency and rotation."""

import threading
import time
from agent.anthropic_credentials import _refresh_oauth_token


class TestRefreshOAuthTokenAdoptsFreshCredential:
    """``_refresh_oauth_token`` should adopt a credential Claude Code has
    already refreshed rather than POSTing a (possibly already-rotated)
    single-use refresh token and racing Claude Code into ``invalid_grant``.
    """

    _FRESH = 9_999_999_999_999

    def test_adopts_already_refreshed_token_without_posting(self, tmp_path, monkeypatch):
        """When a live source already holds a valid token, return it and skip
        the network refresh entirely.
        """
        monkeypatch.setattr(
            "agent.anthropic_credentials.claude_code_credentials_path",
            lambda: tmp_path / ".claude" / ".credentials.json",
        )
        fresh = {
            "accessToken": "already-refreshed-token",
            "refreshToken": "live-refresh",
            "expiresAt": self._FRESH,
        }
        monkeypatch.setattr(
            "agent.anthropic_credentials.read_claude_code_credentials",
            lambda: fresh,
        )

        def _should_not_be_called(*args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("refresh_anthropic_oauth_pure must not be called")

        monkeypatch.setattr(
            "agent.anthropic_credentials.refresh_anthropic_oauth_pure",
            _should_not_be_called,
        )

        # Stale creds passed in by the caller — should be ignored in favor
        # of the live, already-refreshed token.
        result = _refresh_oauth_token({"refreshToken": "stale", "expiresAt": 1})
        assert result == "already-refreshed-token"

    def test_falls_back_to_network_refresh_when_no_fresh_credential(self, tmp_path, monkeypatch):
        """When no live source has a valid token, fall back to refreshing
        ourselves using the freshest available refresh token.
        """
        monkeypatch.setattr(
            "agent.anthropic_credentials.claude_code_credentials_path",
            lambda: tmp_path / ".claude" / ".credentials.json",
        )
        # Live read returns an expired credential carrying a refresh token.
        monkeypatch.setattr(
            "agent.anthropic_credentials.read_claude_code_credentials",
            lambda: {"accessToken": "expired", "refreshToken": "live-refresh", "expiresAt": 1},
        )
        captured = {}

        def _fake_refresh(refresh_token, **kwargs):
            captured["refresh_token"] = refresh_token
            return {
                "access_token": "newly-minted",
                "refresh_token": "rotated",
                "expires_at_ms": self._FRESH,
            }

        monkeypatch.setattr(
            "agent.anthropic_credentials.refresh_anthropic_oauth_pure", _fake_refresh
        )
        monkeypatch.setattr(
            "agent.anthropic_credentials._write_claude_code_credentials",
            lambda *a, **k: None,
        )

        result = _refresh_oauth_token({"refreshToken": "caller-refresh", "expiresAt": 1})
        assert result == "newly-minted"
        # Prefers the live source's refresh token over the caller's stale copy.
        assert captured["refresh_token"] == "live-refresh"

    def test_concurrent_refreshes_use_one_shared_credentials_lock(self, tmp_path, monkeypatch):
        """Direct resolver refreshes must not spend one Claude token twice."""
        shared_credentials_path = tmp_path / ".claude" / ".credentials.json"
        monkeypatch.setattr(
            "agent.anthropic_credentials.claude_code_credentials_path",
            lambda: shared_credentials_path,
        )

        state = {
            "accessToken": "stale-access",
            "refreshToken": "stale-refresh",
            "expiresAt": 1,
        }
        state_lock = threading.Lock()
        calls = []

        def read_credentials():
            with state_lock:
                return dict(state)

        def write_credentials(access_token, refresh_token, expires_at_ms, **_kwargs):
            with state_lock:
                state.update(
                    accessToken=access_token,
                    refreshToken=refresh_token,
                    expiresAt=expires_at_ms,
                )

        def refresh(refresh_token, **_kwargs):
            calls.append(refresh_token)
            # Without the production shared lock, both callers read the stale
            # pair before either fake network request commits its rotation.
            time.sleep(0.05)
            with state_lock:
                if state["refreshToken"] != refresh_token:
                    raise ValueError("invalid_grant: refresh token already used")
                return {
                    "access_token": "fresh-access",
                    "refresh_token": "fresh-refresh",
                    "expires_at_ms": self._FRESH,
                }

        monkeypatch.setattr("agent.anthropic_credentials.read_claude_code_credentials", read_credentials)
        monkeypatch.setattr("agent.anthropic_credentials._write_claude_code_credentials", write_credentials)
        monkeypatch.setattr("agent.anthropic_credentials.refresh_anthropic_oauth_pure", refresh)

        results = {}
        errors = {}
        start = threading.Barrier(2)

        def run(name):
            try:
                start.wait(timeout=5)
                results[name] = _refresh_oauth_token(
                    {
                        "accessToken": "stale-access",
                        "refreshToken": "stale-refresh",
                        "expiresAt": 1,
                    }
                )
            except BaseException as exc:  # pragma: no cover - failure diagnostics
                errors[name] = exc

        threads = [threading.Thread(target=run, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert not [thread for thread in threads if thread.is_alive()]
        assert not errors, errors
        assert results == {"a": "fresh-access", "b": "fresh-access"}
        assert calls == ["stale-refresh"], calls


