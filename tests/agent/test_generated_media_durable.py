"""Generated image/video output must survive the gateway media sweep (#126445).

``save_b64_image`` / ``save_*_video`` write the ONLY copy of base64-returning
provider output. The hourly gateway housekeeping deletes 24h-old files from
``cache/images`` / ``cache/videos`` (transient inbound media), so generated
deliverables live in ``cache/generated/<kind>/`` instead — otherwise the
transcript and the Artifacts panel point at deleted files a day later, with no
way to recover the bytes.

RED on main: every test here fails — the writers target the swept caches and
generated media disappears after the sweep.
"""

from __future__ import annotations

import base64
import os
import time


import pytest

from agent.image_gen_provider import save_b64_image
from agent.video_gen_provider import save_b64_video
from gateway.platforms import base

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de00000010494441547801635c0e000000feff03000006000557bfabd400"
    "00000049454e44ae426082"
)


def _backdate_hours(path, hours: float = 25.0) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


@pytest.mark.parametrize(
    "save, sweeper",
    [(save_b64_image, base.cleanup_image_cache), (save_b64_video, base.cleanup_video_cache)],
    ids=["image", "video"],
)
def test_old_generated_media_is_not_swept(tmp_path, monkeypatch, save, sweeper):
    # Profile home so the check-time per-profile cache roots (built from
    # _MEDIA_DELIVERY_CACHE_SUBDIRS) cover it; the import-time roots do not.
    home = tmp_path / "profiles" / "p"
    home.mkdir(parents=True)
    monkeypatch.setattr(base, "_HERMES_ROOT", tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    path = save(base64.b64encode(PNG_1PX).decode(), prefix="red_test")
    _backdate_hours(path)
    removed = sweeper(max_age_hours=24)
    assert path.exists(), f"25h-old generated media was swept ({removed} removed): {path}"

    # Still deliverable on a strict gateway with recency trust off: only the
    # cache allowlist can vouch for a day-old file.
    monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "1")
    monkeypatch.setenv("HERMES_MEDIA_TRUST_RECENT_FILES", "0")
    assert base.validate_media_delivery_path(str(path)) == str(path.resolve())
