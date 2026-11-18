"""Session-scoped process, MCP and attachment controls reach the shared owner."""
import json
import os
from pathlib import Path
import subprocess
import sys

def test_process_kill_stop_and_mcp_reload_reach_only_this_session(tmp_path):
    """``process.kill`` stops this chat's own process and refuses another chat's; ``process.stop``
    with the resolved session id stops the rest of this chat's; ``reload.mcp`` is served (a
    profile-scoped reconcile)."""
    root = Path(__file__).resolve().parents[2]
    home, user = tmp_path / 'state', tmp_path / 'user'
    home.mkdir()
    user.mkdir()
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'LANG', 'TZ') if k in os.environ}
    env.update(HOME=str(user), USERPROFILE=str(user), HERMES_HOME=str(home),
               HERMES_RUNTIME_DIR=str(runtime), TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path),
               XDG_CACHE_HOME=str(tmp_path / 'cache'), PYTHONPATH=str(root), PYTHONUNBUFFERED='1')
    result = subprocess.run([sys.executable, str(root / 'tests/gateway/fixtures/desktop_process_verbs_peer.py')],
                            cwd=root, env=env, capture_output=True, text=True, timeout=90, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((home / 'receipt.json').read_text())
    assert receipt['kill'] == 'killed', receipt
    # A foreign process must not be exposed as belonging to this session.
    assert receipt['foreign_kill'] == 'process_not_found', receipt
    assert receipt['stop'] == {'killed': 1, 'delegations': 0}, receipt
    assert receipt['list_fields'] == ['completion_reason', 'exited_at', 'started_epoch'], receipt
    assert receipt['list_ids'] is True, receipt
    # tools.show is served for an owner session (the classic CLI's /tools), not -32601.
    assert set(receipt['tools_show']) == {'sections', 'total'}, receipt
    assert receipt['tools_show']['total'] == sum(len(s['tools']) for s in receipt['tools_show']['sections']), receipt
    # Scoped to THIS session's toolsets (created with toolsets=[]), not the launch profile's catalog.
    assert receipt['tools_show']['total'] == 0, receipt
    assert receipt['skills_reload'] == ['zeta-skill'], receipt
    assert receipt['exited'] == [True, True, False], receipt
    assert receipt['reload_mcp'] == 'reloaded', receipt
    # Both path and byte attachments are staged for the owner session.
    assert [ref.startswith('@file:') for ref in receipt['file_attach']] == [True, True], receipt
    assert receipt['staged'] == ['hello', 'hi'], receipt
