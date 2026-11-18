"""Runtime identity contracts for ACP's human and machine surfaces."""

from types import SimpleNamespace
from typing import cast

import pytest

from acp_adapter import entry
from acp_adapter.server import HermesACPAgent
from acp_adapter.session import SessionState
from hermes_cli.version_info import VersionInfo


def test_version_flag_displays_derived_version(monkeypatch, capsys):
    monkeypatch.setattr(
        "hermes_cli.version_info.get_version_info",
        lambda: SimpleNamespace(base_version="1.2.3", derived_version="1.2.3+4.gabcdef0"),
    )

    entry.main(["--version"])

    assert capsys.readouterr().out == "1.2.3+4.gabcdef0\n"


def test_version_slash_command_displays_release_and_short_commit(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.version_info.get_version_info",
        lambda: VersionInfo("1.2.3", "1.2.3+4.gabcdef0", 4, "abcdef0" + "1" * 33, None, "git"),
    )

    agent = object.__new__(HermesACPAgent)
    state = cast(SessionState, SimpleNamespace(cwd="."))
    assert agent._handle_slash_command("/version", state) == "Hermes Agent version v1.2.3 (abcdef0)"


@pytest.mark.asyncio
async def test_initialize_advertises_base_version(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.version_info.get_version_info",
        lambda: SimpleNamespace(base_version="1.2.3", derived_version="1.2.3+4.gabcdef0"),
    )

    response = await object.__new__(HermesACPAgent).initialize()

    assert response.agent_info is not None
    assert response.agent_info.version == "1.2.3"
