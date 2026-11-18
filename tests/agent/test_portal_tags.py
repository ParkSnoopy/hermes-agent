"""Conversation routing context and Portal affinity."""

import contextvars
from concurrent.futures import ThreadPoolExecutor

from agent.portal_tags import (
    get_affinity_scope,
    get_conversation_context,
    reset_affinity_scope,
    reset_conversation_context,
    set_affinity_scope,
    set_conversation_context,
)


def test_ambient_context_set_none_clears():
    for empty in (None, ""):
        token = set_conversation_context(empty)
        try:
            assert get_conversation_context() is None
        finally:
            reset_conversation_context(token)


def test_ambient_context_isolated_between_contexts():
    def in_conversation(cid):
        set_conversation_context(cid)
        return get_conversation_context()

    assert contextvars.copy_context().run(in_conversation, "agent-a") == "agent-a"
    assert contextvars.copy_context().run(in_conversation, "agent-b") == "agent-b"
    assert get_conversation_context() is None


def test_ambient_context_propagates_via_thread_context_helper():
    from tools.thread_context import propagate_context_to_thread

    token = set_conversation_context("moa-root")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            plain = executor.submit(get_conversation_context).result()
            propagated = executor.submit(
                propagate_context_to_thread(get_conversation_context)
            ).result()
    finally:
        reset_conversation_context(token)
    assert plain is None
    assert propagated == "moa-root"


def test_nous_sticky_key_uses_conversation_root():
    from providers import get_provider_profile

    profile = get_provider_profile("nous")
    token = set_conversation_context("root-conversation")
    try:
        body = profile.build_extra_body(session_id="segment-after-compaction")
        assert body["session_id"] == "root-conversation"
        assert "tags" not in body
        aux = profile.build_extra_body()
        assert aux["session_id"] == "root-conversation"
        assert "tags" not in aux
    finally:
        reset_conversation_context(token)


def test_nous_sticky_key_prefers_declared_scope():
    from providers import get_provider_profile

    profile = get_provider_profile("nous")
    root = set_conversation_context("root-conversation")
    scope = set_affinity_scope("declared-conversation")
    try:
        body = profile.build_extra_body(session_id="segment")
        assert body["session_id"] == "declared-conversation"
        assert "tags" not in body
        assert get_affinity_scope() == "declared-conversation"
    finally:
        reset_affinity_scope(scope)
        reset_conversation_context(root)


def test_compress_context_preserves_ambient_context(monkeypatch):
    import agent.conversation_compression as cc
    from run_agent import AIAgent

    seen = {}

    def fake_compress(agent, messages, system_message, **kwargs):
        seen["conversation"] = get_conversation_context()
        return ([], "")

    monkeypatch.setattr(cc, "compress_context", fake_compress)

    class Agent:
        def _conversation_root_id(self):
            return "segment-after-compaction"

    token = set_conversation_context("outer-root")
    try:
        AIAgent._compress_context(Agent(), [], "sys")
        assert seen["conversation"] == "outer-root"
        assert get_conversation_context() == "outer-root"
    finally:
        reset_conversation_context(token)
