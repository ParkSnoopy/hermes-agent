"""Profile names are validated and isolated independently of old HTTP routes."""

from pathlib import Path

import pytest

from hermes_cli.profiles import (
    create_profile,
    get_profile_dir,
    profile_matches_home,
    validate_profile_name,
)


@pytest.fixture
def profile_root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    return root


@pytest.mark.parametrize("name", ["active", "import", "sessions"])
def test_former_route_names_are_ordinary_isolated_profiles(profile_root, name):
    validate_profile_name(name)
    profile = create_profile(name, no_alias=True, no_skills=True)
    assert profile == profile_root / "profiles" / name
    assert get_profile_dir(name) == profile
    assert profile_matches_home(name, profile)
    assert not profile_matches_home(name, profile_root)
    assert not profile_matches_home("default", profile)


@pytest.mark.parametrize("name", ["../beta", "sessions/../beta", "/tmp/beta", ".."])
def test_profile_paths_reject_traversal(profile_root, name):
    with pytest.raises(ValueError):
        get_profile_dir(name)
    with pytest.raises(ValueError):
        create_profile(name, no_alias=True, no_skills=True)
    assert not profile_matches_home(name, profile_root)


def test_profile_home_identity_resolves_symlinks(profile_root, tmp_path):
    profile = create_profile("beta", no_alias=True, no_skills=True)
    link = tmp_path / "profile-link"
    link.symlink_to(profile, target_is_directory=True)
    assert profile_matches_home("beta", link)
    assert not profile_matches_home("active", link)