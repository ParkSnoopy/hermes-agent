"""Contracts for the retained CLI and TUI change lanes."""

from scripts.ci.classify_changes import changed_paths, classify


def test_tui_and_shared_workspaces_select_js_checks():
    for path in ("ui-tui/src/index.ts", "apps/shared/src/index.ts", "tests-js/example.test.mjs"):
        assert classify([path])["frontend"], path


def test_install_and_update_select_python_and_upgrade():
    for path in ("scripts/install.sh", "setup-hermes.sh", "hermes_cli/update_cmd.py"):
        lanes = classify([path])
        assert lanes["python"] and lanes["e2e_upgrade"], path
    for path in ("scripts/install.sh", "scripts/install.ps1", "setup-hermes.sh",
                 "scripts/gen-bootstrap-pins.py", "tests/scripts/test_fresh_source_install.py"):
        assert classify([path])["bootstrap"], path


def test_unknown_diff_and_comparison_overflow_fail_open():
    assert all(classify([]).values())
    assert all(classify(changed_paths([f"file-{n}" for n in range(300)])).values())
    assert classify(["unrecognized/module.py"])["python"]


def test_rename_routes_both_sides():
    assert classify(changed_paths(["docs/new.md\tui-tui/src/old.ts"]))["frontend"]
