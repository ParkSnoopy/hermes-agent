"""Inbound attachments on a multiplexed gateway live in the ROUTED profile's cache (#101134).

Adapters cache attachments before routing; rehoming moves them into the routed profile's
local cache. Real temp homes A (launch) and B (routed), multiplex on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import hermes_yaml as yaml

from agent import secret_scope as ss
from gateway.platforms.base import cache_media_bytes
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import _profile_runtime_scope
from gateway.run_inbound import GatewayInboundMixin
from gateway.run_inbound_media import rehome_inbound_media


@pytest.fixture
def two_homes(tmp_path, monkeypatch):
    """Launch home A and routed profile B, both using local files."""
    a = tmp_path / ".hermes"
    b = a / "profiles" / "b"
    b.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(a))
    for home in (a, b):
        (home / "config.yaml").write_text(
            yaml.safe_dump({"terminal": {"backend": "local"}}), encoding="utf-8")
    ss.set_multiplex_active(True)
    yield a, b
    ss.set_multiplex_active(False)


def _adapter_cached(home: Path, kind: str, name: str) -> str:
    """What an adapter leaves behind: a file under ``<home>/cache/<kind>`` (host path)."""
    path = home / "cache" / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 x" if kind == "documents" else b"\xff\xd8\xff\xe0 jpeg")
    return str(path)


def test_routed_turn_rehomes_adapter_cached_attachments_a_b_a(two_homes):
    a, b = two_homes
    doc, img = _adapter_cached(a, "documents", "doc_0123456789ab_report.pdf"), _adapter_cached(a, "images", "img_1.jpg")
    event = MessageEvent(text=f"[document 'report.pdf' saved at: {doc}]", message_type=MessageType.DOCUMENT,
                         source=None, media_urls=[doc, img], media_types=["application/pdf", "image/jpeg"])

    with _profile_runtime_scope(b):
        rehome_inbound_media(event)
        note = GatewayInboundMixin._prepend_inbound_document_notes(event, "")
        host = b / "cache" / "documents" / "doc_0123456789ab_report.pdf"
        assert host.is_file()
        assert str(host) in note
        assert str(host) in event.text
    assert event.media_urls == [str(b / "cache" / "documents" / "doc_0123456789ab_report.pdf"),
                                str(b / "cache" / "images" / "img_1.jpg")]
    assert not Path(doc).exists() and not Path(img).exists()  # moved, not copied: A holds nothing of B's
    assert (b / "cache" / "images" / "img_1.jpg").is_file()

    # A→B→A: a launch-profile turn keeps its own files where they are (no-op, no bleed into B).
    doc_a = _adapter_cached(a, "documents", "doc_aaaaaaaaaaaa_own.pdf")
    own = MessageEvent(text="", message_type=MessageType.DOCUMENT, source=None,
                       media_urls=[doc_a], media_types=["application/pdf"])
    with _profile_runtime_scope(a):
        rehome_inbound_media(own)
    assert own.media_urls == [doc_a] and Path(doc_a).is_file()
    assert not (b / "cache" / "documents" / "doc_aaaaaaaaaaaa_own.pdf").exists()


def test_cache_media_bytes_returns_local_path_in_note(two_homes):
    """The cached path and rendered note use the routed profile's local path."""
    _, b = two_homes
    with _profile_runtime_scope(b):
        cached = cache_media_bytes(b"%PDF-1.4 x", filename="report.pdf", mime_type="application/pdf")
        assert Path(cached.path).is_file() and Path(cached.path).parent == b / "cache" / "documents"
        assert str(cached.path) in cached.context_note()
