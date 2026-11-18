"""Single resolver for media sources (data:/http(s)/file/local) -> bytes + mime.

Everything funnels through :func:`resolve_image_source` so size and magic-byte checks run
exactly once. Images are the default; video callers opt in via ``permitted=("video",)``.
"""
from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Raw-bytes ingest budget for data URLs and video sources (50 MiB).
_MAX_INGEST_BYTES = 50 * 1024 * 1024
# Host-file reads (250 MiB): bounded during the read, big enough for full-res camera/scan
# images, which call sites downscale before the 20MB provider payload cap.
_MAX_HOST_READ_BYTES = 250 * 1024 * 1024
# Chunk size for host reads whose st_size under-reports (see _read_host_bytes_bounded).
_HOST_READ_CHUNK = 1024 * 1024


class ImageResolutionError(Exception):
    def __init__(self, message: str, *, src: str = "", origin: str = ""):
        super().__init__(message)
        self.src, self.origin = src, origin


class UnsupportedScheme(ImageResolutionError): ...
class SourceUnsafe(ImageResolutionError): ...  # SSRF / path-allowlist
class SourceTooLarge(ImageResolutionError): ...
class SourceNotFound(ImageResolutionError): ...
class NotAnImage(ImageResolutionError): ...


@dataclass
class ResolveContext:
    task_id: Optional[str] = None


@dataclass
class ResolvedImage:
    data: bytes
    mime: str
    origin: str  # one of: data | http | file


# Explicit URL scheme ("ftp://", "s3://"). Bare Windows drive paths lack the "//".
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


async def resolve_image_source(
    src: str, ctx: ResolveContext, *, permitted: tuple = ("image",)) -> ResolvedImage:
    if not isinstance(src, str) or not src.strip():
        raise SourceNotFound("image_url is required", src=str(src))
    s = src.strip()
    if s.startswith("data:"):
        data, mime = _resolve_data_url(s)
        return _finalize(data, mime, "data", s, permitted)
    if s.startswith(("http://", "https://")):
        reason = _http_block_reason(s)
        if reason:
            raise SourceUnsafe(reason, src=s)
        return _finalize(await _download_to_bytes(s), "", "http", s, permitted)
    if _SCHEME_RE.match(s) and not s.lower().startswith("file://"):
        raise UnsupportedScheme(
            "Unrecognized image source scheme. Use an http(s) URL, a local "
            "file path, a file:// URI, or a data: URL.",
            src=s)
    # Everything else is a filesystem path — including bare relative names like "pic.png"
    # (a path-shape gate here regressed them once).
    candidate = s[len("file://"):] if s.lower().startswith("file://") else s
    from agent.file_safety import get_nt_namespace_error
    from tools.file_tools_paths import _resolve_path_for_task
    if reason := get_nt_namespace_error(candidate, verb="Read"):
        raise SourceUnsafe(reason, src=s, origin="file")
    try:
        p = _resolve_path_for_task(candidate, ctx.task_id or "default")
    except ValueError as exc:
        raise SourceUnsafe(str(exc), src=s, origin="file") from exc
    host_target = p
    if host_target.is_file():
        _guard_credential_read(host_target, s)
        # Videos are never downscaled and video_analyze refuses over 50 MB of base64, so the
        # larger host budget only helps images; videos keep the ingest cap and fail at the stat.
        cap = _MAX_HOST_READ_BYTES if "image" in permitted else _MAX_INGEST_BYTES
        data = await asyncio.to_thread(_read_host_bytes_bounded, host_target, s, cap)
        return _finalize(data, "", "file", s, permitted)
    raise SourceNotFound(f"media file not found: '{p}'", src=s, origin="file")


def _read_host_bytes_bounded(path: Path, src: str, cap: int) -> bytes:
    """Read a host file with ``cap`` enforced DURING the read. ``read_bytes()`` buffered the whole
    file before ``_finalize`` checked its size. ``read(n)`` preallocates n bytes, so the first read
    asks for the stat'd size (not the cap); when ``st_size`` under-reports (procfs-style files) the
    rest streams in chunks and stops past ``cap``. The honest-size check reads nothing at all."""
    size = path.stat().st_size
    if size > cap:
        raise SourceTooLarge("media exceeds size limit", src=src, origin="file")
    with path.open("rb") as fh:
        data = fh.read(size + 1)
        if len(data) > size:
            parts, total = [data], len(data)
            while total <= cap and (chunk := fh.read(_HOST_READ_CHUNK)):
                parts.append(chunk)
                total += len(chunk)
            if total > cap:
                raise SourceTooLarge("media exceeds size limit", src=src, origin="file")
            data = b"".join(parts)
    return data


def _guard_credential_read(host_target: Path, src: str) -> None:
    """Shared credential-read guard: refuse secret-bearing files (.env, auth.json) with a specific
    error. Guard import is best-effort; a real block always propagates."""
    try:
        # Shared credential-read guard (agent.file_safety, #57698): refuse secret-bearing files (.env,
        # auth.json, ...) with an intentional, specific error instead of relying on the magic-byte sniff to
        # reject them incidentally. Same chokepoint the image-gen/video-gen provider plugins enforce on
        # model-supplied local paths. Import is best-effort (guard unavailability must not break image
        # loading); a real block always propagates.
        from agent.file_safety import raise_if_read_blocked
    except Exception:
        return
    try:
        raise_if_read_blocked(str(host_target))
    except ValueError as exc:
        raise SourceUnsafe(str(exc), src=src, origin="file")


def _resolve_data_url(s: str) -> tuple[bytes, str]:
    header, _, payload = s.partition(",")
    if ";base64" not in header:
        raise NotAnImage("data: URL must be base64-encoded", src=s[:64])
    declared = header[len("data:"):].split(";", 1)[0].strip() or "application/octet-stream"
    # Cheap pre-decode size gate on the encoded length (~4/3 expansion).
    if (len(payload) * 3) // 4 > _MAX_INGEST_BYTES:
        raise SourceTooLarge("data: URL exceeds size limit", src=s[:64])
    try:
        data = base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise NotAnImage(f"invalid base64 in data: URL: {exc}", src=s[:64])
    return data, declared  # real mime verified in _finalize via magic bytes


def _http_block_reason(url: str) -> Optional[str]:
    """Block reason, or None when allowed. Refuses policy-blocked URLs BEFORE any network I/O;
    ``_download_image`` re-checks per attempt and against the final redirect target (intentional)."""
    from tools.url_safety import is_safe_url
    from tools.website_policy import check_website_access
    if not is_safe_url(url):
        return "blocked: unsafe or private URL"
    if blocked := check_website_access(url):
        return blocked.get("message") or "blocked by website policy"
    return None


async def _download_to_bytes(url: str) -> bytes:
    import tempfile
    from tools.vision_tools import _download_image
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        # Enforces the 50MB stream cap, redirect SSRF guard, and website policy.
        await _download_image(url, tmp)
        return await asyncio.to_thread(tmp.read_bytes)
    except PermissionError as exc:  # website policy block
        raise SourceUnsafe(str(exc), src=url, origin="http")
    finally:
        tmp.unlink(missing_ok=True)


def _finalize(
    data: bytes, declared_mime: str, origin: str, src: str, permitted: tuple = ("image",)
) -> ResolvedImage:
    """Chokepoint: size ceiling + type check. Images by magic bytes; video (opt-in) by
    extension + mp4 sniff — enough because every downstream consumer re-validates. Each
    reader enforces its own tighter cap first; this is the ceiling for all of them."""
    from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes
    if len(data) > _MAX_HOST_READ_BYTES:
        raise SourceTooLarge("media exceeds size limit", src=src, origin=origin)
    sniffed = _detect_image_mime_type_from_bytes(data)
    if sniffed is not None:
        if "image" not in permitted:
            raise NotAnImage("source is an image, but this argument takes a video", src=src, origin=origin)
        return ResolvedImage(data=data, mime=sniffed, origin=origin)
    if "image" in permitted and b"<svg" in data[:4096].lower():
        # Pass SVG through — call sites rasterize it to PNG before embedding.
        return ResolvedImage(data=data, mime="image/svg+xml", origin=origin)
    if "video" in permitted:
        video_mime = _detect_video_mime(data, src)
        if video_mime is not None:
            return ResolvedImage(data=data, mime=video_mime, origin=origin)
        raise NotAnImage("source is not a recognized video (mp4 expected)", src=src, origin=origin)
    raise NotAnImage("source is not a recognized image", src=src, origin=origin)


def _detect_video_mime(data: bytes, src: str) -> Optional[str]:
    """Video MIME from the extension table, else the ISO base-media ``ftyp`` magic at
    offset 4 (covers extensionless data: URLs / query-string URLs)."""
    from urllib.parse import urlsplit
    from tools.vision_tools import _detect_video_mime_type
    path_part = urlsplit(src).path if _SCHEME_RE.match(src) else src
    by_extension = _detect_video_mime_type(Path(path_part))
    if by_extension is not None:
        return by_extension
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    return None


async def resolve_local_source_to_data_url(
    src: str, task_id: Optional[str], *, permitted: tuple = ("image",)) -> str:
    """Convert a path-like media source into a ``data:`` URL via the resolver.

    URL-shaped sources pass through unchanged.
    """
    s = (src or "").strip()
    if not s or s.lower().startswith(("http://", "https://", "data:")):
        return src
    resolved = await resolve_image_source(s, ResolveContext(task_id=task_id), permitted=permitted)
    encoded = base64.b64encode(resolved.data).decode("ascii")
    mime = resolved.mime or "application/octet-stream"
    return f"data:{mime};base64,{encoded}"
