"""Native media keeps the named store and rejects linked digest directories."""
import hashlib
from pathlib import Path

import pytest

from gateway import session_ingress_media as media
from hermes_state_runtime import RuntimeStoreError


def test_capture_preserves_named_store_provenance(tmp_path, monkeypatch):
    home, outside = tmp_path / "home", tmp_path / "outside"
    (home / "cache").mkdir(parents=True)
    (outside / "native-inputs").mkdir(parents=True)
    (home / "cache" / "documents").symlink_to(outside, target_is_directory=True)
    source = tmp_path / "sample.txt"
    source.write_text("native-media sentinel")
    monkeypatch.setenv("HERMES_HOME", str(home))
    calls = []
    mkdir = media.make_user_dir

    def record(path, *args, **kwargs):
        calls.append(Path(path))
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(media, "make_user_dir", record)
    references = media.capture_native_media([source])
    assert Path(references[0]["path"]).is_file()
    store = home / "cache" / "documents" / "native-inputs"
    assert calls == [store, store / references[0]["sha256"]]


def test_capture_rejects_linked_digest_directory(tmp_path, monkeypatch):
    home = tmp_path / "home"
    store = home / "cache" / "documents" / "native-inputs"
    store.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    source = tmp_path / "sample.txt"
    source.write_text("bytes")
    digest = hashlib.sha256(b"bytes").hexdigest()
    (store / digest).symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    with pytest.raises(RuntimeStoreError):
        media.capture_native_media([source])
    assert list(outside.iterdir()) == []
