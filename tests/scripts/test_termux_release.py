"""Termux candidate admission and APT publication, with transport mocked and no servers."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from scripts.bundles import release_artifacts
from scripts.releases import handoff, r2, stable, termux


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb is required for package metadata verification")
@pytest.mark.parametrize("invalid", [None, "version", "architecture", "stamp"])
def test_record_reads_the_deb_and_refuses_wrong_provenance(tmp_path, invalid):
    root = tmp_path / "package"
    control = root / "DEBIAN/control"
    control.parent.mkdir(parents=True)
    version = "1.2.4-1" if invalid == "version" else "1.2.3-1"
    arch = "amd64" if invalid == "architecture" else "aarch64"
    control.write_text(f"Package: hermes-agent\nVersion: {version}\nArchitecture: {arch}\n"
                       "Maintainer: Test Fixture <fixture@example.test>\nDescription: Provenance test fixture\n")
    stamp = root / "usr/lib/hermes/install-stamp.json"
    stamp.parent.mkdir(parents=True)
    commit = "b" * 40 if invalid == "stamp" else "a" * 40
    stamp.write_text(json.dumps({"tag": "v1.2.3", "commit": commit}))
    built = tmp_path / "built"
    (built / "deb").mkdir(parents=True)
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root), str(built / "deb/hermes.deb")],
                   check=True, capture_output=True, timeout=30)
    out = built / "metadata-termux-aarch64.json"
    if invalid is None:
        release_artifacts.record("termux", "aarch64", built, "v1.2.3", "a" * 40, out)
        assert json.loads(out.read_text()) == {
            "platform": "termux", "arch": "aarch64", "tag": "v1.2.3", "commit": "a" * 40,
            "identity": "hermes-agent", "version": "1.2.3-1", "filename": "deb/hermes.deb"}
    else:
        with pytest.raises(ValueError):
            release_artifacts.record("termux", "aarch64", built, "v1.2.3", "a" * 40, out)
        assert not out.exists()


@pytest.fixture
def apt_release(tmp_path, monkeypatch):
    tag, commit, archive = "v1.2.3", "a" * 40, "rc.1-v1.2.3"
    base = "https://artifacts.example.test"
    store, writes = {}, []

    def put(*, tag, key, file, key_is_full=False, immutable=False):
        key = key if key_is_full else r2.staging_key_for(tag, key)
        body = Path(file).read_bytes()
        if immutable and key in store and store[key] != body:
            raise ValueError("Immutable fixture object changed")
        store[key] = body
        writes.append(key)

    def get(creds, origin, bucket, key, now):
        return store[key].decode() if key in store else None

    def download(creds, origin, bucket, key, file, now, *, expected_size, expected_sha256):
        body = store[key]
        assert len(body) == expected_size
        assert hashlib.sha256(body).hexdigest() == expected_sha256
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(body)

    def urlopen(url, **kwargs):
        assert url.startswith(base + "/"), url
        class Response(io.BytesIO):
            def geturl(self):
                return url
        return Response(store[url.removeprefix(base + "/")])

    def signed_request(method, url, **kwargs):
        assert method == "GET"
        key = urlsplit(url).path.removeprefix("/bucket/")
        if key not in store:
            raise r2.R2RequestError(method, key, 404)
        return r2.Response(200, [], store[key])

    monkeypatch.setattr(r2, "put", put)
    monkeypatch.setattr(r2, "get_object", get)
    monkeypatch.setattr(r2, "download_object", download)
    monkeypatch.setattr(r2, "signed_request", signed_request)
    monkeypatch.setattr(r2, "credentials", lambda: ({}, "https://storage.example.test", "bucket"))
    monkeypatch.setattr(r2, "public_base_url", lambda: base)
    monkeypatch.setattr(release_artifacts.urllib.request, "urlopen", urlopen)
    monkeypatch.delenv("R2_DISPOSABLE_RUN", raising=False)
    built = tmp_path / "built"
    for name in ("deb/hermes.deb", "apt/pool/rc.1-v1.2.3/hermes.deb", "apt/key.asc",
                 "apt/dists/hermes-stable/main/binary-aarch64/Packages",
                 "apt/dists/hermes-stable/main/binary-aarch64/by-hash/SHA256/index",
                 "apt/dists/hermes-stable/Release", "apt/dists/hermes-stable/InRelease"):
        file = built / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(f"transport fixture: {name}".encode())
    metadata = {"platform": "termux", "arch": "aarch64", "tag": tag, "commit": commit,
                "identity": "hermes-agent", "version": "1.2.3-1", "filename": "deb/hermes.deb"}
    (built / "metadata-termux-aarch64.json").write_text(json.dumps(metadata))
    handoff.stage(archive, commit, "termux", built, ["deb/*.deb", "apt/**/*", "metadata-*.json"])
    claim = {"tag": tag, "commit": commit, "claim_tag": archive, "claim_object": "b" * 40,
             "claim_epoch": 1_787_965_323, "lanes": frozenset({"termux"})}
    monkeypatch.setattr(stable, "stable_context", lambda env: (tag, commit, claim))
    env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_ACTIONS": "true",
           "GITHUB_EVENT_NAME": "workflow_dispatch",
           "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/stable-release.yml@refs/heads/main",
           "RELEASE_NEEDS": json.dumps({"candidates-termux": {"result": "success"}}),
           "CLOUDFLARE_R2_PUBLIC_URL": base, "GITHUB_OUTPUT": str(tmp_path / "outputs")}
    return env, claim, store, writes, base


def accepted(apt_release, monkeypatch):
    env, claim, store, _, _ = apt_release
    stable.candidate_manifest(env)
    body = store[f"releases/tag/{claim['claim_tag']}/release-candidates.json"]
    claim["candidate_manifest_sha256"] = hashlib.sha256(body).hexdigest()
    monkeypatch.setattr(stable, "final_context", lambda env: (claim["tag"], claim["commit"], claim))
    return json.loads(body)


def test_termux_candidate_publishes_and_advances_apt_without_desktop(tmp_path, monkeypatch, apt_release):
    env, claim, store, writes, base = apt_release
    candidate = accepted(apt_release, monkeypatch)
    assert candidate["schema"] == 3 and "smoke_results" not in candidate
    assert {f"{row['platform']}/{row['arch']}" for row in candidate["packages"]} == {"termux/aarch64"}
    assert termux.stable_head_version(env) is None
    writes.clear()
    release_artifacts.publish(candidate, tmp_path / "published", base)
    assert set(writes) == {"releases/termux/stable/dists/hermes-stable/main/binary-aarch64/by-hash/SHA256/index",
                           "releases/termux/stable/pool/rc.1-v1.2.3/hermes.deb"}
    writes.clear()
    assert termux.advance_stable(env, claim, tmp_path / "promoted") == candidate
    assert termux.stable_head_version(env) == "1.2.3"
    assert writes[-2:] == ["releases/termux/stable/dists/hermes-stable/InRelease", termux.HEAD_KEY]
    assert json.loads(store[termux.HEAD_KEY]) == candidate
    assert all(key.startswith("releases/termux/stable/") for key in writes)


@pytest.mark.parametrize("result", ["failure", "skipped", None])
def test_failed_or_missing_selected_candidate_never_stages_manifest(apt_release, result):
    env, _, store, _, _ = apt_release
    env["RELEASE_NEEDS"] = json.dumps({"candidates-termux": {"result": result}})
    with pytest.raises(ValueError, match="blocked"):
        stable.candidate_manifest(env)
    assert not any(key.endswith("release-candidates.json") for key in store)


@pytest.mark.parametrize("invalid", ["digest", "claim", "workflow", "disposable"])
def test_apt_advance_refuses_invalid_custody_before_any_write(tmp_path, monkeypatch, apt_release, invalid):
    env, claim, store, writes, _ = apt_release
    accepted(apt_release, monkeypatch)
    if invalid == "digest":
        store[f"releases/tag/{claim['claim_tag']}/release-candidates.json"] += b" "
    elif invalid == "claim":
        claim["lanes"] = frozenset({"docker"})
    elif invalid == "workflow":
        env["GITHUB_WORKFLOW_REF"] = "o/r/.github/workflows/termux-release.yml@refs/heads/main"
    else:
        env["R2_DISPOSABLE_RUN"] = "12"
    writes.clear()
    with pytest.raises(ValueError):
        termux.advance_stable(env, claim, tmp_path / "rejected")
    assert writes == [] and termux.HEAD_KEY not in store


@pytest.mark.parametrize("invalid", ["schema", "target", "epoch", "archive", "identity", "version", "artifact", "retired-field", "retired-row-field"])
def test_current_termux_manifest_rejects_wrong_identity(monkeypatch, apt_release, invalid):
    _, claim, _, _, base = apt_release
    candidate = copy.deepcopy(accepted(apt_release, monkeypatch))
    if invalid == "schema":
        candidate["schema"] = 2
    elif invalid == "target":
        candidate["packages"][0]["platform"] = "windows"
    elif invalid == "epoch":
        candidate["releaseEpoch"] = True
    elif invalid == "archive":
        candidate["archive"] = "rc.2-v1.2.3"
    elif invalid == "version":
        candidate["packages"][0]["version"] = "1.2.4-1"
    elif invalid == "identity":
        candidate["packages"][0]["identity"] = "hermes-desktop"
    elif invalid == "retired-row-field":
        candidate["packages"][0]["receiverProtocol"] = 1
    elif invalid == "retired-field":
        candidate["smoke_results"] = {}
    else:
        candidate["packages"][0]["artifact"]["url"] = base + "/outside/hermes.deb"
    with pytest.raises(ValueError):
        stable.validate_candidates(candidate, claim["tag"], claim["commit"], base,
                                   claim["claim_epoch"], archive=claim["claim_tag"])


def test_corrupted_candidate_bytes_never_publish_apt(tmp_path, monkeypatch, apt_release):
    _, claim, store, writes, base = apt_release
    candidate = accepted(apt_release, monkeypatch)
    store[f"releases/tag/{claim['claim_tag']}/deb/hermes.deb"] = b"changed transport fixture"
    writes.clear()
    with pytest.raises(ValueError, match="digest mismatch"):
        release_artifacts.publish(candidate, tmp_path / "rejected", base)
    assert writes == []


@pytest.mark.parametrize("selected,apt_head,docker_digest,expected", [
    ({"termux"}, "1.2.3", None, "1.2.3"),
    ({"docker"}, None, "sha256:" + "c" * 64, "1.2.3"),
    ({"termux", "docker"}, "1.2.3", "sha256:" + "c" * 64, "1.2.3"),
    ({"termux", "docker"}, "1.2.3", None, None),
    ({"termux", "docker"}, None, "sha256:" + "c" * 64, None),
    ({"ci"}, None, None, "1.2.3"),
])
def test_publication_finishes_only_when_every_selected_channel_reaches_it(selected, apt_head, docker_digest, expected):
    from scripts.releases import sequencer

    record = {"state": "published", "version": "1.2.3", "lanes": selected,
              "docker_manifest_digest": "sha256:" + "c" * 64}
    assert sequencer.channel_head(apt_head, [record], docker_digest) == expected


@pytest.fixture
def canary_release():
    """Git/GitHub command-boundary facts; no network, servers or real publication."""
    tag = "v1.2.3+canary.20260818T103000Z"
    commit, tag_object = "a" * 40, "b" * 40
    env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
           "GITHUB_REPOSITORY": "o/r", "GITHUB_ACTOR": "maintainer",
           "GITHUB_REF": "refs/heads/main", "DEFAULT_BRANCH": "main",
           "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/termux-release.yml@refs/heads/main",
           "RELEASE_TAG": tag, "UPLOAD_RELEASE": "true",
           "RELEASE_COMMIT": commit, "RELEASE_TAG_OBJECT": tag_object,
           "RELEASE_NEEDS": json.dumps({name: {"result": "success"} for name in ("validate", "termux-deb")})}
    state = {"invalid": None, "draft": True}
    calls = []

    def run(argv, repo=None):
        calls.append(argv)
        invalid = state["invalid"]
        if argv[:2] == ["git", "rev-parse"]:
            return commit if argv[-1].endswith("^{commit}") else tag_object
        if argv[:2] == ["git", "ls-remote"]:
            if argv[3:] == ["refs/heads/*", "refs/tags/*"]:
                return f"{commit}\trefs/heads/main"
            remote_object = "c" * 40 if invalid == "remote-tag" else tag_object
            return f"{remote_object}\trefs/tags/{tag}\n{commit}\trefs/tags/{tag}^{{}}"
        if argv[:2] == ["git", "for-each-ref"]:
            return commit
        if argv[:2] == ["git", "merge-base"]:
            if invalid == "ancestry":
                raise subprocess.CalledProcessError(1, argv)
            return ""
        if argv[:2] == ["gh", "api"]:
            if argv[-1] == ".permission":
                return "read" if invalid == "permission" else "write"
            if "--paginate" in argv:
                published_tag = "v1.2.3+canary.20260819T103000Z" if invalid == "newer" else tag
                rows = [] if state["draft"] and invalid not in {"newer", "published-commit"} else [
                    {"tag_name": published_tag, "draft": False, "prerelease": True}]
                return json.dumps([rows])
            if argv[-1] == ".sha":
                return "d" * 40 if invalid == "published-commit" else commit
        if argv[:3] == ["gh", "release", "edit"]:
            if invalid != "read-back":
                state["draft"] = False
            return ""
        if argv[:3] == ["gh", "release", "view"]:
            return json.dumps({"tagName": "wrong-tag" if invalid == "release" else tag,
                               "isDraft": state["draft"], "isPrerelease": True})
        raise AssertionError(argv)

    return env, state, calls, run


def test_canary_admission_and_publication_are_replayable_and_read_back(canary_release, tmp_path, monkeypatch):
    import sys

    env, _, calls, run = canary_release
    assert termux.admit_canary(env, run=run) == {"sha": env["RELEASE_COMMIT"], "tag-object": env["RELEASE_TAG_OBJECT"]}
    for _ in range(2):
        assert termux.publish_canary(env, run=run) == {"tagName": env["RELEASE_TAG"], "isDraft": False, "isPrerelease": True}
        assert calls[-2][:3] == ["gh", "release", "edit"] and calls[-1][:3] == ["gh", "release", "view"]
    output = tmp_path / "outputs"
    for key, value in {**env, "GITHUB_OUTPUT": str(output)}.items():
        monkeypatch.setenv(key, value)
    original_admit = termux.admit_canary
    monkeypatch.setattr(termux, "admit_canary", lambda values: original_admit(values, run=run))
    monkeypatch.setattr(sys, "argv", ["termux.py", "admit-canary"])
    termux.main()
    assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == {
        "sha": env["RELEASE_COMMIT"], "tag-object": env["RELEASE_TAG_OBJECT"]}


@pytest.mark.parametrize("invalid", ["workflow", "permission", "remote-tag", "ancestry", "release",
                                     "newer", "published-commit", "commit", "tag-object", "phase", "build",
                                     "upload", "stable-tag", "failure", "skipped", "missing", "read-back"])
def test_canary_refuses_invalid_custody_or_payload_results(canary_release, invalid):
    env, state, calls, run = canary_release
    state["invalid"] = invalid
    changes = {"workflow": ("GITHUB_WORKFLOW_REF", "o/r/.github/workflows/termux-release.yml@refs/heads/topic"),
               "commit": ("RELEASE_COMMIT", "c" * 40), "tag-object": ("RELEASE_TAG_OBJECT", "c" * 40),
               "phase": ("RELEASE_PHASE", "candidate"), "build": ("BUILD_COMMIT", "c" * 40),
               "upload": ("UPLOAD_RELEASE", "false"), "stable-tag": ("RELEASE_TAG", "v1.2.3")}
    if invalid in changes:
        key, value = changes[invalid]
        env[key] = value
    if invalid in {"failure", "skipped", "missing"}:
        env["RELEASE_NEEDS"] = json.dumps({"validate": {"result": "success"},
                                           **({"termux-deb": {"result": invalid}} if invalid != "missing" else {})})
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        termux.publish_canary(env, run=run)
    edits = [call for call in calls if call[:3] == ["gh", "release", "edit"]]
    assert len(edits) == (1 if invalid == "read-back" else 0)
