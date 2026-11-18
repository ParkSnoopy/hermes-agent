"""Security boundary tests for MCP catalog credential writes and child environments."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import hermes_yaml as yaml



@pytest.fixture
def catalog_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolate_hermes_home):
    """Install one synthetic API-key catalog entry in the isolated test home."""
    from hermes_constants import get_hermes_home
    from hermes_cli.config import invalidate_env_cache

    catalog = tmp_path / "optional-mcps"
    entry_dir = catalog / "demo"
    entry_dir.mkdir(parents=True)
    (entry_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "manifest_version": 1,
                "name": "demo",
                "description": "Synthetic catalog boundary fixture",
                "source": "https://example.test/demo",
                "transport": {
                    "type": "stdio",
                    "command": "demo-mcp",
                },
                "auth": {
                    "type": "api_key",
                    "env": [
                        {
                            "name": "DEMO_API_KEY",
                            "prompt": "Demo API key",
                            "secret": True,
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_OPTIONAL_MCPS", str(catalog))
    invalidate_env_cache()
    return get_hermes_home()


def test_catalog_rejects_undeclared_key_before_any_write_or_install(
    catalog_env: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from hermes_cli import mcp_config

    installs: list[str] = []
    monkeypatch.setattr(
        mcp_config,
        "_probe_single_server",
        lambda name, config: installs.append(name),
    )

    from tools.connectors.mcp import _CatalogBackend

    with pytest.raises(ValueError) as exc:
        _CatalogBackend().install("demo", {
            "DEMO_API_KEY": "valid-demo-value", "UNRELATED_SETTING": "must-not-land",
        })
    detail = str(exc.value)
    assert "UNRELATED_SETTING" in detail
    assert "valid-demo-value" not in detail
    assert "must-not-land" not in detail
    assert installs == []
    env_path = catalog_env / ".env"
    assert not env_path.exists() or env_path.read_text(encoding="utf-8") == ""


def test_catalog_cannot_declare_reserved_control_key(
    catalog_env: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from hermes_cli import mcp_config

    catalog_root = Path(os.environ["HERMES_OPTIONAL_MCPS"])
    manifest_path = catalog_root / "demo" / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["auth"]["env"].append(
        {
            "name": "HERMES_YOLO_MODE",
            "prompt": "Unsafe control",
            "secret": False,
        }
    )
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    installs: list[str] = []
    monkeypatch.setattr(
        mcp_config,
        "_probe_single_server",
        lambda name, config: installs.append(name),
    )

    from tools.connectors.mcp import _CatalogBackend

    with pytest.raises(ValueError, match="denylist"):
        _CatalogBackend().install("demo", {"HERMES_YOLO_MODE": "1"})
    assert installs == []
    env_path = catalog_env / ".env"
    assert not env_path.exists() or "HERMES_YOLO_MODE" not in env_path.read_text(
        encoding="utf-8"
    )


def test_catalog_accepts_declared_credential(
    catalog_env: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from hermes_cli import mcp_config
    from agent.secret_scope import get_secret
    from tools.connectors.mcp import _CatalogBackend

    probes: list[str] = []

    def probe(name, cfg, **_kwargs):
        # The credential is in scope for the probe, and nothing is saved before it answers.
        assert get_secret("DEMO_API_KEY") == "valid-demo-value"
        assert not (catalog_env / ".env").exists()
        assert "demo" not in mcp_config._get_mcp_servers()
        probes.append(name)
        return [("demo_tool", "")]

    monkeypatch.setattr(mcp_config, "_probe_single_server", probe)

    assert _CatalogBackend().install(
        "demo", {"DEMO_API_KEY": "valid-demo-value"}
    ) == ["demo_tool"]
    assert probes == ["demo"]
    assert "demo" in mcp_config._get_mcp_servers()
    assert "DEMO_API_KEY=valid-demo-value" in (
        catalog_env / ".env"
    ).read_text(encoding="utf-8")


def test_failed_catalog_probe_does_not_persist_credentials_or_config(catalog_env, monkeypatch):
    from hermes_cli import mcp_config
    from tools.connectors.mcp import _CatalogBackend

    def fail(*args, **kwargs):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(mcp_config, "_probe_single_server", fail)
    with pytest.raises(RuntimeError, match="probe failed"):
        _CatalogBackend().install("demo", {"DEMO_API_KEY": "valid-demo-value"})
    assert not (catalog_env / ".env").exists()
    assert "demo" not in mcp_config._get_mcp_servers()


def test_stdio_environment_filters_ambient_secrets_and_keeps_explicit_values(catalog_env, monkeypatch):
    from tools.mcp_tool_config import _build_safe_env

    monkeypatch.setenv("UNRELATED_PRIVATE_TOKEN", "ambient-secret")
    monkeypatch.setenv("XDG_TEST_SETTING", "safe-setting")
    child = _build_safe_env({"DEMO_API_KEY": "explicit-secret"})
    assert "UNRELATED_PRIVATE_TOKEN" not in child
    assert child["DEMO_API_KEY"] == "explicit-secret"
    assert child["XDG_TEST_SETTING"] == "safe-setting"
    assert child["HERMES_HOME"] == str(catalog_env)


def test_catalog_non_secret_env_never_lands_in_env_file(
    catalog_env: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Non-secret declared env vars (e.g. a base URL) are not written to .env:
    install_entry inlines them into the server config instead."""
    from hermes_cli import mcp_config
    from tools.connectors.mcp import _CatalogBackend

    catalog_root = Path(os.environ["HERMES_OPTIONAL_MCPS"])
    manifest_path = catalog_root / "demo" / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["auth"]["env"].append(
        {
            "name": "DEMO_BASE_URL",
            "prompt": "Demo base URL",
            "secret": False,
        }
    )
    # The transport references the non-secret var; install_entry inlines it.
    # (HTTP transport so the var lands in the server url.)
    manifest["transport"] = {"type": "http", "url": "${DEMO_BASE_URL}"}
    manifest["auth"]["type"] = "api_key"
    manifest["auth"]["env"] = [
        {"name": "MCP_DEMO_API_KEY", "prompt": "Demo API key", "secret": True},
        {"name": "DEMO_BASE_URL", "prompt": "Demo base URL", "secret": False},
    ]
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr(mcp_config, "_probe_single_server", lambda *a, **k: [("demo_tool", "")])
    assert _CatalogBackend().install("demo", {
        "MCP_DEMO_API_KEY": "valid-demo-value", "DEMO_BASE_URL": "https://demo.example.test",
    }) == ["demo_tool"]
    env_text = (catalog_env / ".env").read_text(encoding="utf-8")
    assert "MCP_DEMO_API_KEY=valid-demo-value" in env_text
    assert "DEMO_BASE_URL" not in env_text
    assert "https://demo.example.test" not in env_text
    # The non-secret is inlined into config.yaml (server config carries the
    # literal; the raw file never stores it and never keeps a ${VAR} ref).
    from hermes_cli.config import load_config

    server = load_config()["mcp_servers"]["demo"]
    assert server["url"] == "https://demo.example.test"
    assert "${DEMO_BASE_URL}" not in (
        catalog_env / "config.yaml"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "protected_key",
    [
        "HERMES_YOLO_MODE",
        "HERMES_OPTIONAL_MCPS",
        "HERMES_COPILOT_ACP_COMMAND",
        "HERMES_COPILOT_ACP_ARGS",
    ],
)
def test_env_writer_rejects_protected_key(
    catalog_env: Path,
    protected_key: str,
):
    from hermes_cli.config import save_env_value

    with pytest.raises(ValueError, match="denylist"):
        save_env_value(protected_key, "must-not-land")
    env_path = catalog_env / ".env"
    assert not env_path.exists() or protected_key not in env_path.read_text(
        encoding="utf-8"
    )


def test_process_supplied_catalog_root_remains_supported(catalog_env: Path):
    from hermes_cli.mcp_catalog import get_entry

    assert get_entry("demo") is not None


def _acp_launch_resolvers(monkeypatch: pytest.MonkeyPatch):
    """(command, args) resolvers of an external-process provider's env-driven launch fallback.

    The profile is registered like a plugin's, so its launch env names are executable authority."""
    import providers
    from agent.acp_client import ACPClient
    from providers.base import ProviderProfile

    acme = ProviderProfile(name="acme-acp", auth_type="external_process", base_url="acp://acme",
                           process_command="acme", process_command_env_vars=("HERMES_ACME_ACP_COMMAND",),
                           process_args_env_var="HERMES_ACME_ACP_ARGS")
    real_list = providers.list_providers
    monkeypatch.setattr(providers, "list_providers", lambda: [*real_list(), acme])

    class _AcmeACPClient(ACPClient):
        default_command = "acme"
        default_args = ("--acp", "--stdio")
        command_env_vars = acme.process_command_env_vars
        args_env_var = acme.process_args_env_var

    def launch():
        return _AcmeACPClient(acp_cwd=os.getcwd())

    return (lambda: launch()._acp_command), (lambda: launch()._acp_args)


def test_rejected_acp_launch_controls_do_not_change_live_resolvers(
    catalog_env: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from hermes_cli.config import save_env_value
    _resolve_command, _resolve_args = _acp_launch_resolvers(monkeypatch)

    monkeypatch.setenv("HERMES_ACME_ACP_COMMAND", "/opt/trusted/acme")
    monkeypatch.setenv("HERMES_ACME_ACP_ARGS", "--acp --stdio")
    expected_command = _resolve_command()
    expected_args = _resolve_args()

    attempts = {
        "HERMES_ACME_ACP_COMMAND": "/tmp/attacker-command",
        "HERMES_ACME_ACP_ARGS": "--malicious-transport",
    }
    for key, value in attempts.items():
        with pytest.raises(ValueError, match="denylist"):
            save_env_value(key, value)

    assert _resolve_command() == expected_command
    assert _resolve_args() == expected_args
    env_path = catalog_env / ".env"
    if env_path.exists():
        env_text = env_path.read_text(encoding="utf-8")
        assert "/tmp/attacker-command" not in env_text
        assert "--malicious-transport" not in env_text


def test_preexisting_acp_launch_controls_remain_usable(
    catalog_env: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _resolve_command, _resolve_args = _acp_launch_resolvers(monkeypatch)
    from hermes_cli.env_loader import load_hermes_dotenv

    monkeypatch.setenv("HERMES_ACME_ACP_COMMAND", "parent-placeholder")
    monkeypatch.setenv("HERMES_ACME_ACP_ARGS", "--parent-placeholder")
    (catalog_env / ".env").write_text(
        "HERMES_ACME_ACP_COMMAND=/opt/operator/acme\n"
        "HERMES_ACME_ACP_ARGS=--acp --stdio --operator-mode\n",
        encoding="utf-8",
    )

    load_hermes_dotenv(
        hermes_home=catalog_env,
        load_external_secrets=False,
    )

    assert _resolve_command() == "/opt/operator/acme"
    assert _resolve_args() == ["--acp", "--stdio", "--operator-mode"]


def test_connection_card_install_keeps_env_file_secrets_only(
    catalog_env: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The connector-card backend (Desktop/TUI/CLI setup card) makes the same secrets-only split
    as the terminal install: a declared non-secret lands in the server block, never in .env."""
    from hermes_cli import mcp_config
    from tools.connectors.mcp import _CatalogBackend

    catalog_root = Path(os.environ["HERMES_OPTIONAL_MCPS"])
    manifest_path = catalog_root / "demo" / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["transport"]["env"] = {"DEMO_BASE_URL": "${DEMO_BASE_URL}"}
    manifest["auth"]["env"] = [
        {"name": "DEMO_API_KEY", "prompt": "Demo API key", "secret": True},
        {"name": "DEMO_BASE_URL", "prompt": "Demo base URL", "secret": False},
    ]
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr(mcp_config, "_probe_single_server", lambda name, cfg, **_k: [("demo_tool", "")])

    _CatalogBackend().install(
        "demo", {"DEMO_API_KEY": "valid-demo-value", "DEMO_BASE_URL": "https://demo.example.test"}
    )

    env_text = (catalog_env / ".env").read_text(encoding="utf-8")
    assert "DEMO_API_KEY=valid-demo-value" in env_text
    assert "DEMO_BASE_URL" not in env_text and "https://demo.example.test" not in env_text
    assert mcp_config._get_mcp_servers()["demo"]["env"]["DEMO_BASE_URL"] == "https://demo.example.test"
