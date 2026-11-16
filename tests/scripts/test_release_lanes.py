"""The stable release lanes: what a lane set builds, tests, and records in its claim."""
import pytest

from scripts.releases import lanes as lane_set


def test_the_default_builds_everything_but_termux():
    assert lane_set.DEFAULT == set(lane_set.NAMES) - {"termux"}
    assert lane_set.select() == lane_set.DEFAULT


@pytest.mark.parametrize("kwargs,expected", [
    ({"only": ["termux", "docker"]}, {"termux", "docker"}),
    ({"with_": ["termux"]}, set(lane_set.NAMES)),
    ({"without": ["nix", "install-e2e"]}, set(lane_set.DEFAULT) - {"nix", "install-e2e"}),
    ({"only": ["termux"], "with_": ["termux-checks"], "without": ["termux"]}, {"termux-checks"}),
    ({"only": []}, set()),
])
def test_select_edits_the_default_or_replaces_it(kwargs, expected):
    assert lane_set.select(**kwargs) == expected


def test_select_refuses_unknown_and_contradictory_lanes():
    with pytest.raises(ValueError, match="unknown lane intel"):
        lane_set.select(with_=["intel"])
    for retired in ("windows", "windows-store", "macos-arm64", "macos-intel", "bootstrap-version", "smoke", "upgrade"):
        with pytest.raises(ValueError, match=f"unknown lane {retired}"):
            lane_set.select(with_=[retired])
    with pytest.raises(ValueError, match="both name ci"):
        lane_set.select(with_=["ci"], without=["ci"])


def test_options_flatten_repeats_and_commas():
    assert lane_set.parse_list(["termux, docker", "ci"]) == ["termux", "docker", "ci"]
    assert lane_set.parse_list(None) is None


def test_every_package_target_has_a_build_lane():
    assert lane_set.TARGET_LANE == {"termux/aarch64": "termux"}


def test_every_job_names_real_lanes():
    for job, needs in lane_set.JOB_LANES.items():
        assert needs <= {*lane_set.NAMES, *lane_set.DERIVED}, job
    for target, lane in lane_set.TARGET_LANE.items():
        assert lane in lane_set.NAMES, target
    for handoff, lane in lane_set.HANDOFF_LANE.items():
        assert lane in {*lane_set.NAMES, *lane_set.DERIVED}, handoff


def test_a_job_runs_only_when_every_lane_it_needs_is_on():
    selected = lane_set.jobs_to_run(["termux", "docker"])
    assert {job for job, runs in selected.items() if runs} == {
        "docker", "publish-docker", "candidates-termux", "candidate-manifest", "publish-bundles"}
    # termux-checks needs both the check and the package.
    assert not lane_set.jobs_to_run(["termux-checks"])["termux-checks"]
    assert lane_set.jobs_to_run(["termux-checks", "termux"])["termux-checks"]
    assert not any(lane_set.jobs_to_run([]).values())



def test_flags_round_trip_and_refuse_anything_loose():
    flags = lane_set.flags(["termux", "docker"])
    assert flags["bundles"] is True and flags["ci"] is False
    assert lane_set.from_flags(flags) == {"termux", "docker"}
    assert lane_set.flags(["docker"])["bundles"] is False
    for bad in (None, {}, {**flags, "extra": True}, {**flags, "ci": "true"},
                {name: flags[name] for name in flags if name != "ci"}):
        with pytest.raises(ValueError, match="boolean for every lane"):
            lane_set.from_flags(bad)


def test_claim_requires_current_revision_and_canonical_lanes():
    current = {"lanes": ["docker", "termux"], "lanesRevision": lane_set.LANES_REVISION}
    assert lane_set.claim_lanes(current) == {"docker", "termux"}
    for invalid in ({"lanes": ["docker"]}, {"skipBundles": False, "skipTests": False},
                    {**current, "lanesRevision": True}, {**current, "lanesRevision": 1},
                    {**current, "lanesRevision": 2},
                    {**current, "lanes": ["termux", "docker"]},
                    {**current, "lanes": ["unknown"]}):
        with pytest.raises(ValueError, match="invalid"):
            lane_set.claim_lanes(invalid)


def test_targets_and_handoffs_follow_the_build_lanes():
    assert lane_set.targets(["docker", "termux"]) == {"termux/aarch64"}
    assert lane_set.handoffs(["termux"], ("termux",)) == ("termux",)


def test_only_built_channels_move():
    # A release that moves no channel is finished once public.
    assert lane_set.moves_channels(["docker"]) and lane_set.moves_channels(["termux"])
    assert not lane_set.moves_channels(["ci"]) and not lane_set.moves_channels([])
