"""Registry dispatch forwards browser execution arguments without extra work."""


def test_browser_exec_handler_forwards_arguments(monkeypatch):
    from tools import browser_use_cli

    calls = []
    monkeypatch.setattr(browser_use_cli, "browser_exec", lambda **kwargs: calls.append(kwargs) or "result")
    result = browser_use_cli._handle_browser_exec(
        {"code": "print(1)", "session": "work", "timeout_s": 7, "local": True}, task_id="task")
    assert result == "result"
    assert calls == [{"code": "print(1)", "session": "work", "timeout_s": 7, "local": True, "task_id": "task"}]
