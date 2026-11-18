from hermes_cli.config import DEFAULT_CONFIG


def test_repo_discovery_defaults_are_opt_in_by_root():
    policy = DEFAULT_CONFIG["projects"]["repo_scan"]

    assert policy["enabled"] is True
    # Empty roots are deliberately safe: discovery does not infer a home-wide
    # search. Users must explicitly configure roots or use session projects.
    assert policy["roots"] == []
    assert policy["exclude_paths"] == []
