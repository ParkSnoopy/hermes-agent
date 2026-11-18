"""Subagent steering and interruption are scoped to their owning authority session."""
import asyncio

from tests.gateway.fixtures.subagent_controls_peer import probe


def test_subagent_steer_and_stop_reach_only_this_sessions_children(tmp_path, monkeypatch):
    home, user = tmp_path / 'state', tmp_path / 'user'
    home.mkdir()
    user.mkdir()
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.setenv('HERMES_HOME', str(home))
    receipt = asyncio.run(probe(home))
    assert receipt['owned'] == {'steer': {'status': 'queued', 'subagent_id': 'owned', 'text': 'check owned'},
                                'stop': {'found': True, 'subagent_id': 'owned'}}, receipt
    assert receipt['sibling'] == {'steer': {'status': 'rejected', 'subagent_id': 'sibling', 'text': 'check sibling'},
                                  'stop': {'found': False, 'subagent_id': 'sibling'}}, receipt
    assert receipt['delivered'] == {'owned': [['check owned'], 1], 'sibling': [[], 0]}, receipt
    assert receipt['late']['status'] == 'rejected', receipt
