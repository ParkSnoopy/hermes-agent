"""A paused bare gateway may only adopt a replacement serving its own home."""

from pathlib import Path

from hermes_cli import update_cmd_posix_pause as pause


def test_replay_adopts_only_the_paused_home_and_excludes_the_old_pid(tmp_path, monkeypatch):
    from gateway import status

    home_a, home_b = tmp_path / "a", tmp_path / "b"
    seen = []

    def live_home(home):
        seen.append(home)
        return {home_a: 101, home_b: 202}.get(home)

    monkeypatch.setattr(status, "live_gateway_pid_for_home", live_home)
    assert pause._gateway_on_home(str(home_a), {101}) is None
    assert pause._gateway_on_home(str(home_a), {99}) == 101
    assert pause._gateway_on_home(str(home_b), {99}) == 202
    assert pause._relaunch_bare({"pid": 99, "home": str(home_a)}) == 101
    assert seen == [home_a, home_a, home_b, home_a]


def test_unreadable_home_is_not_adopted(tmp_path, monkeypatch):
    from gateway import status

    def fail(_home: Path):
        raise OSError("identity unavailable")

    monkeypatch.setattr(status, "live_gateway_pid_for_home", fail)
    assert pause._gateway_on_home(str(tmp_path), set()) is None
