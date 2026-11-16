"""Publish the accepted Termux APT channel independently of graphical clients."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from scripts.releases import commit_build, r2, stable, versioning
from scripts.releases.r2_scope import R2Scope

HEAD_KEY = "releases/termux/stable/release-candidates.json"


def admit_canary(env: dict, *, run=commit_build.output) -> dict[str, str]:
    """Bind a maintainer's default-branch dispatch to its annotated canary receipt."""
    from hermes_cli.update_channel import is_canary_tag

    tag = env.get("RELEASE_TAG", "")
    if (env.get("GITHUB_ACTIONS") != "true" or not is_canary_tag(tag)
            or env.get("RELEASE_PHASE") or env.get("BUILD_COMMIT")
            or env.get("UPLOAD_RELEASE") != "true"):
        raise ValueError("Canary publication requires a tag-only Termux dispatch")
    commit = commit_build.require_commit(run(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"] ))
    tag_object = commit_build.require_commit(run(["git", "rev-parse", f"refs/tags/{tag}^{{tag}}"] ))
    controller = commit_build._controller(env, commit, run=run)
    remote = dict(line.split()[::-1] for line in run([
        "git", "ls-remote", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}",
    ]).splitlines())
    if remote != {f"refs/tags/{tag}": tag_object, f"refs/tags/{tag}^{{}}": commit}:
        raise ValueError("Canary receipt differs from its exact remote tag object")
    run(["git", "merge-base", "--is-ancestor", commit, f"origin/{controller['default']}"])
    published = versioning.published_release_identity(controller["repository"], run=run, channel="canary")
    if published is not None and (published[0].split("+canary.", 1)[1] > tag.split("+canary.", 1)[1]
                                  or published[0] == tag[1:] and published[1] != commit):
        raise ValueError("Canary receipt would replace a newer or different published build")
    release = json.loads(run(["gh", "release", "view", tag, "--repo", controller["repository"],
                              "--json", "tagName,isDraft,isPrerelease"]))
    if (release.get("tagName") != tag or not isinstance(release.get("isDraft"), bool)
            or release.get("isPrerelease") is not True):
        raise ValueError("Canary publication requires its exact GitHub prerelease")
    return {"sha": commit, "tag-object": tag_object}


def publish_canary(env: dict, *, run=commit_build.output) -> dict:
    """Publish source only after payload success and verify the exact release read-back."""
    stable.require_success(json.loads(env.get("RELEASE_NEEDS", "{}")), ["validate", "termux-deb"])
    admitted = admit_canary(env, run=run)
    if (admitted["sha"] != env.get("RELEASE_COMMIT")
            or admitted["tag-object"] != env.get("RELEASE_TAG_OBJECT")):
        raise ValueError("Canary receipt changed after payload admission")
    tag, repository = env["RELEASE_TAG"], env["GITHUB_REPOSITORY"]
    run(["gh", "release", "edit", tag, "--repo", repository,
         "--draft=false", "--prerelease=true", "--latest=false"])
    release = json.loads(run(["gh", "release", "view", tag, "--repo", repository,
                              "--json", "tagName,isDraft,isPrerelease"]))
    if release != {"tagName": tag, "isDraft": False, "isPrerelease": True}:
        raise ValueError("Published canary release did not read back exactly")
    return release


def _read(key: str) -> bytes | None:
    creds, base, bucket = r2.credentials()
    url = R2Scope.configured().object_url(base, bucket, key)
    try:
        response = r2.signed_request("GET", url, creds=creds, now=r2.amz_timestamp())
    except r2.R2RequestError as error:
        if error.status == 404:
            return None
        raise
    if len(response._body) > 4 * 1024 * 1024:
        raise ValueError("Release metadata exceeds size limit")
    return response._body


def read_archive_bytes(key: str) -> bytes:
    body = _read(key)
    if body is None:
        raise ValueError(f"Release archive object is unavailable: {key}")
    return body


def stable_head_version(env: dict) -> str | None:
    body = _read(HEAD_KEY)
    if body is None:
        return None
    candidate = json.loads(body)
    stable.validate_candidates(candidate, candidate["tag"], candidate["commit"],
                               r2.public_base_url(), archive=candidate["archive"])
    return candidate["tag"][1:]


def advance_stable(env: dict, release: dict, root: Path) -> dict:
    """Bind APT promotion to the published final receipt and its immutable archive."""
    from scripts.bundles.release_artifacts import promote

    repository = env["GITHUB_REPOSITORY"]
    workflows = {f"{repository}/.github/workflows/{name}@{stable.CONTROLLER_REF}"
                 for name in ("stable-release.yml", "stable-release-publication.yml")}
    if (env.get("GITHUB_ACTIONS") != "true"
            or env.get("GITHUB_EVENT_NAME") not in {"workflow_dispatch", "workflow_run", "schedule"}
            or env.get("GITHUB_WORKFLOW_REF") not in workflows
            or env.get("R2_DISPOSABLE_RUN") or os.environ.get("R2_DISPOSABLE_RUN")):
        raise ValueError("Termux promotion requires the stable release controller")
    scoped = {**env, "RELEASE_TAG": release["tag"], "RELEASE_COMMIT": release["commit"],
              "RELEASE_CLAIM_TAG": release["claim_tag"],
              "RELEASE_CLAIM_OBJECT": release["claim_object"]}
    tag, commit, claim = stable.final_context(scoped)
    if "termux" not in claim["lanes"]:
        raise ValueError("A claim without Termux cannot advance its APT channel")
    body = read_archive_bytes(f"releases/tag/{claim['claim_tag']}/release-candidates.json")
    digest = hashlib.sha256(body).hexdigest()
    if digest != claim["candidate_manifest_sha256"] or digest != release["candidate_manifest_sha256"]:
        raise ValueError("Termux candidate differs from the published final receipt")
    candidate = json.loads(body)
    base = r2.public_base_url()
    stable.validate_candidates(candidate, tag, commit, base, claim["claim_epoch"], archive=claim["claim_tag"])
    stable.require_targets_match_claim(candidate, claim["lanes"])
    promote(candidate, root, base)
    if json.loads(read_archive_bytes(HEAD_KEY)) != candidate:
        raise ValueError("Termux stable head differs from the accepted candidate")
    return candidate


def main() -> None:
    import sys

    env = dict(os.environ)
    if sys.argv[1:] == ["admit-canary"]:
        result = admit_canary(env)
        with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write("".join(f"{key}={value}\n" for key, value in result.items()))
    elif sys.argv[1:] == ["publish-canary"]:
        result = publish_canary(env)
    else:
        raise SystemExit("usage: python -m scripts.releases.termux {admit-canary|publish-canary}")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
