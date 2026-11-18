"""Receipt-bound native candidate integrity before any channel publication."""
import copy

import pytest

from scripts.bundles import release_artifacts as artifacts
from scripts.releases import handoff
from tests.scripts.test_termux_release import apt_release, accepted


@pytest.mark.parametrize("invalid", ["missing", "metadata"])
def test_assemble_rejects_missing_receipt_and_changed_metadata(tmp_path, apt_release, invalid):
    _, claim, _, writes, base = apt_release
    fetched = tmp_path / "fetched"
    handoff.fetch(claim["claim_tag"], claim["commit"], ["termux"], fetched, ["metadata-*.json"])
    if invalid == "missing":
        (fetched / handoff.receipt_name("termux")).unlink()
    else:
        (fetched / "metadata-termux-aarch64.json").write_text("{}")
    writes.clear()
    with pytest.raises(ValueError, match="handoff|digest"):
        artifacts.assemble(fetched, claim["tag"], claim["commit"], base, tmp_path / "candidate.json",
                           release_epoch=claim["claim_epoch"], archive=claim["claim_tag"],
                           lanes=claim["lanes"])
    assert not writes


@pytest.mark.parametrize("invalid", ["digest", "url", "duplicate"])
def test_publish_rejects_invalid_candidate_before_any_write(tmp_path, monkeypatch, apt_release, invalid):
    _, _, _, writes, base = apt_release
    candidate = copy.deepcopy(accepted(apt_release, monkeypatch))
    if invalid == "digest":
        candidate["packages"][0]["artifact"]["sha256"] = "f" * 64
    elif invalid == "url":
        candidate["packages"][0]["artifact"]["url"] = "https://other.example/package.deb"
    else:
        candidate["files"].append(copy.deepcopy(candidate["files"][0]))
    writes.clear()
    with pytest.raises(ValueError):
        artifacts.publish(candidate, tmp_path / "published", base)
    assert not writes


def test_cli_rejects_unpinned_candidate_before_materialization(tmp_path, monkeypatch, apt_release):
    _, claim, _, writes, base = apt_release
    accepted(apt_release, monkeypatch)
    monkeypatch.setenv("CANDIDATE_MANIFEST_SHA256", "f" * 64)
    root = tmp_path / "rejected"
    writes.clear()
    with pytest.raises(ValueError, match="digest mismatch"):
        artifacts.main(["materialize", "--root", str(root), "--tag", claim["tag"],
                        "--archive", claim["claim_tag"], "--commit", claim["commit"],
                        "--public-base", base])
    assert not root.exists() and not writes


def test_corrupt_index_prevents_promotion_before_any_write(tmp_path, monkeypatch, apt_release):
    _, claim, store, writes, base = apt_release
    candidate = accepted(apt_release, monkeypatch)
    store[f"releases/tag/{claim['claim_tag']}/apt/dists/hermes-stable/InRelease"] = b"tampered"
    writes.clear()
    with pytest.raises(ValueError, match="digest mismatch"):
        artifacts.promote(candidate, tmp_path / "rejected", base)
    assert not writes
