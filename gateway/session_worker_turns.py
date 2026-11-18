"""Run a managed session turn with its frozen workspace and surface scope."""
import contextlib


@contextlib.contextmanager
def _worker_workspace(frame):
    """Bind the session's task identity, route and frozen workspace for tool execution."""
    session_id = (frame.get('scope') or {}).get('session_id')
    if not session_id:
        yield None
        return
    from gateway.session_authorities import served_profile_name
    from gateway.session_context import clear_session_vars, set_session_vars
    from hermes_state_keys import profile_from_session_key
    route = frame.get('route') or session_id
    profile = served_profile_name(frame.get('home'))
    tokens = set_session_vars(session_key=route, session_id=session_id,
                              profile=profile if profile_from_session_key(route) == profile else '')
    try:
        yield session_id
    finally:
        clear_session_vars(tokens)


def run_worker_turns(agent, frame, history):
    from gateway.session_managed_worker import worker_turn_scope
    from gateway.session_surface import arm_surface_turn
    from gateway.session_display import worker_display_kwargs
    with worker_turn_scope(frame):
        arm_surface_turn(agent)
        author = frame.get('turn_author')
        with _worker_workspace(frame) as task_id:
            return agent.run_conversation(frame['text'], conversation_history=history, **worker_display_kwargs(),
                                          **({'task_id': task_id} if task_id else {}),
                                          **({'turn_author': author} if author is not None else {}))
