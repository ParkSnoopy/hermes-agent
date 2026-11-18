"""Config propagation tests for orphan-reap settings (#79635)."""

import textwrap

import pytest


@pytest.fixture()
def _temp_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_TUI_WS_ORPHAN_REAP_GRACE_S", raising=False)
    return home


def _write_config(home, body: str) -> None:
    (home / "config.yaml").write_text(textwrap.dedent(body))




def test_orphan_settings_propagate_from_yaml(_temp_home):
    from hermes_cli.config import load_config
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    _write_config(
        _temp_home,
        """
        dashboard:
          ws_orphan_reap_grace_s: 90
          ws_orphan_activity_stale_s: 120
        """,
    )
    cfg = load_config()
    dash = cfg["dashboard"]
    assert dash["ws_orphan_reap_grace_s"] == 90
    assert dash["ws_orphan_activity_stale_s"] == 120
    # Deep-merge: sibling defaults survive a partial user section.
    assert dash["startup_orphan_sweep"] == DEFAULT_CONFIG["dashboard"]["startup_orphan_sweep"]


def test_ws_orphan_reap_grace_reads_config(_temp_home):
    from tui_gateway import server

    _write_config(
        _temp_home,
        """
        dashboard:
          ws_orphan_reap_grace_s: 33
        """,
    )
    assert server._resolve_ws_orphan_reap_grace() == 33.0


def test_ws_orphan_reap_grace_env_var_overrides_config(_temp_home, monkeypatch):
    from tui_gateway import server

    _write_config(
        _temp_home,
        """
        dashboard:
          ws_orphan_reap_grace_s: 33
        """,
    )
    monkeypatch.setenv("HERMES_TUI_WS_ORPHAN_REAP_GRACE_S", "7")
    assert server._resolve_ws_orphan_reap_grace() == 7.0


def test_ws_orphan_reap_grace_invalid_values_fall_back(_temp_home, monkeypatch):
    from tui_gateway import server

    _write_config(
        _temp_home,
        """
        dashboard:
          ws_orphan_reap_grace_s: not-a-number
        """,
    )
    assert server._resolve_ws_orphan_reap_grace() == 20.0
    monkeypatch.setenv("HERMES_TUI_WS_ORPHAN_REAP_GRACE_S", "-5")
    assert server._resolve_ws_orphan_reap_grace() == 0.0
