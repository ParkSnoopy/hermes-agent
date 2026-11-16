"""Retained uninstall paths do not depend on the removed desktop product."""

from argparse import Namespace

from hermes_cli import uninstall


def test_uninstall_dry_run_keeps_data_and_never_runs_cleanup(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    monkeypatch.setattr(uninstall, "_refuse_if_steward_owned", lambda: None)
    monkeypatch.setattr(uninstall, "get_project_root", lambda: project)
    monkeypatch.setattr(uninstall, "get_hermes_home", lambda: home)
    monkeypatch.setattr(uninstall, "_perform_uninstall", lambda **kwargs: (_ for _ in ()).throw(AssertionError()))
    uninstall.run_uninstall(Namespace(dry_run=True, full=False))
    assert f"Keep Hermes config/data: {home}" in capsys.readouterr().out


def test_data_uninstall_plans_only_current_home(monkeypatch, tmp_path, capsys):
    from hermes_cli import data_cleanup

    home = tmp_path / "home"
    project = tmp_path / "project"
    target = home / "sessions"
    monkeypatch.setattr(uninstall, "get_hermes_home", lambda: home)
    monkeypatch.setattr(uninstall, "get_project_root", lambda: project)
    monkeypatch.setattr(data_cleanup, "plan_data_removal", lambda h, p: data_cleanup.DataRemovalPlan(h, (target,), ()))
    uninstall.run_data_uninstall(Namespace(dry_run=True))
    assert str(target) in capsys.readouterr().out