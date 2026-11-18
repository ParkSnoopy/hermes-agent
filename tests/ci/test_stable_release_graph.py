"""The release workflow's dependency graph enforces publication ordering."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from scripts.releases import lanes as lane_set

from tests.ci.workflow_conditions import gate
from tests.ci.workflow_steps import is_checkout

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return YAML(typ="base").load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def direct_needs(jobs, name):
    needs = jobs[name].get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def ancestors(jobs, name):
    seen = set()
    pending = [name]
    while pending:
        needs = jobs[pending.pop()].get("needs", [])
        for item in [needs] if isinstance(needs, str) else needs:
            if item not in seen:
                seen.add(item)
                pending.append(item)
    return seen


def test_admit_can_read_the_claim_draft():
    jobs = workflow("stable-release.yml")["jobs"]
    assert jobs["admit"]["permissions"]["contents"] == "write"


def test_release_reuses_whole_ci_before_publication():
    jobs = workflow("stable-release.yml")["jobs"]
    assert jobs["ci"]["uses"] == "./.github/workflows/ci.yaml"
    assert jobs["ci"]["with"]["release"] == "true"
    assert "secrets" not in jobs["ci"]
    assert "docker" not in jobs and "publish-docker" not in jobs
    # Every gate and the retained package candidate starts straight after admit.
    # The acceptance join requires CI, so a slow gate cannot delay a build.
    for parallel in ("nix", "pm-bundle", "termux-checks", "install-e2e"):
        assert direct_needs(jobs, parallel) == ["admit"], parallel
    candidate_calls = ["candidates-termux"]
    for call in candidate_calls:
        assert direct_needs(jobs, call) == ["admit"], call
    required = {"ci", "nix", "pm-bundle", "install-e2e", "termux-checks",
                "candidate-manifest", *candidate_calls}
    assert required <= ancestors(jobs, "acceptance")
    assert "candidates" not in jobs
    for name in candidate_calls:
        call = jobs[name]
        assert call["uses"] == "./.github/workflows/termux-release.yml"
        assert call["with"]["release-phase"] == "candidate"
        assert {"tag", "claim-tag", "claim-object"} <= set(call["with"])
    assert not any("linux" in name for name in jobs)
    for name in ("transitions", "macos-packaged", "bootstrap-version", "windows-packaged"):
        assert name not in jobs
    assert set(jobs["candidate-manifest"]["needs"]) == \
        {"admit", *candidate_calls}

    assert required <= ancestors(jobs, "publish-bundles")
    assert {"publish-bundles", "publication"} <= ancestors(jobs, "complete")
    assert "promote-docker" not in jobs and "promote-bundles" not in jobs
    # The gates must run to judge a failed or skipped prerequisite, not skip with it.
    for name in ("acceptance", "publication", "complete"):
        failed = {need: {"result": "failure"} for need in ancestors(jobs, name)}
        assert gate(jobs[name]["if"], {}, failed), name


def test_termux_canary_wires_the_exact_admitted_receipt_into_publication():
    jobs = workflow("termux-release.yml")["jobs"]
    admission = next(step for step in jobs["validate"]["steps"] if step.get("id") == "admission")
    assert "python -m scripts.releases.termux admit-canary" in admission["run"]
    assert admission["env"]["RELEASE_TAG"] == "${{ inputs.tag }}"
    assert jobs["validate"]["outputs"]["tag-object"] == "${{ steps.admission.outputs.tag-object }}"
    payload = jobs["termux-deb"]
    checkout = next(step for step in payload["steps"] if is_checkout(step))
    assert checkout["with"]["ref"] == "${{ needs.validate.outputs.sha }}"
    assert payload["env"]["HERMES_RELEASE_COMMIT"] == "${{ inputs.build_commit == '' && needs.validate.outputs.sha || '' }}"
    publisher = jobs["canary-publish"]
    assert set(publisher["needs"]) == {"validate", "termux-deb"}
    assert publisher["env"]["RELEASE_COMMIT"] == "${{ needs.validate.outputs.sha }}"
    assert publisher["env"]["RELEASE_TAG_OBJECT"] == "${{ needs.validate.outputs.tag-object }}"
    assert publisher["env"]["RELEASE_NEEDS"] == "${{ toJSON(needs) }}"
    assert any(step.get("run") == "python -m scripts.releases.termux publish-canary" for step in publisher["steps"])
    assert "canary-publish" in jobs["phase-result"]["needs"]
    assert jobs["phase-result"]["if"] == "always()"


@pytest.mark.parametrize("phase,canary", [("candidate", False), ("publish", False), ("", True), ("", False)],
                         ids=["candidate", "stable-publication", "canary", "commit"])
@pytest.mark.parametrize("job", ["validate", "termux-deb", "stable-publish", "canary-publish"])
@pytest.mark.parametrize("status", ["failure", "skipped", "missing"])
def test_termux_phase_gate_requires_every_selected_job(tmp_path, phase, canary, job, status):
    jobs = workflow("termux-release.yml")["jobs"]
    step = jobs["phase-result"]["steps"][0]
    selected = {"validate", "stable-publish" if phase == "publish" else "termux-deb"}
    if canary:
        selected.add("canary-publish")
    results = {name: {"result": "success" if name in selected else "skipped"}
               for name in jobs["phase-result"]["needs"]}
    if status == "missing":
        del results[job]
    else:
        results[job]["result"] = status
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], cwd=tmp_path,
                            env={**os.environ, "PHASE": phase, "CANARY": str(canary).lower(),
                                 "RESULTS": json.dumps(results)}, capture_output=True,
                            text=True, encoding="utf-8", timeout=10, check=False)
    assert (result.returncode == 0) == (job not in selected), result.stdout + result.stderr


LANE_SETS = {
    "default": lane_set.DEFAULT,
    "everything": frozenset(lane_set.NAMES),
    "termux only": lane_set.select(only=["termux"]),
    "nothing": frozenset(),
    # Each lane alone and each lane missing, so no job's condition can borrow another lane's flag.
    **{f"only {name}": frozenset({name}) for name in lane_set.NAMES},
    **{f"everything but {name}": frozenset(lane_set.NAMES) - {name} for name in lane_set.NAMES},
}


@pytest.mark.parametrize("lanes", LANE_SETS.values(), ids=LANE_SETS)
def test_every_lane_controlled_job_runs_exactly_when_the_lane_table_says(lanes):
    """The workflow's `if` conditions and the gate's expectations describe the same graph."""
    jobs = workflow("stable-release.yml")["jobs"]
    runs = lane_set.jobs_to_run(lanes)
    admit = {"result": "success", "outputs": {"lanes": json.dumps(lane_set.flags(lanes)),
                                              "jobs": json.dumps(runs)}}

    def result(name):
        if name == "admit":
            return admit
        # What the job does under these lanes is what the table says (a job outside it never skips).
        return {"result": "success" if runs.get(name, True) else "skipped",
                "outputs": {"baseline": "published"}}

    for name, wanted in runs.items():
        assert name in jobs, name
        needs = {need: result(need) for need in direct_needs(jobs, name)}
        assert gate(jobs[name].get("if", "true"), {}, needs) == wanted, (name, sorted(lanes))
    # A job that is not lane-controlled never skips with the lanes.
    for name in set(jobs) - set(runs) - {"admit", "acceptance", "publication", "complete",
                                         "candidate-manifest", "publish-bundles"}:
        assert "outputs.jobs" not in str(jobs[name].get("if", "")), name


def test_lane_inputs_reach_the_workflows_they_configure():
    jobs = workflow("stable-release.yml")["jobs"]
    # Termux acceptance is intrinsic to its build.
    assert "skip-tests" not in jobs["candidates-termux"]["with"]
    assert {"lanes", "jobs"} <= set(jobs["admit"]["outputs"])
    for name in ("acceptance", "publication", "complete"):
        gate_step = next(step for step in jobs[name]["steps"]
                         if "scripts.releases.stable gate" in step.get("run", ""))
        assert gate_step["env"] == {"RELEASE_NEEDS": "${{ toJSON(needs) }}",
                                    "LANES": "${{ needs.admit.outputs.lanes }}"}
        gated = gate_step["run"].split(" gate ", 1)[1].split()
        assert set(gated) <= set(direct_needs(jobs, name)), name



def test_admit_emits_the_same_lanes_and_jobs_the_gate_reads(tmp_path):
    from scripts.releases import stable

    output = tmp_path / "output"
    summary = tmp_path / "summary"
    admitted = {"claim_tag": "rc.1-v1.2.3", "claim_object": "a" * 40, "tag": "v1.2.3", "commit": "b" * 40,
                "version": "1.2.3", "attempt": 1, "claim_epoch": 1, "autopublish": False,
                "lanes": lane_set.select(only=["termux"])}
    env = {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_REPOSITORY": "o/r"}
    from unittest import mock
    with mock.patch.object(stable, "check_claim", return_value=admitted), \
            mock.patch.object(stable, "output", return_value=json.dumps(
                {"tagName": "rc.1-v1.2.3", "isDraft": True, "isPrerelease": False, "databaseId": 7})):
        stable.admit(env)
    emitted = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert lane_set.from_flags(json.loads(emitted["lanes"])) == admitted["lanes"]
    assert json.loads(emitted["jobs"]) == lane_set.jobs_to_run(admitted["lanes"])
    assert "on: termux" in summary.read_text(encoding="utf-8")


def test_all_applicable_ci_jobs_are_aggregated_without_retired_desktop_e2e():
    jobs = workflow("ci.yaml")["jobs"]
    checks = {name for name, job in jobs.items() if "uses" in job}
    assert checks <= set(jobs["all-checks-pass"]["needs"])
    assert "e2e-desktop" not in jobs
    assert "workflow_call" in workflow("ci.yaml")["on"]


def test_claim_custody_and_final_payload_identity_reach_every_privileged_phase():
    release = workflow("stable-release.yml")
    jobs = release["jobs"]
    # The claim is the one record of the attempt's policy; a dispatch cannot override it.
    inputs = set(release["on"]["workflow_dispatch"]["inputs"])
    assert not {"autopublish", "lanes", "skip-bundles", "skip-tests"}.intersection(inputs)
    assert {"claim-tag", "claim-object", "tag", "commit", "version", "release-id", "release-epoch"} <= \
        set(jobs["admit"]["outputs"])
    for name in ("publish-bundles", "candidates-termux"):
        call = jobs[name]["with"]
        assert call["tag"] == "${{ needs.admit.outputs.tag }}"
        assert call["claim-tag"] == "${{ needs.admit.outputs.claim-tag }}"
        assert call["claim-object"] == "${{ needs.admit.outputs.claim-object }}"
    termux = workflow("termux-release.yml")
    assert "HERMES_RELEASE_EPOCH" not in termux["jobs"]["termux-deb"]["env"]
    assert jobs["nix"]["with"]["version"] == "${{ needs.admit.outputs.version }}"
    assert "version" not in jobs["pm-bundle"]["with"]

    assert "release-epoch" not in jobs["nix"]["with"]
    assert "release-epoch" not in jobs["pm-bundle"]["with"]

    complete = jobs["complete"]["steps"]
    # A6: the green workflow validates the accepted candidate archive; the
    # final tag and the retarget move to the publication pass.
    validation = next(i for i, step in enumerate(complete)
                      if step.get("name", "").startswith("Validate the accepted candidate archive"))
    assert "DOCKER_MANIFEST_DIGEST" not in complete[validation]["env"]
    assert "RELEASE_ID" not in complete[validation]["env"]
    # Complete and publication read the same accepted candidate manifest.
    assert complete[validation]["env"]["CANDIDATE_MANIFEST_SHA256"] == \
        "${{ needs.candidate-manifest.outputs.manifest-sha256 }}"
    assert jobs["publish-bundles"]["with"]["manifest-sha256"] == \
        "${{ needs.candidate-manifest.outputs.manifest-sha256 }}"
    reconcile = next(i for i, step in enumerate(complete) if step.get("name", "").startswith("Reconcile ordered"))
    assert validation < reconcile
    assert not any("Create the final tag" in step.get("name", "") for step in complete)


def test_release_gates_extract_consumer_facing_versions():
    nix = workflow("nix.yml")["jobs"]
    nix_check = next(
        step["run"] for step in nix["flake-check"]["steps"]
        if step.get("name") == "Verify release package runtime identity"
    )
    assert '"$package/bin/hermes" --version' in nix_check
    assert "actual != expected" in nix_check
    # A path: flake has no git metadata; without ?rev= the stamp has no commit
    # and the packaged CLI reports "unknown".
    assert 'release-source?rev=$CANDIDATE_COMMIT' in nix_check
    flake_check = next(step["run"] for step in nix["flake-check"]["steps"] if step.get("name") == "nix flake check")
    assert "release-source?rev={1}" in flake_check and "env.CANDIDATE_COMMIT" in flake_check


def test_packaged_stamp_writers_receive_versions_without_rewriting_python_metadata():
    nix_steps = workflow("nix.yml")["jobs"]["flake-check"]["steps"]
    prepare = next(step["run"] for step in nix_steps if step.get("name") == "Prepare isolated release source")
    assert "scripts/releases/stamping.py" in prepare


def test_publication_reconciler_has_every_recovery_trigger_and_shared_lock():
    stable = workflow("stable-release.yml")
    publication = workflow("stable-release-publication.yml")

    assert stable["concurrency"] == publication["concurrency"] == {
        "group": "stable-release", "cancel-in-progress": "false",
    }
    assert {"workflow_dispatch", "workflow_run"} <= set(publication["on"])
    assert publication["on"]["workflow_run"] == {
        "workflows": ["Stable Release"], "types": ["completed"],
    }
    reconcile = publication["jobs"]["reconcile"]
    assert reconcile["environment"] == "release-signing"
    assert publication["permissions"] == {"contents": "write", "actions": "write"}
    # Reconcile after a dispatched run of this repository that did not succeed;
    # a manual dispatch of the reconciler always runs.
    def run_of(**overrides):
        workflow_run = {"conclusion": "failure", "event": "workflow_dispatch",
                        "head_repository": {"full_name": "o/r"}, **overrides}
        return {"event_name": "workflow_run", "repository": "o/r", "event": {"workflow_run": workflow_run}}

    assert gate(reconcile["if"], {}, {}, github=run_of())
    assert gate(reconcile["if"], {}, {}, github={"event_name": "workflow_dispatch"})
    for refused in ({"conclusion": "success"}, {"event": "push"}, {"head_repository": {"full_name": "fork/r"}}):
        assert not gate(reconcile["if"], {}, {}, github=run_of(**refused)), refused
    checkout = reconcile["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["persist-credentials"] == "false"
    assert not any(step.get("run", "").startswith("sleep ") for step in reconcile["steps"])



def test_every_run_step_receives_the_variables_it_reads():
    """A run step reading $VAR it was never given gets an empty string: rc.14-v0.21.7 passed
    every build and gate, then 'complete' died rendering the builds table without
    CLOUDFLARE_R2_PUBLIC_URL or RELEASE_CLAIM_TAG."""
    import re

    runner = {"GITHUB_REPOSITORY", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "GITHUB_ENV", "GITHUB_SHA",
              "GITHUB_RUN_ID", "GITHUB_WORKSPACE", "RUNNER_TEMP", "HOME", "PATH"}
    for name, job in workflow("stable-release.yml")["jobs"].items():
        given = set(job.get("env") or {})
        for step in job.get("steps", []):
            used = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]+)", step.get("run", ""))) - runner
            missing = used - given - set(step.get("env") or {})
            assert not missing, f"{name} / {step.get('name') or step['run'][:40]}: {sorted(missing)}"
