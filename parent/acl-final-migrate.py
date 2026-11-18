from pathlib import Path
root = Path(__file__).resolve().parents[1]
files = [
'hermes_constants.py', 'hermes_state.py', 'gateway/shutdown_flush.py', 'gateway/hosted_room_attachments.py',
'gateway/session_ingress_media.py', 'gateway/platforms/base.py', 'gateway/host_rendezvous.py',
'gateway/runtime_ownership.py', 'gateway/status_lock_dir.py', 'gateway/status.py', 'agent/vault_store.py',
'agent/runtime_session_store.py', 'hermes_cli/config_home.py', 'hermes_cli/update_host_obligation.py',
'scripts/ci/classify_changes.py', 'tests/pm/test_bootstrap_import_closure.py', 'tests/pm/test_takeover_preparation.py',
' tests/scripts/test_fresh_source_install.py'.strip(), 'tests/hermes_cli/test_update_host_obligation.py',
'tools/vision_tools.py', 'tools/process_registry_output_log.py', 'tools/bot_live_delivery.py',
'tools/spill_safety.py', 'tools/computer_use/tool.py', 'tools/process_registry_results.py']
for name in files:
    path = root / name
    text = path.read_text()
    assert 'hermes_dir_acl' in text, name
    text = text.replace('hermes_dir_acl', 'hermes_user_dirs')
    if name == 'scripts/ci/classify_changes.py':
        text = text.replace('# update restores the Windows user ACL on the home after elevation', '# shared user directory creation')
    if name == 'tests/pm/test_bootstrap_import_closure.py':
        text = text.replace('with_acl', 'with_dirs').replace('carries_the_acl_module', 'carries_the_directory_module')
        text = text.replace('test_home_mkdir_never_silently_skips_the_elevated_grant', 'test_home_mkdir_requires_the_directory_module')
        text = text.replace('"""Review #140490: an ImportError fallback to plain mkdir made whether an elevated run grants\n    the user access depend on which files happened to be present. A missing module is loud."""', '"""A missing shared directory module is reported rather than silently ignored."""')
    if name == 'gateway/session_ingress_media.py':
        text = text.replace('"""The store\'s path as the profile names it, unresolved. The one value ``make_user_dir`` gets:\n    ``resolve()`` erases a link planted below the Hermes home (``cache/documents`` -> elsewhere),\n    and an elevated grant on the resolved path would land outside the home with no home fence."""', '"""The store path as the profile names it, before resolution for identity checks."""')
    path.write_text(text)
