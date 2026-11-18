"""Stable release admission, Termux candidates and final release receipts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

import urllib.request

from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

from hermes_cli.update_channel import STABLE_TAG_RE
from scripts.releases import lanes as lane_set

from scripts.releases.draft_warning import strip_draft_warning
from scripts.releases.versioning import tag_record
SHA = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"[a-f0-9]{64}")
ALL_TARGETS = tuple(lane_set.TARGET_LANE)


# Stable releases are cut from ``main`` or from a ``release/*`` maintenance branch.
RELEASE_LINE_BRANCHES = ("main", "release/*")


def release_line_refspecs(remote_prefix: str) -> list[str]:
    """Fetch refspecs that mirror every release-line branch under ``refs/remotes/<remote_prefix>/``."""
    return [f"+refs/heads/{branch}:refs/remotes/{remote_prefix}/{branch}"
            for branch in RELEASE_LINE_BRANCHES]


def release_line_contains(commit: str, remote_prefix: str) -> list[str]:
    """The git command that lists the fetched release-line branches holding ``commit``.

    Empty output means the commit is on no release line. Git matches the
    ``release/*`` pattern across the slash, so ``release/0.22.1`` qualifies.
    """
    return ["git", "for-each-ref", "--contains", commit, "--format=%(refname)",
            *(f"refs/remotes/{remote_prefix}/{branch}" for branch in RELEASE_LINE_BRANCHES)]


def admit_claim(tag: str, commit: str, *, on_main) -> dict:
    """Admit a release from its attempt ref. The checkout version is not read.

    ``main`` carries ``0.0.0`` on purpose, so the version and the attempt come
    from ``rc.<N>-vX.Y.Z`` and the only question about the commit is whether
    it is on a release line: ``main`` or a ``release/*`` branch. ``on_main`` is
    that predicate; the name predates release branches.
    """
    from scripts.releases.versioning import parse_attempt_ref

    parsed = parse_attempt_ref(tag)
    if parsed is None:
        raise ValueError(f"{tag} is not a claim tag")
    version, attempt = parsed
    if not on_main(commit):
        raise ValueError(f"{commit} is not on main or a release/* branch")
    return {"claim_tag": tag, "tag": f"v{version}", "version": version, "attempt": attempt,
            "commit": commit}


def require_stable_identity(tag: str, commit: str) -> None:
    """Validate the final payload identity without requiring its future ref."""
    if not isinstance(tag, str) or not STABLE_TAG_RE.fullmatch(tag) or not SHA.fullmatch(commit or ""):
        raise ValueError("Invalid stable payload identity")


def require_success(needs: dict, required: list[str]) -> None:
    if not required or len(set(required)) != len(required):
        raise ValueError("Invalid required-job list")
    failures = [f"{name}={needs.get(name, {}).get('result', 'missing')}"
                for name in required if needs.get(name, {}).get("result") != "success"]
    if failures:
        raise ValueError("Release blocked: " + ", ".join(failures))



def gate_expectations(required: list[str], lanes: Iterable[str]) -> dict:
    """Each gated job's required result under the claim's lanes: `skipped` for a job a
    lane it needs is off for, `success` for every other."""
    runs = lane_set.jobs_to_run(lanes)
    return {name: "success" if runs.get(name, True) else "skipped" for name in required}


def require_gate(needs: dict, required: list[str], *, lanes: Iterable[str]) -> None:
    """``require_success`` that also demands a lane-removed job really was skipped."""
    if not required or len(set(required)) != len(required):
        raise ValueError("Invalid required-job list")
    expected = gate_expectations(required, lanes)

    failures = [f"{name}={needs.get(name, {}).get('result', 'missing')} (expected {want})"
                for name, want in expected.items() if needs.get(name, {}).get("result") != want]
    if failures:
        raise ValueError("Release blocked: " + ", ".join(failures))



def require_targets_match_claim(manifest: dict, lanes: Iterable[str]) -> None:
    """The manifest carries exactly the targets the claim's build lanes produce."""
    present = {f"{row.get('platform')}/{row.get('arch')}" for row in manifest.get("packages", [])}
    wanted = lane_set.targets(lanes)
    if present != wanted:
        raise ValueError(f"Candidate manifest targets {sorted(present)} differ from the claim's {sorted(wanted)}")



def _validated_rows(manifest: dict, tag: str, commit: str, public_base: str,
                    release_epoch: int | None, *, archive: str) -> dict:
    """Validate the current Termux manifest and its immutable package identity."""
    require_stable_identity(tag, commit)
    if set(manifest) != {"schema", "tag", "commit", "releaseEpoch", "archive", "packages", "files"}:
        raise ValueError("Candidate manifest fields differ from the current contract")
    if manifest.get("schema") != 3 or manifest.get("tag") != tag or manifest.get("commit") != commit or not isinstance(manifest.get("packages"), list):
        raise ValueError("Candidate manifest does not match release identity")
    if manifest.get("archive") != archive:
        raise ValueError("Candidate manifest names a different release archive")
    admitted_epoch = manifest.get("releaseEpoch")
    if isinstance(admitted_epoch, bool) or not isinstance(admitted_epoch, int) or admitted_epoch <= 0:
        raise ValueError("Candidate release epoch must be a positive integer")
    if release_epoch is not None and admitted_epoch != release_epoch:
        raise ValueError("Candidate release epoch differs from the admitted claim")
    prefix = urlsplit(f"{public_base.rstrip('/')}/releases/tag/{archive}/")
    if prefix.scheme != "https" or prefix.username or prefix.password or not prefix.netloc:
        raise ValueError("Public release origin must use HTTPS")
    rows = {}
    for item in manifest["packages"]:
        if not isinstance(item, dict) or set(item) != {
                "platform", "arch", "tag", "commit", "identity", "version", "artifact"}:
            raise ValueError("Candidate package fields differ from the current contract")
        target = f"{item.get('platform')}/{item.get('arch')}"
        if target not in ALL_TARGETS or target in rows or item.get("tag") != tag or item.get("commit") != commit:
            raise ValueError(f"Invalid or duplicate candidate target: {target}")
        artifact = item.get("artifact", {})
        url = urlsplit(artifact.get("url", ""))
        decoded = unquote(url.path)
        if any(part in (".", "..") for part in decoded.split("/")) or "\\" in decoded or "%" in decoded:
            raise ValueError("Invalid artifact path encoding")
        if (url.scheme, url.netloc) != (prefix.scheme, prefix.netloc) or not url.path.startswith(prefix.path) or url.query or url.fragment or url.username or url.password:
            raise ValueError(f"Candidate package is outside its immutable tag archive: {target}")
        if not DIGEST.fullmatch(artifact.get("sha256", "")):
            raise ValueError(f"Invalid candidate digest: {target}")
        if (item.get("identity") != "hermes-agent" or item.get("version") != f"{tag[1:]}-1"
                or not url.path.endswith(".deb")):
            raise ValueError("Termux candidate identity or version differs from the admitted release")
        rows[target] = item
    return rows


def validate_candidates(manifest: dict, tag: str, commit: str, public_base: str,
                        release_epoch: int | None = None, *, archive: str) -> dict:
    """`tag` is the plain payload identity; `archive` is the releases/tag/<ref>/
    prefix every artifact URL must live under. Stable attempts name the attempt
    ref as their archive; the two are separate fields and never overloaded.


    """
    rows = _validated_rows(manifest, tag, commit, public_base, release_epoch, archive=archive)
    if not rows:
        raise ValueError("Candidate manifest has no packages")
    # Which targets a release must carry is its claim's business (require_targets_match_claim),
    # not the manifest's: a release may build any subset of the lanes.

    return rows



def read_manifest(url: str, expected_hash: str | None = None, *, expected_origin: str | None = None,
                  opener=None) -> dict:
    # Resolved per call: a default bound at import would pin the opener that
    # existed then and ignore the process's trust setup.
    opener = opener or urllib.request.urlopen
    location = urlsplit(url)
    origin = urlsplit(expected_origin or url)

    def check_origin(target):
        if target.scheme != "https" or not target.hostname or target.username or target.password:
            raise ValueError("Manifest origin must use HTTPS without credentials")
        if (target.scheme, target.hostname, target.port or 443) != (origin.scheme, origin.hostname, origin.port or 443):
            raise ValueError("Manifest is outside the expected release origin")

    check_origin(location)
    with opener(url, timeout=60) as response:
        check_origin(urlsplit(response.geturl()))
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Release manifest exceeds size limit")
    if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError("Candidate manifest digest mismatch")
    return json.loads(data)


def output(argv: list[str]) -> str:
    return subprocess.check_output(argv, text=True, encoding="utf-8").strip()


def validate_claim(metadata: object, *, version: str, attempt: int, commit: str) -> dict:
    """The claim message's exact shape. It is the one record of the attempt's policy.
    The copy returned has its current-revision lanes."""
    expected = {"schema": 1, "version": version, "attempt": attempt, "commit": commit}
    if (not isinstance(metadata, dict)
            or any(metadata.get(key) != value for key, value in expected.items())
            or not isinstance(metadata.get("autopublish"), bool)
            or not isinstance(metadata.get("claimEpoch"), int)
            or metadata["claimEpoch"] <= 0):
        raise ValueError("Stable claim metadata is invalid")
    base = {*expected, "autopublish", "claimEpoch"}
    if set(metadata) != {*base, "lanes", "lanesRevision"}:
        raise ValueError("Stable claim metadata is invalid")
    return {**metadata, "lanes": lane_set.claim_lanes(metadata)}


def _claim_metadata(raw: str, *, version: str, attempt: int, commit: str) -> dict:
    try:
        metadata = tag_record(raw)
    except (TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError("Stable claim metadata is invalid") from error
    return validate_claim(metadata, version=version, attempt=attempt, commit=commit)


def validate_final(final: object, *, version: str, commit: str, claim_tag: str,
                   claim_object: str, claim: dict) -> dict:
    """The final receipt tag's exact shape, bound to its validated claim.

    A claim that builds no bundle has no candidate manifest, so its receipt
    records ``candidateManifestSha256: null``; one that builds no Docker image
    records ``dockerManifestDigest: null``. Every other receipt pins them.
    """
    if not isinstance(final, dict):
        raise ValueError("Final tag metadata differs from its claim")
    expected = {
        "schema": 1, "version": version, "commit": commit,
        "claimTag": claim_tag, "claimTagObject": claim_object,
        "autopublish": claim["autopublish"],
        "claimEpoch": claim["claimEpoch"],
        "releaseId": final.get("releaseId"),
        "candidateManifestSha256": final.get("candidateManifestSha256"),
        "dockerManifestDigest": final.get("dockerManifestDigest"),
        "archive": f"releases/tag/{claim_tag}/",
    }
    manifest = final.get("candidateManifestSha256")
    lanes = claim["lanes"]
    manifest_ok = bool(DIGEST.fullmatch(manifest or "")) if lane_set.has_bundles(lanes) else manifest is None
    docker = final["dockerManifestDigest"]
    docker_ok = bool(re.fullmatch(r"sha256:[a-f0-9]{64}", docker or "")) if "docker" in lanes else docker is None
    if (final != expected
            or not isinstance(final["releaseId"], int) or final["releaseId"] <= 0
            or not manifest_ok or not docker_ok):
        raise ValueError("Final tag metadata differs from its claim")
    return final


def tagger_epoch(tag_object: str, run=output) -> int:
    body = run(["git", "cat-file", "-p", tag_object])
    tagger = next((line for line in body.splitlines() if line.startswith("tagger ")), None)
    if tagger is None:
        raise ValueError("Stable claim has no tagger timestamp")
    try:
        epoch = int(tagger.rsplit(" ", 2)[1])
    except (IndexError, ValueError) as error:
        raise ValueError("Stable claim tagger timestamp is invalid") from error
    if epoch <= 0:
        raise ValueError("Stable claim tagger timestamp is invalid")
    return epoch


CONTROLLER_BRANCH = "main"
CONTROLLER_REF = f"refs/heads/{CONTROLLER_BRANCH}"
CONTROLLER_WORKFLOW = "stable-release.yml"


def claim_run_title(claim_tag: str) -> str:
    """The run-name stable-release.yml sets. Every run is on the default branch, so this names the claim."""
    return f"Stable release {claim_tag}"


def is_claim_run(*, title: str | None, claim_tag: str) -> bool:
    """Match the current default-branch controller's claim-specific run name."""
    return title == claim_run_title(claim_tag)


def check_controller(env: dict) -> None:
    """Require the default-branch dispatch of Stable Release.

    Reusable workflows keep the top-level caller's identity, so the same check holds
    inside every called workflow. The commit under test comes from the claim tag, not
    from GITHUB_SHA, which is the default branch tip here.
    """
    repository = env.get("GITHUB_REPOSITORY", "")
    expected = f"{repository}/.github/workflows/{CONTROLLER_WORKFLOW}@{CONTROLLER_REF}"
    if (not repository or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or env.get("GITHUB_REF") != CONTROLLER_REF or env.get("GITHUB_WORKFLOW_REF") != expected):
        raise ValueError("Stable release must run from the default-branch controller workflow")


def check_claim(env: dict, run=output) -> dict:
    """Bind the run to one remote annotated claim object and its commit."""
    claim_tag = env.get("RELEASE_CLAIM_TAG")
    if not isinstance(claim_tag, str) or not claim_tag:
        raise ValueError("Stable release needs a claim tag")
    check_controller(env)
    claim_ref = f"refs/tags/{claim_tag}"
    local_object = run(["git", "rev-parse", claim_ref])
    local_commit = run(["git", "rev-parse", f"{claim_ref}^{{commit}}"])
    if run(["git", "cat-file", "-t", local_object]) != "tag":
        raise ValueError("Stable claim must be an annotated tag")
    remote = dict(line.split()[::-1] for line in run(
        ["git", "ls-remote", "origin", claim_ref, f"{claim_ref}^{{}}"]
    ).splitlines())
    remote_object = remote.get(claim_ref)
    remote_commit = remote.get(f"{claim_ref}^{{}}")
    expected_object = env.get("RELEASE_CLAIM_OBJECT")
    commit = local_commit
    if not SHA.fullmatch(commit):
        raise ValueError("Stable claim needs an exact commit")
    if (remote_commit != commit or remote_object != local_object
            or (expected_object and remote_object != expected_object)):
        raise ValueError("Stable claim tag moved")
    run(["git", "fetch", "origin", *release_line_refspecs("origin")])

    def on_main(sha: str) -> bool:
        return bool(run(release_line_contains(sha, "origin")))

    admitted = admit_claim(claim_tag, commit, on_main=on_main)
    raw_metadata = run(["git", "tag", "-l", claim_tag, "--format=%(contents)"])
    metadata = _claim_metadata(raw_metadata, version=admitted["version"],
                               attempt=admitted["attempt"], commit=commit)
    claim_epoch = tagger_epoch(local_object, run)
    if metadata["claimEpoch"] != claim_epoch:
        raise ValueError("Stable claim epoch differs from its annotated tagger timestamp")
    return {**admitted, "claim_object": local_object,
            "autopublish": metadata["autopublish"], "lanes": metadata["lanes"],
            "claim_epoch": claim_epoch}


def stable_context(env: dict, run=output) -> tuple[str, str, dict]:
    claim = check_claim(env, run=run)
    tag = env.get("RELEASE_TAG")
    if not isinstance(tag, str) or tag != claim["tag"]:
        raise ValueError("Stable payload tag differs from the admitted claim")
    return tag, claim["commit"], claim


def final_context(env: dict, run=output) -> tuple[str, str, dict]:
    """Verify the final annotated receipt, its claim, and published release."""
    repository = env.get("GITHUB_REPOSITORY", "")
    tag = env.get("RELEASE_TAG", "")
    commit = env.get("RELEASE_COMMIT", "")
    claim_tag = env.get("RELEASE_CLAIM_TAG", "")
    claim_object = env.get("RELEASE_CLAIM_OBJECT", "")
    require_stable_identity(tag, commit)
    admitted = admit_claim(claim_tag, commit, on_main=lambda _commit: True)
    if admitted["tag"] != tag or not SHA.fullmatch(claim_object):
        raise ValueError("Final release differs from its claim")

    refs = {}
    for line in run(["git", "ls-remote", "origin",
                     f"refs/tags/{claim_tag}", f"refs/tags/{claim_tag}^{{}}",
                     f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"]).splitlines():
        sha, ref = line.split()
        refs[ref] = sha
    if (refs.get(f"refs/tags/{claim_tag}") != claim_object
            or refs.get(f"refs/tags/{claim_tag}^{{}}") != commit
            or refs.get(f"refs/tags/{tag}^{{}}") != commit
            or not refs.get(f"refs/tags/{tag}")):
        raise ValueError("Final release tag custody changed")

    run(["git", "fetch", "origin", *release_line_refspecs("origin"),
         f"+refs/tags/{claim_tag}:refs/tags/{claim_tag}",
         f"+refs/tags/{tag}:refs/tags/{tag}"])
    for receipt, expected_object in ((claim_tag, claim_object),
                                     (tag, refs[f"refs/tags/{tag}"])):
        local_object = run(["git", "rev-parse", f"refs/tags/{receipt}"])
        if local_object != expected_object or run(["git", "cat-file", "-t", local_object]) != "tag":
            raise ValueError("Final release local tag differs from the remote")
    if not run(release_line_contains(commit, "origin")):
        raise ValueError(f"{commit} is not on main or a release/* branch")
    claim = _claim_metadata(
        run(["git", "tag", "-l", claim_tag, "--format=%(contents)"]),
        version=admitted["version"], attempt=admitted["attempt"], commit=commit,
    )
    final = validate_final(
        tag_record(run(["git", "tag", "-l", tag, "--format=%(contents)"])),
        version=admitted["version"], commit=commit, claim_tag=claim_tag,
        claim_object=claim_object, claim=claim,
    )
    release = json.loads(run([
        "gh", "api", f"repos/{repository}/releases/tags/{tag}",
    ]))
    if (release.get("id") != final["releaseId"] or release.get("tag_name") != tag
            or release.get("draft") is not False
            or release.get("prerelease") is not False or not release.get("published_at")):
        raise ValueError("Stable channel requires the published final release")
    return tag, commit, {**admitted, "claim_object": claim_object,
                         "autopublish": claim["autopublish"],
                         "lanes": claim["lanes"],
                         "claim_epoch": claim["claimEpoch"],
                         "release_id": final["releaseId"],
                         "candidate_manifest_sha256": final["candidateManifestSha256"],
                         "docker_manifest_digest": final["dockerManifestDigest"]}


def emit(values: dict, env: dict) -> None:
    with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as file:
        file.writelines(f"{key}={value if isinstance(value, str) else json.dumps(value, separators=(',', ':'))}\n" for key, value in values.items())


def read_candidate(env: dict) -> dict:
    digest = env.get("CANDIDATE_MANIFEST_SHA256", "")
    if not DIGEST.fullmatch(digest):
        raise ValueError("Pinned candidate manifest digest is required")
    return read_manifest(env["CANDIDATE_MANIFEST_URL"], digest)


def read_admitted_candidate(tag: str, commit: str, public_base: str, digest: str, *,
                            archive: str) -> dict:
    """The page and package promoter consume the same pinned admission."""
    if not DIGEST.fullmatch(digest or ""):
        raise ValueError("Pinned candidate manifest digest is required")
    require_stable_identity(tag, commit)
    manifest = read_manifest(f"{public_base.rstrip('/')}/releases/tag/{archive}/release-candidates.json",
                             digest, expected_origin=public_base)
    validate_candidates(manifest, tag, commit, public_base, archive=archive)
    return manifest


def summary(text: str, env: dict) -> None:
    with Path(env["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as file:
        file.write(text + "\n")


def admit(env: dict) -> None:
    """Admit the claim. The checkout carries 0.0.0, so the tag is the version."""
    admitted = check_claim(env)
    repository = env["GITHUB_REPOSITORY"]
    release = json.loads(output([
        "gh", "release", "view", admitted["claim_tag"], "--repo", repository,
        "--json", "databaseId,tagName,isDraft,isPrerelease",
    ]))
    if (release.get("tagName") != admitted["claim_tag"] or release.get("isDraft") is not True
            or release.get("isPrerelease") is not False or not isinstance(release.get("databaseId"), int)):
        raise ValueError("Stable claim must already own one non-prerelease draft")
    emit({
        "claim-tag": admitted["claim_tag"], "claim-object": admitted["claim_object"],
        "tag": admitted["tag"], "commit": admitted["commit"], "version": admitted["version"],
        "release-id": release["databaseId"], "release-epoch": admitted["claim_epoch"],
        # `lanes` is every lane's flag; `jobs` is every lane-controlled job's run flag. Each
        # job's `if` reads `jobs`, so the lane table is the only place that says what runs.
        "lanes": lane_set.flags(admitted["lanes"]), "jobs": lane_set.jobs_to_run(admitted["lanes"]),
    }, env)
    summary(
        f"## Stable candidate {admitted['claim_tag']}\nCommit: {admitted['commit']}\n"
        f"Version: {admitted['version']}\nPayload tag: {admitted['tag']}\n"
        f"Lanes {lane_set.describe(admitted['lanes'])}\n",
        env,
    )


def verify(env: dict) -> None:
    """Revalidate claim custody in a reusable privileged workflow."""
    tag, commit, claim = stable_context(env)
    emit({"tag": tag, "sha": commit, "channel": "stable", "payload-version": tag[1:],
          "release-epoch": claim["claim_epoch"]}, env)



CANDIDATE_HANDOFFS = ("termux",)
CANDIDATE_INCLUDES = ("metadata-*.json",)


def candidate_manifest(env: dict) -> None:
    """Merge the staged handoffs into the accepted candidate manifest."""
    from scripts.bundles.release_artifacts import assemble
    from scripts.releases.handoff import fetch

    needs = json.loads(env.get("RELEASE_NEEDS", "{}"))
    if not isinstance(needs, dict):
        raise ValueError("Candidate call results must be a needs object")
    tag, commit, claim = stable_context(env)
    lanes = claim["lanes"]
    if not lane_set.has_bundles(lanes):
        raise ValueError("A claim that builds no bundle has no candidate manifest")
    handoffs = lane_set.handoffs(lanes, CANDIDATE_HANDOFFS)
    runs = lane_set.jobs_to_run(lanes)
    # Missing or failed selected builds leave no accepted manifest.
    require_success(needs, [call for call in lane_set.CANDIDATE_CALLS if runs[call]])
    base = env["CLOUDFLARE_R2_PUBLIC_URL"].rstrip("/")
    archive = claim["claim_tag"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fetch(tag=archive, commit=commit, names=list(handoffs), root=root,
              includes=list(CANDIDATE_INCLUDES))
        out = root / "release-candidates.json"
        assemble(root, tag, commit, base, out,
                 release_epoch=claim["claim_epoch"], archive=archive, lanes=lanes)
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
    emit({"manifest-url": f"{base}/releases/tag/{archive}/release-candidates.json",
          "manifest-sha256": digest}, env)


def _final_metadata(tag: str, commit: str, claim: dict, candidate_manifest_sha256: str | None,
                    docker_manifest_digest: str | None, release_id: int) -> dict:
    if not lane_set.has_bundles(claim["lanes"]):
        if candidate_manifest_sha256 is not None:
            raise ValueError("A claim that builds no bundle cannot bind a candidate manifest")
    elif not DIGEST.fullmatch(candidate_manifest_sha256 or ""):
        raise ValueError("Final tag candidate manifest digest is invalid")
    if "docker" not in claim["lanes"]:
        if docker_manifest_digest is not None:
            raise ValueError("A claim that builds no Docker image cannot bind a Docker digest")
    elif not re.fullmatch(r"sha256:[a-f0-9]{64}", docker_manifest_digest or ""):
        raise ValueError("Final tag Docker manifest digest is invalid")
    if not isinstance(release_id, int) or release_id <= 0:
        raise ValueError("Final tag release database ID is invalid")
    return {
        "schema": 1, "version": tag[1:], "commit": commit,
        "claimTag": claim["claim_tag"], "claimTagObject": claim["claim_object"],
        "autopublish": claim["autopublish"],
        "claimEpoch": claim["claim_epoch"],
        "releaseId": release_id,
        "candidateManifestSha256": candidate_manifest_sha256,
        "dockerManifestDigest": docker_manifest_digest,
        "archive": f"releases/tag/{claim['claim_tag']}/",
    }


def authenticated_push(ref: str, env: dict | None = None) -> list[str]:
    """``git push origin <ref>`` that authenticates with GH_TOKEN when it is set.

    The publication checkout runs with persist-credentials: false, so git has
    no token of its own; gh hands GH_TOKEN to this one push as a credential
    helper, keeping the token out of argv and out of .git/config.
    """
    env = os.environ if env is None else env
    auth = (["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
            if env.get("GH_TOKEN") else [])
    return ["git", *auth, "push", "origin", ref]


def ensure_final_tag(tag: str, commit: str, claim: dict, *, candidate_manifest_sha256: str | None,
                     docker_manifest_digest: str | None, release_id: int, run=output) -> str:
    """Create or verify the immutable annotated final tag."""
    require_stable_identity(tag, commit)
    expected = _final_metadata(
        tag, commit, claim, candidate_manifest_sha256, docker_manifest_digest, release_id,
    )
    ref = f"refs/tags/{tag}"
    remote_raw = run(["git", "ls-remote", "origin", ref, f"{ref}^{{}}"])
    if not remote_raw:
        try:
            local_object = run(["git", "rev-parse", "--verify", ref])
        except subprocess.CalledProcessError:
            message = json.dumps(expected, sort_keys=True, separators=(",", ":"))
            run([
                "git", "-c", "user.name=Hermes Release Automation",
                "-c", "user.email=release-bot@users.noreply.github.com",
                "tag", "-a", tag, commit, "-m", message,
            ])
        else:
            if (run(["git", "cat-file", "-t", local_object]) != "tag"
                    or run(["git", "rev-parse", f"{ref}^{{commit}}"]) != commit
                    or tag_record(run(["git", "tag", "-l", tag, "--format=%(contents)"])) != expected):
                raise ValueError("Local final tag collision")
        run(authenticated_push(ref))
        remote_raw = run(["git", "ls-remote", "origin", ref, f"{ref}^{{}}"])
    remote = dict(line.split()[::-1] for line in remote_raw.splitlines())
    tag_object, peeled = remote.get(ref), remote.get(f"{ref}^{{}}")
    if not tag_object or peeled != commit:
        raise ValueError("Final stable tag points at the wrong commit or is lightweight")
    try:
        local_object = run(["git", "rev-parse", ref])
    except subprocess.CalledProcessError:
        run(["git", "fetch", "origin", f"{ref}:{ref}"])
        local_object = run(["git", "rev-parse", ref])
    if local_object != tag_object or run(["git", "cat-file", "-t", local_object]) != "tag":
        raise ValueError("Final stable tag object differs from the verified remote")
    metadata = tag_record(run(["git", "tag", "-l", tag, "--format=%(contents)"]))
    if metadata != expected:
        raise ValueError("Final stable tag metadata differs from the accepted artifacts")
    return tag_object


def edit_draft_release(repository: str, release_id: int, tag: str, commit: str, *,
                       run=output) -> None:
    """Retarget the draft onto the receipt tag and strip the warning blocks.

    Immutable releases take no edits after publication, so every edit happens
    here while the release is still a draft, and the tag name, draft flag, and
    body are read back before anything else touches the release. A release that
    is already public is left alone: nothing can repair it.
    """
    endpoint = f"repos/{repository}/releases/{release_id}"
    current = json.loads(run(["gh", "api", endpoint]))
    if current.get("id") != release_id:
        raise ValueError("Stable draft release id changed")
    if (current.get("tag_name") == tag and current.get("draft") is False
            and current.get("prerelease") is False):
        return
    if current.get("draft") is not True:
        raise ValueError("Stable release is no longer a draft and cannot be repaired")
    body = strip_draft_warning(current.get("body") or "")
    run([
        "gh", "api", "--method", "PATCH", endpoint,
        "--raw-field", f"tag_name={tag}", "--raw-field", f"target_commitish={commit}",
        "--raw-field", "make_latest=true",
        "--field", "prerelease=false", "--field", "draft=true",
        "--raw-field", f"body={body}",
    ])
    release = json.loads(run(["gh", "api", endpoint]))
    if (release.get("id") != release_id or release.get("tag_name") != tag
            or release.get("prerelease") is not False or release.get("draft") is not True):
        raise ValueError("Stable draft retarget did not persist")
    # A fence that survives the edit — balanced or not — means the body was
    # changed underneath this call, and the release must not go public.
    if strip_draft_warning(release.get("body") or "") != body:
        raise ValueError("Stable draft body edit did not persist")


def publish_release_draft(repository: str, release_id: int, tag: str, *, run=output) -> None:
    """Make the release public as its own final call.

    Under immutable releases this is the last edit the release ever takes, so
    it runs only after the retarget and the strip have both been read back.
    """
    endpoint = f"repos/{repository}/releases/{release_id}"
    run(["gh", "api", "--method", "PATCH", endpoint, "--field", "draft=false"])
    release = json.loads(run(["gh", "api", endpoint]))
    if (release.get("id") != release_id or release.get("tag_name") != tag
            or release.get("prerelease") is not False or release.get("draft") is not False
            or not release.get("published_at")):
        raise ValueError("Stable release publication did not persist")


def publish_attempt(record: dict, *, repository: str, run=output, read_archive) -> dict:
    """The one ordered publication pass, steps 1-4, each read back before the next.

    Explicit publish and autopublish converge here. The manifest digest is
    hashed from the attempt archive (nothing records it earlier), the receipt
    tag is written, the draft is retargeted and stripped while still a draft,
    and only then does the final call make it public. Returns the two digests
    the receipt binds (candidate manifest and Docker manifest), so the head
    advance that follows in the same pass checks the receipt it just wrote.
    """
    from scripts.releases import docker

    claim = {"claim_tag": record["claim_tag"], "claim_object": record["claim_object"],
             "autopublish": record["autopublish"], "lanes": record["lanes"],
             "claim_epoch": record["claim_epoch"]}
    # A claim that built no bundle staged no archive, so its receipt binds no manifest. One that
    # built no image pushed none, so it binds no digest.
    manifest_sha256 = hashlib.sha256(
        read_archive(f"releases/tag/{record['claim_tag']}/release-candidates.json")
    ).hexdigest() if lane_set.has_bundles(record["lanes"]) else None
    docker_digest = docker.published_digest(record["claim_tag"], run) if "docker" in record["lanes"] else None
    ensure_final_tag(record["tag"], record["commit"], claim,
                     candidate_manifest_sha256=manifest_sha256,
                     docker_manifest_digest=docker_digest,
                     release_id=record["release_id"], run=run)
    edit_draft_release(repository, record["release_id"], record["tag"], record["commit"], run=run)
    publish_release_draft(repository, record["release_id"], record["tag"], run=run)
    return {"candidate_manifest_sha256": manifest_sha256, "docker_manifest_digest": docker_digest}


def complete(env: dict) -> None:
    """Validate the accepted candidate archive. The final tag moves to publish."""
    tag, commit, claim = stable_context(env)
    if not lane_set.has_bundles(claim["lanes"]):
        raise ValueError("A claim that builds no bundle has no candidate archive to validate")
    base = env["CLOUDFLARE_R2_PUBLIC_URL"].rstrip("/")
    candidate = read_candidate(env)
    validate_candidates(candidate, tag, commit, base, claim["claim_epoch"], archive=claim["claim_tag"])

    require_targets_match_claim(candidate, claim["lanes"])


def main(argv: list[str] | None = None, env: dict | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if argv and argv[0] == "gate":
        needs = json.loads(env["RELEASE_NEEDS"])
        summary("\n".join(f"- {name}: {needs.get(name, {}).get('result', 'missing')}" for name in argv[1:]), env)
        require_gate(needs, argv[1:], lanes=lane_set.from_flags(json.loads(env.get("LANES") or "null")))
        return
    commands = {"admit": admit, "verify": verify,
                "candidate-manifest": candidate_manifest, "complete": complete}
    if len(argv) != 1 or argv[0] not in commands:
        raise ValueError("Expected admit, verify, gate, candidate-manifest or complete")
    commands[argv[0]](env)


if __name__ == "__main__":
    main()
