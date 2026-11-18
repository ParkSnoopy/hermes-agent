"""A managed worker cannot receive detached completions after its turn exits."""


def test_registered_worker_process_cannot_receive_async_completions(monkeypatch):
    from agent import runtime_session_store
    from gateway.session_context import async_delivery_supported

    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    assert async_delivery_supported() is True
    monkeypatch.setattr(runtime_session_store, '_worker_process', True)
    assert async_delivery_supported() is False


def test_session_context_no_longer_bridges_scheduler_markers(monkeypatch):
    from gateway import session_context as context

    monkeypatch.setenv('HERMES_CRON_SESSION', '1')
    monkeypatch.setenv('HERMES_CRON_AUTO_DELIVER_CHAT_ID', 'old-chat')
    assert not any(name.startswith('HERMES_CRON_') for name in context._VAR_MAP)
    tokens = context.set_session_vars(platform='api_server', session_id='native', session_history_delivery='1')
    try:
        assert context.get_session_env('HERMES_SESSION_ID') == 'native'
        assert context.session_history_delivery_supported() is True
    finally:
        context.clear_session_vars(tokens)
    assert context.session_history_delivery_supported() is False
