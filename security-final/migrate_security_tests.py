import ast
from pathlib import Path

root = Path('/home/admin/host/tmp/hermes-agent-fix-workspace/source')

def remove_nodes(path, names):
    p = root / path
    text = p.read_text()
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
            lines[start:node.end_lineno] = [''] * (node.end_lineno-start)
            found.add(node.name)
    assert found == set(names), (path, set(names)-found)
    p.write_text(''.join(lines))

def replace(path, pairs):
    p = root / path
    text = p.read_text()
    for a, b in pairs:
        assert a in text, (path, a)
        text = text.replace(a, b)
    p.write_text(text)

remove_nodes('tests/hermes_cli/test_gateway_restart_loop.py', [
    'test_prompt_with_command_raises', 'test_script_with_command_raises',
    'test_script_with_neutral_label_submit_or_bootstrap_raises',
    'test_binary_script_does_not_silently_bypass', 'test_relative_script_resolved_under_scripts_dir',
    'test_python_script_with_pathlib_division_not_blocked', 'test_python_script_with_literal_lifecycle_command_still_blocked',
    'test_remote_read_fallback_binary_does_not_crash_guard', 'test_shell_script_reference_walk_still_works',
    'test_cron_script_scan_blocks_cloud_script_without_opening', 'test_tilde_nul_candidate_does_not_crash_cron_script_resolution',
    'test_binary_from_remote_callback_never_false_positives', 'test_oversized_remote_callback_text_fails_closed',
    'test_cron_guard_total_when_home_unresolvable', 'test_remote_backend_script_read_uses_env_execute',
    'test_python_script_branch_gets_the_same_exemption', 'test_check_gateway_lifecycle_adversarial_script_values',
])
replace('tests/hermes_cli/test_gateway_restart_loop.py', [
 ('- Defense 2: cron create rejects prompts containing gateway lifecycle commands\n', ''),
 ('# cron.lifecycle_guard module — the shared checker create_job/CLI/terminal use', '# Shared lifecycle scanner'),
 ('Direct tests for cron.lifecycle_guard.check_gateway_lifecycle.', 'Direct tests for the retained lifecycle scanner.'),
 ('Remote-backend and two-session cwd regression coverage.', 'Local backend refusal coverage.'),
])
remove_nodes('tests/tools/test_execute_code_approval_cluster.py', ['test_guard_cron_deny_blocks','test_guard_explicit_non_cron_masks_leaked_env','test_guard_legacy_env_cron_still_blocks'])
replace('tests/tools/test_execute_code_approval_cluster.py', [('from gateway.session_context import clear_session_vars, reset_session_vars, set_session_vars\n', ''), ('cron-deny, headless-local,', 'headless-local,')])
remove_nodes('tests/tools/test_hardline_blocklist.py', ['test_cron_approve_mode_cannot_bypass_hardline'])
remove_nodes('tests/tools/test_approval.py', ['test_cron_session_returns_false_regardless_of_platform'])
replace('tests/tools/test_approval_config_readonly.py', [('from tools.approval_context import _get_cron_approval_mode', 'from tools.approval_context import _get_unattended_approval_mode'), ('  cron_mode: deny', '  unattended_mode: deny'), ('    _get_cron_approval_mode()', '    assert _get_unattended_approval_mode() == "deny"')])
replace('tests/tools/test_approval_content_rule_keys.py', [('for the session, or permanently for cron', 'for the session, or permanently'), ('def test_cron_deny_blocks_when_only_one_rule_is_permanently_allowed(monkeypatch):\n    monkeypatch.setenv("HERMES_CRON_SESSION", "1")\n    monkeypatch.setattr(approval_context, "_get_cron_approval_mode", lambda: "deny")', 'def test_unattended_deny_blocks_when_only_one_rule_is_permanently_allowed(monkeypatch):\n    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "webhook")\n    monkeypatch.setattr(approval_context, "_get_unattended_approval_mode", lambda: "deny")')])
replace('tests/tools/test_protected_instruction_cli_channel.py', [('cron and unattended platforms', 'unattended platforms'), ('    {"HERMES_CRON_SESSION": "1"},\n', ''), ('ids=["single_query", "cron", "webhook"]', 'ids=["single_query", "webhook"]')])
replace('tests/tools/test_write_approval.py', [('["single_query", "cron", "webhook"]', '["single_query", "webhook"]'), ('`hermes chat -q`, cron and unattended platforms', '`hermes chat -q` and unattended platforms'), ('"single_query": ("HERMES_SINGLE_QUERY_SESSION", "1"), "cron": ("HERMES_CRON_SESSION", "1"),', '"single_query": ("HERMES_SINGLE_QUERY_SESSION", "1"),'), ('monkeypatch.setenv("HERMES_CRON_SESSION", "1")', 'monkeypatch.setenv("HERMES_SINGLE_QUERY_SESSION", "1")'), ('monkeypatch.delenv("HERMES_CRON_SESSION")', 'monkeypatch.delenv("HERMES_SINGLE_QUERY_SESSION")')])
replace('tests/tools/test_hardline_blocklist.py', [('gateway /yolo, approvals.mode=off, or cron approve mode.', 'gateway /yolo or approvals.mode=off.'), (' / cron approve-mode', '')])
print('migration applied')
