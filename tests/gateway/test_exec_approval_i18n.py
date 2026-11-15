"""Shared gateway approval translation contract."""

from agent.i18n import t


def test_exec_approval_contract_keys_exist_in_english():
    for key in ("header", "reason_label", "smart_deny_line", "timed_out_notice",
                "action_once", "action_session", "action_always", "action_deny"):
        assert t(f"gateway.exec_approval.{key}") != f"gateway.exec_approval.{key}"
