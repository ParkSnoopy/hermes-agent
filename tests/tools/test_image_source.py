"""Tests for tools/image_source.py — the unified vision image-source resolver.

Covers local files, data URLs, HTTP sources, size caps and magic-byte sniffing.
"""

import base64
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# Minimal valid 1x1 PNG bytes. Resolver validation requires a decodable fixture.
PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
CORRUPT_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAIAAAACUFjqAAAAFElEQVR4nGP8z8Dwn4EIwESJ5gAAVQ4CH1evYJQAAAAASUVORK5CYII="
)


def _reload(monkeypatch, hermes_home: Path):
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    import hermes_constants
    importlib.reload(hermes_constants)
    import tools.image_source as isrc
    importlib.reload(isrc)
    return isrc




class TestDataUrl:
    @pytest.mark.asyncio
    async def test_valid_data_url_resolves_to_bytes(self, tmp_path, monkeypatch):
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        b64 = base64.b64encode(PNG).decode()
        res = await isrc.resolve_image_source(
            f"data:image/png;base64,{b64}", isrc.ResolveContext())
        assert res.data == PNG
        assert res.mime == "image/png"
        assert res.origin == "data"

    @pytest.mark.asyncio
    async def test_non_image_data_url_rejected(self, tmp_path, monkeypatch):
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        b64 = base64.b64encode(b"not an image").decode()
        with pytest.raises(isrc.NotAnImage):
            await isrc.resolve_image_source(
                f"data:text/plain;base64,{b64}", isrc.ResolveContext())

    @pytest.mark.asyncio
    async def test_corrupt_png_rejected_at_resolver_boundary(self, tmp_path, monkeypatch):
        """A PNG-shaped but undecodable payload never becomes a resolved image."""
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        monkeypatch.setenv("TERMINAL_ENV", "local")
        img = tmp_path / "corrupt.png"
        img.write_bytes(CORRUPT_PNG)
        with pytest.raises(isrc.NotAnImage):
            await isrc.resolve_image_source(str(img), isrc.ResolveContext())


class TestLocalBackend:
    @pytest.mark.asyncio
    async def test_local_backend_reads_any_host_path(self, tmp_path, monkeypatch):
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        monkeypatch.setenv("TERMINAL_ENV", "local")
        img = tmp_path / "outside" / "pic.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(PNG)
        res = await isrc.resolve_image_source(str(img), isrc.ResolveContext())
        assert res.data == PNG
        assert res.origin == "file"


    @pytest.mark.asyncio
    async def test_bare_relative_path_resolves(self, tmp_path, monkeypatch):
        """A cwd-relative bare filename ('pic.png') is a valid local source —
        main accepted it; the resolver must not regress it (PR review)."""
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        monkeypatch.setenv("TERMINAL_ENV", "local")
        img = tmp_path / "pic.png"
        img.write_bytes(PNG)
        monkeypatch.chdir(tmp_path)
        res = await isrc.resolve_image_source("pic.png", isrc.ResolveContext())
        assert res.data == PNG
        assert res.origin == "file"


class TestBoundedHostRead:
    """Host reads stay within the ingest cap DURING the read, not after buffering the file."""

    @pytest.mark.asyncio
    async def test_oversized_file_rejected_without_opening_it(self, tmp_path, monkeypatch):
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        monkeypatch.setenv("TERMINAL_ENV", "local")
        big = tmp_path / "big.png"
        with big.open("wb") as fh:  # sparse: stat says cap+1, no disk used
            fh.write(PNG)
            fh.truncate(isrc._MAX_HOST_READ_BYTES + 1)
        opened = []
        real_open = Path.open
        monkeypatch.setattr(Path, "open", lambda self, *a, **k: opened.append(self.name) or real_open(self, *a, **k))
        with pytest.raises(isrc.SourceTooLarge):
            await isrc.resolve_image_source(str(big), isrc.ResolveContext())
        assert "big.png" not in opened

    @pytest.mark.asyncio
    async def test_read_is_capped_when_stat_under_reports(self, tmp_path, monkeypatch):
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        monkeypatch.setenv("TERMINAL_ENV", "local")
        img = tmp_path / "pic.png"
        img.write_bytes(PNG)
        real_stat, real_open, reads = Path.stat, Path.open, []

        def zero_size_stat(self, **kw):  # procfs-style: size 0, content streams
            st = real_stat(self, **kw)
            if self.name != "pic.png":
                return st
            return SimpleNamespace(**{**{a: getattr(st, a) for a in dir(st) if a.startswith("st_")}, "st_size": 0})

        class _Recording:
            def __init__(self, fh):
                self._fh = fh

            def read(self, n=-1):
                reads.append(n)
                return self._fh.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._fh.close()

        def recording_open(self, *a, **kw):
            fh = real_open(self, *a, **kw)
            return _Recording(fh) if self.name == "pic.png" else fh

        monkeypatch.setattr(Path, "stat", zero_size_stat)
        monkeypatch.setattr(Path, "open", recording_open)
        res = await isrc.resolve_image_source(str(img), isrc.ResolveContext())
        assert res.data == PNG
        assert reads and all(0 <= n <= isrc._MAX_HOST_READ_BYTES + 1 for n in reads)

    @pytest.mark.asyncio
    async def test_small_file_read_never_reserves_the_cap(self, tmp_path, monkeypatch):
        # BufferedReader.read(n) allocates n bytes up front, so read(cap+1) reserved 250 MiB of
        # address space per screenshot and raised MemoryError under RLIMIT_AS / strict overcommit.
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        monkeypatch.setenv("TERMINAL_ENV", "local")
        img = tmp_path / "pic.png"
        img.write_bytes(PNG)
        real_open, reads = Path.open, []

        class _Recording:
            def __init__(self, fh):
                self._fh = fh

            def read(self, n=-1):
                reads.append(n)
                return self._fh.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._fh.close()

        monkeypatch.setattr(Path, "open", lambda self, *a, **kw: (
            _Recording(real_open(self, *a, **kw)) if self.name == "pic.png" else real_open(self, *a, **kw)))
        res = await isrc.resolve_image_source(str(img), isrc.ResolveContext())
        assert res.data == PNG
        assert reads and all(0 <= n <= len(PNG) + 1 for n in reads)

    @pytest.mark.asyncio
    async def test_video_host_read_keeps_the_50_mib_budget(self, tmp_path, monkeypatch):
        # Videos are never downscaled and video_analyze refuses >50 MB of base64, so the 250 MiB
        # image budget must not let a cached video be read and copied before that refusal.
        home = tmp_path / "hermes"
        isrc = _reload(monkeypatch, home)
        monkeypatch.setenv("TERMINAL_ENV", "local")
        clip = home / "cache" / "video" / "inbound.mp4"
        clip.parent.mkdir(parents=True)
        with clip.open("wb") as fh:  # sparse: stat says 50 MiB + 1
            fh.write(b"\0\0\0\x18ftypmp42")
            fh.truncate(isrc._MAX_INGEST_BYTES + 1)
        with pytest.raises(isrc.SourceTooLarge):
            await isrc.resolve_image_source(str(clip), isrc.ResolveContext(), permitted=("video",))






class TestSvgNormalization:
    """SVG resolves end-to-end: the resolver passes it through as
    image/svg+xml and the vision call sites rasterize it to PNG via
    _normalize_to_supported_image (PR #52688, folded in)."""

    @pytest.mark.asyncio
    async def test_svg_rasterized_when_converter_available(self, tmp_path, monkeypatch):
        from tools import vision_tools_image_prep as vt
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        monkeypatch.setenv("TERMINAL_ENV", "local")
        svg = tmp_path / "art.svg"
        svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>')

        def fake_rasterize(svg_path, out_path):
            out_path.write_bytes(PNG)
            return True

        with patch.object(vt, "_rasterize_svg_to_png", side_effect=fake_rasterize):
            res = await isrc.resolve_image_source(str(svg), isrc.ResolveContext())
            assert res.mime == "image/svg+xml"
            path, mime, err = vt._normalize_to_supported_image(svg, "image/svg+xml")
        assert err is None
        assert mime == "image/png"
        assert path.read_bytes() == PNG
        path.unlink()

    def test_svg_actionable_error_when_no_converter(self, tmp_path, monkeypatch):
        from tools import vision_tools_image_prep as vt
        _reload(monkeypatch, tmp_path / "hermes")
        svg = tmp_path / "art.svg"
        svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        with patch.object(vt, "_rasterize_svg_to_png", return_value=False):
            path, _mime, err = vt._normalize_to_supported_image(svg, "image/svg+xml")
        assert path is None
        assert "rasterizer" in err




# HEIF/HEIC/AVIF ISO-BMFF headers: 'ftyp' box at bytes 4-8, major brand at 8-12,
# minor version at 12-16, then a list of 4-byte compatible brands.
# 0x18-byte box length prefix mirrors a real iPhone HEIC (\x00\x00\x00\x18 ftyp...).
HEIC_HEADER = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 32
AVIF_HEADER = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1" + b"\x00" * 32
MIF1_HEADER = b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic" + b"\x00" * 32
# AVIF encoders routinely stamp the generic still-image brand 'mif1' as the
# MAJOR brand and declare the AV1 codec only in the compatible-brand list, so a
# major-brand-only sniff mislabels these as HEVC-coded HEIC.
MIF1_MAJOR_AVIF_COMPATIBLE = (
    b"\x00\x00\x00\x1cftypmif1\x00\x00\x00\x00mif1avif" + b"\x00" * 32
)
MIF1_MAJOR_AV01_COMPATIBLE = (
    b"\x00\x00\x00 ftypmif1\x00\x00\x00\x00mif1av01avif" + b"\x00" * 32
)


class TestHeicDetection:
    """iPhone HEIC/HEIF (and AVIF) are sniffed from the ISO-BMFF ftyp brand so
    they reach _normalize_to_supported_image instead of being rejected as
    'not a recognized image' (the pre-fix behavior for iPhone photos)."""

    def test_heic_brand_detected(self):
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        assert _detect_image_mime_type_from_bytes(HEIC_HEADER) == "image/heic"

    def test_generic_mif1_brand_detected_as_heic(self):
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        assert _detect_image_mime_type_from_bytes(MIF1_HEADER) == "image/heic"

    def test_avif_brand_detected(self):
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        assert _detect_image_mime_type_from_bytes(AVIF_HEADER) == "image/avif"

    def test_mif1_major_with_avif_compatible_brand_is_avif(self):
        """Regression: a 'mif1'-major file that lists 'avif' among its
        compatible brands is AV1-coded and must be reported as AVIF. Sniffing
        only the major brand labeled every mif1 file HEIC, which routes an AVIF
        to the HEVC error text and mis-reports the format to the caller."""
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        assert _detect_image_mime_type_from_bytes(
            MIF1_MAJOR_AVIF_COMPATIBLE) == "image/avif"

    def test_mif1_major_with_av01_compatible_brand_is_avif(self):
        """Same as above for the 'av01' codec brand, deeper in the list."""
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        assert _detect_image_mime_type_from_bytes(
            MIF1_MAJOR_AV01_COMPATIBLE) == "image/avif"


    def test_brand_scan_does_not_read_past_the_ftyp_box(self):
        """The scan is bounded by the declared ftyp box size, so an 'avif' token
        sitting in a FOLLOWING box must not upgrade a HEIC to AVIF."""
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        # ftyp box is exactly 0x18 bytes (major mif1 + one compatible 'heic');
        # the next box then contains the literal bytes 'avif'.
        hdr = (
            b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00heic"
            b"\x00\x00\x00\x10metaavif" + b"\x00" * 32
        )
        assert _detect_image_mime_type_from_bytes(hdr) == "image/heic"

    @pytest.mark.parametrize("declared_size", [0, 1, 4, 8, 12, 15])
    def test_malformed_box_size_fails_closed(self, declared_size):
        """A malformed declared size must NOT widen the brand scan.

        Regression: the bound previously fell back to the whole 64-byte sniff
        window whenever the declared size was outside ``16 <= size <= len``. That
        failed OPEN in precisely the attacker-controlled case — a declared size
        of 0/4/8/12 on a genuine HEIC let the literal bytes ``avif`` in a LATER
        box upgrade it to AVIF, defeating the box bound this code exists to
        enforce. Every malformed size must still report HEIC.
        """
        import struct
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        hdr = (
            struct.pack(">I", declared_size)
            + b"ftyp" + b"mif1" + b"\x00" * 4 + b"heic"
            + b"\x00\x00\x00\x10" + b"meta" + b"avif" + b"\x00" * 24
        )
        assert _detect_image_mime_type_from_bytes(hdr) == "image/heic", (
            f"declared size {declared_size} widened the scan and leaked an "
            f"'avif' token from a following box"
        )

    def test_honest_oversized_box_still_scans_what_arrived(self):
        """A size >= 16 that overruns the sniff window is a truncated read, not
        an attack: clamp to the available bytes rather than failing closed, so a
        genuine AVIF whose ftyp box is larger than 64 bytes is still detected."""
        import struct
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        hdr = (
            struct.pack(">I", 9999)
            + b"ftyp" + b"mif1" + b"\x00" * 4 + b"mif1avif"
        )
        assert _detect_image_mime_type_from_bytes(hdr) == "image/avif"

    def test_misaligned_box_size_does_not_crash_or_misdetect(self):
        """A size that is not a multiple of 4 must not misalign the brand loop
        into reading a partial brand."""
        import struct
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        for size in (17, 18, 19, 21, 22, 23):
            hdr = (
                struct.pack(">I", size)
                + b"ftyp" + b"mif1" + b"\x00" * 4 + b"heic"
                + b"\x00\x00\x00\x10" + b"meta" + b"avif" + b"\x00" * 24
            )
            got = _detect_image_mime_type_from_bytes(hdr)
            assert got == "image/heic", f"size {size} -> {got}"


    def test_truncated_ftyp_header_does_not_crash(self):
        """A short/garbage read must return a value, not raise."""
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        assert _detect_image_mime_type_from_bytes(b"\x00\x00\x00\x18ftyp") is None
        # Bogus (huge) declared box size must fall back to the sniffed window.
        assert _detect_image_mime_type_from_bytes(
            b"\xff\xff\xff\xffftypmif1\x00\x00\x00\x00mif1avif") == "image/avif"

    def test_ftyp_with_unknown_brand_not_misdetected(self):
        """An ISO-BMFF ftyp box that isn't an image brand (e.g. mp4) stays
        unrecognized — we must not claim every ftyp container is an image."""
        from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
        mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 32
        assert _detect_image_mime_type_from_bytes(mp4) is None

    @pytest.mark.asyncio
    async def test_heic_resolves_and_normalizes_to_png(self, tmp_path, monkeypatch):
        """End-to-end: a real HEIC file resolves (mime image/heic) and
        _normalize_to_supported_image re-encodes it to PNG via pillow-heif."""
        pillow_heif = pytest.importorskip("pillow_heif")
        pillow_heif.register_heif_opener()
        from PIL import Image
        from tools import vision_tools_image_prep as vt
        isrc = _reload(monkeypatch, tmp_path / "hermes")
        monkeypatch.setenv("TERMINAL_ENV", "local")

        heic = tmp_path / "photo.heic"
        # x265's default CPU-sized pool can exhaust CI threads and hang the encoder.
        # This fixture needs one worker, not a pool per parallel test process.
        Image.new("RGB", (8, 8), (120, 60, 200)).save(
            str(heic), format="HEIF",
            enc_params={"x265:pools": "none", "x265:frame-threads": "1"},
        )

        res = await isrc.resolve_image_source(str(heic), isrc.ResolveContext())
        assert res.mime == "image/heic"

        path, mime, err = vt._normalize_to_supported_image(heic, "image/heic")
        assert err is None
        assert mime == "image/png"
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        path.unlink()

    def test_heic_actionable_error_when_decoder_missing(self, tmp_path, monkeypatch):
        """With pillow-heif unavailable, a HEIC image gets an actionable error
        (install pillow-heif) rather than a generic conversion failure — the
        same soft-dependency posture as SVG-without-rasterizer."""
        from tools import vision_tools_image_prep as vt
        _reload(monkeypatch, tmp_path / "hermes")
        heic = tmp_path / "photo.heic"
        heic.write_bytes(HEIC_HEADER)

        import builtins
        real_import = builtins.__import__

        def _no_heif(name, *args, **kwargs):
            if name == "pillow_heif":
                raise ImportError("pillow_heif not installed")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_no_heif):
            path, _mime, err = vt._normalize_to_supported_image(heic, "image/heic")
        assert path is None
        assert "pillow-heif" in err

    def test_avif_converts_without_pillow_heif(self, tmp_path, monkeypatch):
        """Regression: AVIF must NOT be gated on pillow-heif.

        Pillow >= 11.3 bundles a native AvifImagePlugin, while pillow-heif
        wheels are commonly built with no AV1 codec at all (libheif_info()
        reports ``AVIF: ''``). Gating AVIF on a pillow-heif import therefore
        rejected files Pillow could already decode and told the user to install
        a library that cannot decode them. With pillow-heif absent, a real AVIF
        must still normalize to PNG.
        """
        pytest.importorskip("PIL")
        from PIL import Image, features
        if not features.check("avif"):
            pytest.skip("this Pillow build has no native AVIF codec")
        from tools import vision_tools_image_prep as vt
        _reload(monkeypatch, tmp_path / "hermes")

        avif = tmp_path / "photo.avif"
        Image.new("RGB", (8, 8), (10, 180, 90)).save(str(avif), format="AVIF")

        import builtins
        real_import = builtins.__import__

        def _no_heif(name, *args, **kwargs):
            if name == "pillow_heif":
                raise ImportError("pillow_heif not installed")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_no_heif):
            path, mime, err = vt._normalize_to_supported_image(avif, "image/avif")

        assert err is None, f"AVIF was rejected without pillow-heif: {err}"
        assert mime == "image/png"
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        path.unlink()

    def test_avif_error_names_av1_not_just_pillow_heif(self, tmp_path, monkeypatch):
        """When an AVIF genuinely cannot be decoded, the guidance must mention
        the AV1/Pillow path — not blame pillow-heif alone, which frequently
        ships without any AV1 codec."""
        from tools import vision_tools_image_prep as vt
        _reload(monkeypatch, tmp_path / "hermes")
        # Valid AVIF brand, but the payload is not decodable by anything.
        broken = tmp_path / "broken.avif"
        broken.write_bytes(AVIF_HEADER)

        path, _mime, err = vt._normalize_to_supported_image(broken, "image/avif")
        assert path is None
        assert "AV1" in err or "Pillow" in err
