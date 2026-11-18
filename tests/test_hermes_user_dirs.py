"""User-directory creation preserves pathlib's Linux behavior."""
import os

import pytest

from hermes_user_dirs import make_user_dir, unusable_dir_remedy


def test_mkdir_modes_and_errors(tmp_path):
    target = make_user_dir(tmp_path / "a" / "b", 0o700, parents=True)
    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_uid == os.getuid()
    assert make_user_dir(target, exist_ok=True) == target
    with pytest.raises(FileExistsError):
        make_user_dir(target)
    with pytest.raises(FileNotFoundError):
        make_user_dir(tmp_path / "x" / "y")


def test_named_profile_mkdir_respects_deleted_and_missing_homes(tmp_path):
    from hermes_constants import mark_named_profile_deleted, mkdir_under_hermes_home

    profile = tmp_path / ".hermes" / "profiles" / "alice"
    with pytest.raises(FileNotFoundError):
        mkdir_under_hermes_home(profile / "logs")
    assert not profile.exists()
    profile.mkdir(parents=True)
    assert mkdir_under_hermes_home(profile / "logs").is_dir()
    mark_named_profile_deleted(profile)
    with pytest.raises(FileNotFoundError):
        mkdir_under_hermes_home(profile / "other")
    assert not (profile / "other").exists()


def test_permission_remedy_names_directory(tmp_path):
    assert unusable_dir_remedy(tmp_path) == f'run: sudo chown -R "$(id -un)" "{tmp_path}"'
