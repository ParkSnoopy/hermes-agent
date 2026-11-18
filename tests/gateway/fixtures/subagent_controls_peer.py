"""Per-child steering and interruption reach only this authority session's children."""
import weakref


async def probe(home):
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from tools.delegate_tool_child_run import _register_child
    from tools.delegate_tool_registry import _close_subagent_steering, _unregister_subagent

    (home / 'config.yaml').write_text('gateway:\n  multiplex_profiles: false\nmodel:\n  provider: custom\n  default: no-inference\n  base_url: http://127.0.0.1:1/v1\nauxiliary:\n  title_generation:\n    enabled: false\n')
    runner = GatewayRunner()
    authority = await initialize_session_authority(runner, profile_id=str(home), instance_id='subagent-controls')
    connection = AuthorityConnection(authority, object(), {'user_id': 'owner'}, operator=True)
    receipt = {}

    async def rpc(method, **params):
        return await connection.dispatch({'jsonrpc': '2.0', 'id': method,
                                          'method': method, 'params': params})

    class Agent:
        def __init__(self):
            self.steered, self.stopped = [], []

        def steer(self, text):
            self.steered.append(text)
            return True

        def hard_interrupt(self, message=None):
            self.stopped.append(message)
            return True

    try:
        created = await rpc('session.create', request_id='controls-owner', source='tui', toolsets=[])
        sid = created['result']['session_id']
        live = authority.sessions[sid]
        parent, other_parent = Agent(), Agent()
        parent.session_id, other_parent.session_id = sid, 'foreign-session'
        with runner._agent_cache_lock:
            runner._agent_cache[live.route] = (parent, 'fixture')
        children = {}
        try:
            for name, ancestor in [('owned', parent), ('sibling', other_parent)]:
                child = children[name] = Agent()
                child._subagent_id = name
                child._delegate_parent_ref = weakref.ref(ancestor)
                child._parent_session_id = ancestor.session_id
                _register_child(child, ancestor, name + '-goal', owner_session_id=None,
                                owner_transport=None, owner_session_record=None)
            # Both operations must enforce the child's membership in this session.
            for name in ('owned', 'sibling'):
                steer = await rpc('subagent.steer', session_id=sid, subagent_id=name, text='check ' + name)
                stop = await rpc('subagent.interrupt', session_id=sid, subagent_id=name)
                receipt[name] = {'steer': steer.get('result', steer.get('error')),
                                 'stop': stop.get('result', stop.get('error'))}
            receipt['delivered'] = {name: [child.steered, len(child.stopped)] for name, child in children.items()}
            # A child past its final steer drain refuses instead of dropping the text.
            _close_subagent_steering('owned', children['owned'])
            late = await rpc('subagent.steer', session_id=sid, subagent_id='owned', text='too late')
            receipt['late'] = late.get('result', late.get('error'))
        finally:
            for name, child in children.items():
                _unregister_subagent(name, agent=child)
    finally:
        await connection.close()
        runner.session_store.close_all_db_handles()
    return receipt
