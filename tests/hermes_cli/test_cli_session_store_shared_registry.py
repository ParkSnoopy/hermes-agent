"""The CLI's session store is the gateway's, never a state.db writer in the CLI process.

Main acquired ``hermes_state_registry`` (a writer handle) at startup. Rewired, the gateway is the
single writer: ``_init_session_store`` builds a ``GatewaySessionStore`` and opens no SessionDB.
"""

from types import SimpleNamespace
from typing import Any

from cli import HermesCLI


def test_cli_session_store_is_the_gateway_store_and_opens_no_writer(monkeypatch):
    import hermes_state_registry
    from hermes_cli.gateway_session_store import GatewaySessionStore

    constructed = []
    monkeypatch.setattr(hermes_state_registry, "_open_session_db", lambda path: constructed.append(path))
    cli = SimpleNamespace()
    HermesCLI._init_session_store(cli)
    assert isinstance(cli._session_db, GatewaySessionStore) and not cli._session_db_unavailable
    assert constructed == []


def test_cli_session_store_failure_retains_details_for_deferred_warning(monkeypatch):
    from gateway import warning_notifications
    from hermes_cli import cli_init_mixin, gateway_session_store

    def fail_to_construct():
        raise RuntimeError("gateway store unavailable")

    callbacks = []
    rendered = []
    monkeypatch.setattr(gateway_session_store, "session_store", fail_to_construct)
    monkeypatch.setattr(warning_notifications, "render_notification",
                        lambda callback, **kwargs: callbacks.append(callback))
    monkeypatch.setattr(cli_init_mixin, "Console",
                        lambda **kwargs: SimpleNamespace(print=rendered.append))
    cli: Any = SimpleNamespace()
    HermesCLI._init_session_store(cli)
    assert cli._session_db is None
    assert cli._session_db_unavailable is True
    assert len(callbacks) == 1
    assert rendered == []
    callbacks[0]()
    assert len(rendered) == 1
    assert "Details: gateway store unavailable" in rendered[0]
    assert "NOT be saved" in rendered[0]
