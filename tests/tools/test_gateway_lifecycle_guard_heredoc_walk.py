"""The referenced-script walk sees the same inert-heredoc-masked view as the direct scan (#110422).

``_direct_lifecycle_scan`` masks provably-inert heredoc bodies (quoted delimiter, allowlisted
data consumer such as ``python3 - <<'PY'``), but ``_contains_unsafe_gateway_action`` walked
referenced scripts and ``sh -c`` payloads on the raw command, so a >1 MiB data path mentioned
inside the Python body failed closed as an oversized "script".
"""

import pytest

from tools.gateway_lifecycle_guard import contains_gateway_lifecycle_command_or_referenced_script as guard


@pytest.fixture(autouse=True)
def _materialize_mcp_sdk_symbols():
    """These scanner tests do not use the MCP SDK."""
    yield


def _big_file(tmp_path):
    path = tmp_path / "big_blob.bin"
    path.write_bytes(b"\0" * (2 * 1024 * 1024))
    return path


def test_inert_heredoc_body_path_not_walked_as_script(tmp_path):
    big = _big_file(tmp_path)
    command = f"python3 - <<'PY'\nfrom pathlib import Path\nprint(Path('{big}').stat().st_size)\nPY"
    assert guard(command, cwd=str(tmp_path)) is False


def test_prefixed_inert_heredoc_body_path_not_walked_as_script(tmp_path):
    big = _big_file(tmp_path)
    command = (
        f"cd {tmp_path} && python3 - <<'PY'\n"
        f"from pathlib import Path\nprint(Path('{big}').stat().st_size)\nPY"
    )
    assert guard(command, cwd=str(tmp_path)) is False


def test_allowlisted_name_function_heredoc_stays_visible(tmp_path):
    command = "python3() { bash; }; python3 <<'PY'\nhermes gateway restart\nPY"
    assert guard(command, cwd=str(tmp_path)) is True


def test_unquoted_heredoc_body_path_still_walked(tmp_path):
    """An expansion-capable body is not provably inert: the walk still sees it and fails closed."""
    big = _big_file(tmp_path)
    assert guard(f"cat > /tmp/x <<EOF\n{big}\nEOF", cwd=str(tmp_path)) is True


def test_inert_heredoc_body_script_path_still_read(tmp_path):
    """Masking hides the body from the *executed* view only: a lifecycle script named inside a
    Python body is still handed to ``os.system`` at runtime, so its contents must still be read."""
    script = tmp_path / "restart.sh"
    script.write_text("#!/bin/sh\nhermes gateway restart\n", encoding="utf-8")
    command = f"python3 - <<'PY'\nimport os\nos.system('{script}')\nPY"
    assert guard(command, cwd=str(tmp_path)) is True


def test_mentioned_data_file_that_cannot_be_scanned_is_not_a_verdict(tmp_path, monkeypatch):
    """A file only MENTIONED in an inert body may exhaust the text budget (one >64 KiB line), pull
    in 64+ remote-read misses (a markdown table of paths) or be a live SQLite database: each is
    "nothing to scan", never a block (#113944). The same file *executed* still fails closed."""
    from tools import gateway_lifecycle_guard as lifecycle_guard
    from hermes_cli.sqlite_safe_read import connect_tracked

    minified = tmp_path / "minified.json"
    minified.write_text("[" + "1," * 40000 + "1]", encoding="utf-8")
    notes = tmp_path / "notes.md"
    notes.write_text("\n".join(f"| /opt/frag{i}/tool-{i}.md | note |" for i in range(70)), encoding="utf-8")
    db = tmp_path / "state.db"
    conn = connect_tracked(db)
    try:
        for data in (minified, notes, db):
            command = f"cd {tmp_path} && python3 - <<'PY'\nt = open('{data}', encoding='utf-8').read()\nPY"
            assert guard(command, cwd=str(tmp_path)) is False, data.name
        unsafe, refusal = lifecycle_guard.scan_gateway_lifecycle(f"bash {minified}")
        assert unsafe is True and "budget" in refusal
        unsafe, refusal = lifecycle_guard.scan_gateway_lifecycle(f"bash {db}")
        assert unsafe is True and "SQLite" in refusal
    finally:
        conn.close()


def test_mentioned_script_with_lifecycle_command_still_blocks(tmp_path):
    """The lenient path only covers "could not scan": a mentioned script whose text IS a lifecycle
    command is still a positive verdict, and the refusal reason stays empty (it is not a scan failure)."""
    from tools import gateway_lifecycle_guard as lifecycle_guard

    script = tmp_path / "restart.sh"
    script.write_text("#!/bin/sh\nhermes gateway restart\n", encoding="utf-8")
    command = f"cd {tmp_path} && python3 - <<'PY'\nimport os\nos.system('{script}')\nPY"
    assert lifecycle_guard.scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (True, None)



