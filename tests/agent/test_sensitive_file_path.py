from pathlib import Path, PurePosixPath

import pytest

from agent.file_safety import is_sensitive_file_path
from hermes_cli.home_data_layout import SECRET_CACHE_STORES


@pytest.mark.parametrize("path", [
    ".ENV", ".EnV.local", ".envrc", "AUTH.JSON", ".git-credentials",
    "a/MCP-TOKENS/server.json", "a/PaIrInG/data", "webhook_subscriptions.json",
    *(PurePosixPath(store).name for store in SECRET_CACHE_STORES),
])
def test_sensitive_paths(path):
    assert is_sensitive_file_path(Path(path))


@pytest.mark.parametrize("path", ["notes.txt", ".environment", "pairings/file", "a/config.yml"])
def test_unrelated_paths(path):
    assert not is_sensitive_file_path(Path(path))