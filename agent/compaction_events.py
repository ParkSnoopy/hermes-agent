"""Post-compaction plugin notification; no event recording or export."""

from __future__ import annotations

import copy
import logging


def notify_compaction(
    *, session_id: str, kind: str, reason: str, messages_before: list | None = None,
    messages_after: list | None = None, old_session_id: str = "", verbatim_tail: list | None = None,
    tail_committed: bool = True,
) -> None:
    """Notify an interested plugin after a rewrite, without collecting an attempt record.

    Manual partial compression keeps the tail outside the compressor's input. Rejoin copies for
    the callback's counts only; the host still owns publishing that tail.
    """
    try:
        from hermes_cli.lifecycle import has_hook

        if not has_hook("post_compaction"):
            return
        from agent.model_metadata import estimate_messages_tokens_rough

        if verbatim_tail and messages_before is not None and messages_after is not None:
            from hermes_cli.partial_compress import rejoin_compressed_head_and_tail

            messages_before = rejoin_compressed_head_and_tail(messages_before, copy.deepcopy(verbatim_tail))
            if not tail_committed:
                messages_after = rejoin_compressed_head_and_tail(messages_after, copy.deepcopy(verbatim_tail))
        fire_post_compaction(
            session_id=session_id, old_session_id=old_session_id, kind=kind, reason=reason,
            before_tokens=estimate_messages_tokens_rough(messages_before) if messages_before is not None else None,
            after_tokens=estimate_messages_tokens_rough(messages_after) if messages_after is not None else None,
            messages_removed=(len(messages_before) - len(messages_after)
                              if messages_before is not None and messages_after is not None else None),
        )
    except Exception:
        logging.getLogger(__name__).debug("post-compaction notification failed", exc_info=True)


def fire_post_compaction(
    *, session_id: str, old_session_id: str = "", kind: str | None, reason: str | None,
    before_tokens: int | None, after_tokens: int | None, messages_removed: int | None,
) -> None:
    """The ``post_compaction`` plugin hook, gated on a listener. Callbacks are isolated by the dispatcher."""
    from hermes_cli.lifecycle import has_hook, invoke_hook

    if has_hook("post_compaction"):
        invoke_hook(
            "post_compaction", session_id=session_id, old_session_id=old_session_id, kind=kind, reason=reason,
            before_tokens=before_tokens, after_tokens=after_tokens, messages_removed=messages_removed,
        )
