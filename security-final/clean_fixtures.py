from pathlib import Path
import re
root = Path('tests/tools')
names = 'hardline_blocklist approval_content_rule_keys execute_code_approval_cluster approval protected_instruction_cli_channel cli_approval_exec_ask_leak approval_unattended_presence approval_cancelled_attribution denial_circuit_breaker approval_deny_rules approval_provenance approval_plugin_hooks execute_code_local_session_approval'.split()
for name in names:
    p = root / f'test_{name}.py'
    text = p.read_text()
    text = re.sub(r'^\s*monkeypatch\.delenv\("HERMES_CRON_SESSION", raising=False\)\n', '\n', text, flags=re.MULTILINE)
    text = text.replace('"HERMES_CRON_SESSION", ', '').replace(', "HERMES_CRON_SESSION"', '')
    text = text.replace('        "HERMES_CRON_SESSION",\n', '')
    text = text.replace(',\n        "HERMES_CRON_SESSION")', ')')
    text = text.replace('non-gateway, non-cron, non-yolo', 'non-gateway, non-yolo')
    text = text.replace('Deny-by-default (approvals.unattended_mode: deny) mirrors cron: an', 'Deny-by-default (approvals.unattended_mode: deny): an')
    text = text.replace(' on unattended platforms (parity with cron).', ' on unattended platforms.')
    text = text.replace('        # HERMES_CRON_SESSION takes priority over HERMES_GATEWAY_SESSION in\n        # _is_gateway_approval_context(); a leaked value from a parent cron\n        # process would force the cron path and break these gateway tests.\n        os.environ.pop("HERMES_CRON_SESSION", None)\n', '')
    compile(text, str(p), 'exec')
    p.write_text(text)
