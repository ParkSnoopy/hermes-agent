"""``post_compaction`` fires once per committed compaction, never for an attempt that changed nothing,
and a raising plugin never changes what the compaction returns (#118382)."""

from unittest.mock import Mock, patch
from types import SimpleNamespace

import threading

import pytest

from hermes_cli import plugins as plugins_mod
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from agent.context_compressor import ContextCompressor
from agent.transports.codex_app_server_session import TurnResult
from agent.conversation_compression import compress_context


class _TodoStore:
    def format_for_injection(self):
        return ""


class _Agent:
    def __init__(self, compressor):
        self.context_compressor = compressor
        self.session_id = "session-events-test"
        self.platform = "cli"
        self.model = "test/main-model"
        self.provider = "test-provider"
        self.tools = []
        self._compression_feasibility_checked = True
        self.compression_in_place = False
        self._memory_manager = None
        self._session_db = None
        self._todo_store = _TodoStore()
        self._cached_system_prompt = None

    def _emit_status(self, _message):
        pass

    def _emit_warning(self, _message):
        pass

    def _invalidate_system_prompt(self):
        self._cached_system_prompt = None

    def _build_system_prompt(self, system_message):
        return system_message

    def commit_memory_session(self, _messages):
        pass


def _compressor(**kwargs):
    with patch("agent.context_compressor.get_model_context_length", return_value=100_000):
        compressor = ContextCompressor(
            model="test/main-model", provider="test-provider", threshold_percent=0.50, quiet_mode=True,
            config_context_length=100_000, **kwargs,
        )
    compressor.tail_token_budget = 10
    return compressor


def _messages():
    filler = " ".join(["context"] * 200)
    msgs = [{"role": "system", "content": "system prompt"}]
    for idx in range(10):
        msgs.append({"role": "user", "content": f"user message {idx} {filler}"})
        msgs.append({"role": "assistant", "content": f"assistant reply {idx} {filler}"})
    return msgs




@pytest.fixture
def ctx(monkeypatch):
    mgr = PluginManager()
    mgr._discovered = True
    monkeypatch.setattr(plugins_mod, "_plugin_manager", mgr)
    monkeypatch.setattr(plugins_mod, "_plugin_managers_by_home", {})
    return PluginContext(PluginManifest(name="compaction-observer", source="user"), mgr)


@pytest.mark.parametrize("summary, committed", [("SANITIZED SUMMARY", True), (None, False)])
def test_post_compaction_fires_once_per_committed_compaction(ctx, summary, committed):
    fired = []

    def boom(**_kw):
        raise RuntimeError("plugin bug")

    ctx.register_hook("post_compaction", lambda **kw: fired.append(kw))
    ctx.register_hook("post_compaction", boom)
    agent = _Agent(_compressor(abort_on_summary_failure=True))

    with patch.object(agent.context_compressor, "_generate_summary", return_value=summary):
        compressed, _ = compress_context(agent, _messages(), "system prompt", approx_tokens=80_000, trigger="pre_api")

    assert (len(compressed) < len(_messages())) is committed
    if not committed:
        assert fired == []
        return
    [kw] = fired
    assert (kw["session_id"], kw["old_session_id"], kw["kind"], kw["reason"]) == (
        "session-events-test", "", "summarize", "pre_api",
    )
    assert kw["messages_removed"] == len(_messages()) - len(compressed) > 0
    assert kw["before_tokens"] > kw["after_tokens"] > 0


@pytest.mark.parametrize("case", ["success", "failure", "noop", "detached"])
def test_provider_compaction_hook_only_for_live_commits(ctx, case):
    fired = []
    ctx.register_hook("post_compaction", lambda **kw: fired.append(kw))
    result = TurnResult(thread_id="t1", turn_id="c1", error="boom" if case == "failure" else None)
    compact = Mock(return_value=result)
    agent = SimpleNamespace(
        api_mode="codex_app_server", codex_app_server_auto_compaction="native" if case == "noop" else "hermes",
        session_id="codex-session", _persist_disabled=case == "detached", _cached_system_prompt="cached",
        _codex_session=SimpleNamespace(compact_thread=compact, close=Mock()),
        _compression_activity_heartbeat_interval=0.1,
        context_compressor=SimpleNamespace(compression_count=0, last_compression_rough_tokens=0,
                                          last_prompt_tokens=1, last_completion_tokens=0,
                                          awaiting_real_usage_after_compression=False),
        _touch_activity=Mock(), _emit_status=Mock(), _emit_warning=Mock(),
    )
    messages = [{"role": "user", "content": "question"}]
    returned, _ = compress_context(agent, messages, "system", force=case != "noop", trigger="manual")
    assert returned is messages
    assert compact.call_count == (case != "noop")
    if case == "success":
        [kw] = fired
        assert (kw["session_id"], kw["kind"], kw["reason"]) == ("codex-session", "provider_native", "manual")
        assert kw["messages_removed"] is None
    else:
        assert fired == []


def test_proactive_prune_notifies_plugin(ctx):
    from agent.turn_preflight import compress_after_tool_results

    fired = []
    ctx.register_hook("post_compaction", lambda **kw: fired.append(kw))
    compressor = _compressor(proactive_prune_tokens=48_000, proactive_prune_min_result_chars=8_000,
                             protect_first_n=2, protect_last_n=4)
    compressor.last_prompt_tokens = 120_000
    agent = SimpleNamespace(context_compressor=compressor, compression_enabled=True, session_id="prune-session",
                            tools=[], _persist_disabled=False, _usage_anchor=None, _compression_feasibility_checked=True,
                            _warn_context_overflow_blocked=lambda *_args: None)
    messages = [{"role": "system", "content": "sys"}]
    for i in range(8):
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 20_000 if i < 3 else "ok"})
    verdict = compress_after_tool_results(
        agent, messages=messages, system_message="sys", user_message="ask", active_system_prompt="sys",
        conversation_history=None, compression_attempts=3, max_compression_attempts=3, effective_task_id="task",
        final_response=None, turn_exit_reason=None, current_turn_user_idx=0,
    )
    assert verdict.messages is not messages
    [kw] = fired
    assert (kw["session_id"], kw["kind"], kw["reason"]) == ("prune-session", "prune_tool_results", "proactive_prune")
    assert kw["before_tokens"] > kw["after_tokens"]


def test_micro_compaction_notifies_plugin(ctx):
    from tests.agent.test_micro_compaction import _compressor as micro_compressor, _conversation

    fired = []
    ctx.register_hook("post_compaction", lambda **kw: fired.append(kw))
    compressor = micro_compressor()
    compressor._session_id = "micro-session"
    messages = _conversation()
    result = compressor._micro_compact(list(messages))
    assert result != messages
    assert any("ROLLING SUMMARY" in str(message.get("content")) for message in result)
    [kw] = fired
    assert (kw["session_id"], kw["kind"], kw["reason"]) == ("micro-session", "micro_summarize", "between_turns")
    assert kw["messages_removed"] == len(messages) - len(result)


def test_a_slow_callback_for_one_session_does_not_skip_another_sessions_compaction(ctx):
    """The bounded dispatcher skips a callback still running for the same call; two sessions are two calls."""
    entered, release, seen = threading.Event(), threading.Event(), []

    def slow(session_id, **_kw):
        seen.append(session_id)
        if session_id == "a":
            entered.set()
            release.wait(10)

    ctx.register_hook("post_compaction", slow)
    first = threading.Thread(target=ctx._manager.invoke_hook, args=("post_compaction",), kwargs={"session_id": "a"})
    first.start()
    assert entered.wait(10)
    try:
        ctx._manager.invoke_hook("post_compaction", session_id="b")
    finally:
        release.set()
        first.join(10)

    assert sorted(seen) == ["a", "b"]
