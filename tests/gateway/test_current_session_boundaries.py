"""Current local lineage and frozen credential authority boundaries."""
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from gateway.session_contract import SessionRef
from gateway.session_local_route import resolve_local_target
from gateway.session_policy_credentials import _binding, _check_scope
from hermes_state_runtime import RuntimeStoreError


def test_local_segment_resolves_only_through_its_receipt():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, chat_id TEXT)')
    conn.execute('CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT)')
    conn.executemany('INSERT INTO sessions VALUES (?, ?)', [('local-root', 'local-root'), ('segment', 'local-root')])
    from hermes_state_local import POLICY_PREFIX
    conn.execute('INSERT INTO state_meta VALUES (?, ?)',
                 (POLICY_PREFIX + 'local-root', json.dumps({'lineage': ['local-root', 'segment']})))

    @contextmanager
    def read():
        yield conn

    class DB:
        def get_session(self, sid):
            row = conn.execute('SELECT chat_id FROM sessions WHERE id=?', (sid,)).fetchone()
            return {'chat_id': row[0]} if row else None

        _read_ctx = staticmethod(read)

    calls = []
    authority = SimpleNamespace(db=DB(), profile_id='profile', authorize=lambda *args: calls.append(args))
    actor = object()
    ref = resolve_local_target(authority, actor, 'segment')
    assert ref == SessionRef('profile', 'local-root')
    assert calls == [(actor, ref, 'session:read')]
    conn.execute('UPDATE state_meta SET value=?', (json.dumps({'lineage': ['local-root']}),))
    with pytest.raises(RuntimeStoreError) as exc:
        resolve_local_target(authority, actor, 'segment')
    assert exc.value.reason == 'not_found'
    conn.close()


def test_current_credential_reference_requires_endpoint_binding(tmp_path):
    config = {'model': {'provider': 'custom', 'base_url': 'https://example.test'}}
    policy = SimpleNamespace(config_json=json.dumps(config))
    authority = SimpleNamespace(profile_id='profile')
    path = ['model', 'api_key']
    source = {'home': str(tmp_path), 'profile': 'profile', 'bound': [[path, _binding(config, path)]]}
    _check_scope(authority, source, policy, tmp_path, ())
    changed = SimpleNamespace(config_json=json.dumps({'model': {'provider': 'custom', 'base_url': 'https://elsewhere.test'}}))
    with pytest.raises(RuntimeStoreError) as exc:
        _check_scope(authority, source, changed, tmp_path, ())
    assert exc.value.reason == 'launch_credentials_unavailable'
    del source['bound']
    with pytest.raises(KeyError):
        _check_scope(authority, source, policy, tmp_path, ())


def test_inventory_groups_tools_from_registry(monkeypatch):
    from gateway.session_local_inventory import _grouped
    from tools.registry import registry

    monkeypatch.setattr(registry, 'get_toolset_for_tool', lambda name: {'search': 'web'}.get(name))
    tools = [{'function': {'name': 'search'}}, {'function': {'name': 'unknown'}}]
    assert _grouped(tools) == {'web': ['search'], 'other': ['unknown']}
