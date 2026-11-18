"""Record, stage and promote the exact Termux release artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os

import re

import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Iterable

from scripts.releases import lanes as lane_set
from scripts.releases.stable import read_admitted_candidate, validate_candidates


def sha256_file(file: Path) -> str:
    with file.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()



def single(items):
    items = list(items)
    if len(items) != 1:
        raise ValueError(f"Expected exactly one artifact, found {items}")
    return items[0]


def record(platform: str, arch: str, root: Path, tag: str, commit: str, out: Path) -> None:
    """Read identities from the built packages, never from the workflow matrix."""
    row: dict = {"platform": platform, "arch": arch, "tag": tag, "commit": commit}
    if platform == "termux" and arch == "aarch64":
        package = single((root / "deb").glob("*.deb"))
        fields = subprocess.check_output(["dpkg-deb", "--field", str(package), "Package", "Version", "Architecture"], text=True, encoding="utf-8")
        parsed = dict(line.split(": ", 1) for line in fields.splitlines())
        row.update(identity=parsed["Package"], version=parsed["Version"], filename=package.relative_to(root).as_posix())
        if parsed["Version"] != f"{tag[1:]}-1":
            raise ValueError("Termux artifact version differs from release tag")
        if parsed["Architecture"] != "aarch64":
            raise ValueError("Wrong Termux package architecture")
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run(["dpkg-deb", "--extract", str(package), temp], check=True)
            stamps = list(Path(temp).rglob("install-stamp.json"))
            if not any((data := json.loads(p.read_text(encoding="utf-8-sig"))).get("commit") == commit and data.get("tag") == tag for p in stamps):
                raise ValueError("Termux package has no matching provenance")
    else:
        raise ValueError("Unknown platform")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(row, sort_keys=True, indent=2) + "\n", encoding="utf-8")



ALL_HANDOFFS = ("termux",)


def assemble(root: Path, tag: str, commit: str, public_base: str, out: Path,
             *, release_epoch: int, archive: str,
             lanes: Iterable[str]) -> dict:
    """Bind the native metadata to files already staged by their build jobs.

    `tag` stays the plain payload identity; `archive` is the attempt ref the
    manifest and every artifact URL are keyed under. `lanes` are the claim's:
    a handoff group a lane did not build must have no target in the manifest.
    """
    from scripts.releases.handoff import receipt_name, validate_receipt
    from scripts.releases.r2 import put, staging_key_for

    lanes = frozenset(lanes)
    expected = lane_set.handoffs(lanes, ALL_HANDOFFS)
    if not expected:
        raise ValueError("Invalid candidate handoff set: the lanes built no package")
    absent = frozenset(lane_set.TARGET_LANE) - lane_set.targets(lanes)

    by_name = {}
    for name in expected:
        file = root / receipt_name(name)
        if not file.is_file():
            raise ValueError(f"Missing candidate handoff: {name}")
        receipt = json.loads(file.read_text(encoding="utf-8-sig"))
        # Handoffs are staged under the archive ref; their bound tag is the
        # archive, not the plain payload tag.
        for row in validate_receipt(receipt, archive, commit, name):
            prior = by_name.get(row["path"])
            if prior is not None and prior != row:
                raise ValueError("Candidate handoffs disagree on file receipts")
            by_name[row["path"]] = row
    # Package bytes stay bound by their receipt digests; publish() downloads and
    # checks them before anything goes live.
    rows = []
    for name, receipt in sorted(by_name.items()):
        if name.startswith("metadata-"):
            file = root / name
            if not file.is_file() or sha256_file(file) != receipt["sha256"]:
                raise ValueError(f"Candidate local digest mismatch: {name}")
            rows.append(json.loads(file.read_text(encoding="utf-8-sig")))

    files = [{**row, "url": f"{public_base.rstrip('/')}/{staging_key_for(archive, name)}"}
             for name, row in sorted(by_name.items()) if not name.startswith("metadata-")]
    by_name = {item["path"]: item for item in files}
    packages = []
    for row in rows:
        # record() verified each package's own stamp; a row is metadata and carries no baseVersion.
        if row.get("commit") != commit or row.get("tag") != tag:
            raise ValueError("Built package provenance does not match the release")
        filename = row["filename"]
        item = by_name[filename]
        packages.append({k: v for k, v in {**row, "artifact": {"url": item["url"], "sha256": item["sha256"]}}.items() if k != "filename"})
    result = {"schema": 3, "tag": tag, "commit": commit, "releaseEpoch": release_epoch,
              "archive": archive,
              "packages": packages, "files": files}
    validate_candidates(result, tag, commit, public_base, release_epoch, archive=archive)
    present = {f"{row['platform']}/{row['arch']}" for row in packages}
    if present != set(lane_set.TARGET_LANE) - absent:
        raise ValueError("Candidate package rows differ from the built handoffs")
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    put(tag=archive, key="release-candidates.json", file=out, immutable=True)
    return result


# Package payloads: receipt-bound by sha256, never needed on disk to write feeds.
PACKAGE_SUFFIXES = (".deb",)


def materialize(manifest: dict, root: Path, *, public_base: str,
                metadata_only: bool = False) -> None:
    validate_candidates(manifest, manifest["tag"], manifest["commit"], public_base,
                        archive=manifest["archive"])
    files = manifest.get("files", [])
    if not files or len({item["path"] for item in files}) != len(files):
        raise ValueError("Missing or duplicate candidate file receipts")
    by_url = {item["url"]: item["sha256"] for item in files}
    if any(by_url.get(row["artifact"]["url"]) != row["artifact"]["sha256"] for row in manifest["packages"]):
        raise ValueError("Package receipts differ from candidate file receipts")
    selected = files
    if metadata_only:
        selected = [item for item in selected if not item["path"].endswith(PACKAGE_SUFFIXES)]

    from scripts.releases.progress import log

    total = sum(item.get("size", 0) for item in selected)
    log(f"Materializing {len(selected)} candidate files ({total / 1e9:.1f} GB) into {root}")
    for number, item in enumerate(selected, start=1):
        log(f"  [{number}/{len(selected)}] {item['path']} ({item.get('size', 0) / 1e6:.0f} MB)")
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts or any(c in item["path"] for c in "\\:%?#") or item["path"].startswith("/") or "//" in item["path"]:
            raise ValueError("Invalid artifact path")
        expected = f"{public_base.rstrip('/')}/releases/tag/{manifest['archive']}/{relative.as_posix()}"
        if item["url"] != expected or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise ValueError("Invalid artifact URL or digest")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with urllib.request.urlopen(item["url"], timeout=600) as response, target.open("wb") as file:
            if not response.geturl().startswith("https://"):
                raise ValueError("Artifact redirected outside HTTPS")
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                file.write(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError(f"Artifact digest mismatch: {relative}")


def publish(manifest: dict, root: Path, public_base: str) -> None:
    """Verify versioned uploads and stage APT content-addressed files, not indexes."""
    from scripts.releases.r2 import put

    materialize(manifest, root, public_base=public_base)

    for file in (root / "apt").rglob("*"):
        rel = file.relative_to(root / "apt").as_posix()
        if file.is_file() and (rel.startswith("pool/") or "/by-hash/" in rel):
            put(tag=manifest["tag"], key=f"releases/termux/stable/{rel}", file=file, key_is_full=True, immutable=True)



def promote(manifest: dict, root: Path, public_base: str) -> None:
    from scripts.releases.r2 import put
    from scripts.releases.termux import HEAD_KEY

    # APT indexes are receipt-bound; package bytes were verified before publication.
    materialize(manifest, root, public_base=public_base, metadata_only=True)

    apt = root / "apt"
    indexes = sorted(p for p in apt.rglob("*") if p.is_file() and not p.relative_to(apt).as_posix().startswith("pool/") and "/by-hash/" not in p.relative_to(apt).as_posix())
    indexes.sort(key=lambda p: p.name == "InRelease")
    # Termux is opt-in: a release that did not build it leaves the APT channel
    # on the previous Termux release, and ships no indexes to publish.
    termux = any(row["platform"] == "termux" for row in manifest["packages"])
    if termux and not any(p.name == "InRelease" for p in indexes):
        raise ValueError("Missing signed APT index")
    if not termux and indexes:
        raise ValueError("APT indexes staged for a release without a Termux candidate")

    def publish_pointer(key, file):
        put(tag=manifest["tag"], key=key, file=file, key_is_full=True)
        digest = hashlib.sha256()
        with urllib.request.urlopen(f"{public_base.rstrip('/')}/{key}", timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != sha256_file(file):
            raise ValueError(f"Channel read-back differs: {key}")

    for file in indexes:
        publish_pointer(f"releases/termux/stable/{file.relative_to(apt).as_posix()}", file)
    head = root / "release-candidates.json"
    head.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    publish_pointer(HEAD_KEY, head)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["record", "publish", "promote", "materialize"])
    parser.add_argument("--platform", choices=["termux"])
    parser.add_argument("--arch", choices=["aarch64"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG"))
    parser.add_argument("--archive", default=os.environ.get("HERMES_ARCHIVE_TAG"),
                        help="Attempt ref the release archive is keyed under when the payload tag is plain vX.Y.Z")
    # A stable run dispatches from the default branch, so GITHUB_SHA is not the admitted claim commit.
    parser.add_argument("--commit", default=os.environ.get("RELEASE_COMMIT") or os.environ.get("GITHUB_SHA"))
    parser.add_argument("--public-base", default=os.environ.get("CLOUDFLARE_R2_PUBLIC_URL"))
    args = parser.parse_args(argv)
    if args.command == "record":
        if not args.out or not args.platform or not args.arch:
            parser.error("record requires --out, --platform and --arch")
        record(args.platform, args.arch, args.root, args.tag, args.commit, args.out)
    else:
        if not args.archive:
            parser.error(f"{args.command} requires the admitted --archive ref")
        manifest = read_admitted_candidate(args.tag, args.commit, args.public_base,
                                          os.environ.get("CANDIDATE_MANIFEST_SHA256", ""),
                                          archive=args.archive)
        if args.command == "materialize":
            materialize(manifest, args.root, public_base=args.public_base)
        else:
            {"publish": publish, "promote": promote}[args.command](manifest, args.root, args.public_base)


if __name__ == "__main__":
    main()
