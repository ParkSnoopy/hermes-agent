"""Media cache download retries, content validation and SSRF guards."""

import asyncio
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _stream_response(content=b"\xff\xd8\xff fake jpeg"):
    response = MagicMock()
    response.headers = {}

    async def chunks():
        yield content

    response.aiter_bytes = chunks
    return response


def _stream_client(responses):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    def stream(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        context = AsyncMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        return context

    client.stream = MagicMock(side_effect=stream)
    return client


def _status_error(status):
    request = httpx.Request("GET", "https://example.com/media")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("download failed", request=request, response=response)


@pytest.mark.parametrize("brand,ext", [(b"heic", ".heic"), (b"mif1", ".heif"), (b"avif", ".avif")])
def test_cache_isobmff_stills(tmp_path, monkeypatch, brand, ext):
    from gateway.platforms.base import cache_image_from_bytes
    monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
    data = b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00mif1heic" + b"\x00" * 64
    assert Path(cache_image_from_bytes(data, ext)).read_bytes() == data


@pytest.mark.parametrize("data,ext", [
    (b"<!DOCTYPE html><html></html>", ".png"),
    (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64, ".heic"),
])
def test_cache_rejects_non_images(tmp_path, monkeypatch, data, ext):
    from gateway.platforms.base import cache_image_from_bytes
    monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
    with pytest.raises(ValueError, match="non-image data"):
        cache_image_from_bytes(data, ext)


@pytest.mark.parametrize("kind,ext,content", [
    ("image", ".jpg", b"\xff\xd8\xff image data"),
    ("audio", ".ogg", b"audio data"),
])
@pytest.mark.parametrize("failure", [httpx.TimeoutException("timed out"), _status_error(429), _status_error(503)])
def test_retry_then_cache_content(tmp_path, monkeypatch, kind, ext, content, failure):
    from gateway.platforms import base
    monkeypatch.setattr(base, f"{kind.upper()}_CACHE_DIR", tmp_path / kind)
    client = _stream_client(iter([failure, _stream_response(content)]))
    sleep = AsyncMock()
    with patch("tools.url_safety.is_safe_url", return_value=True), \
         patch("httpx.AsyncClient", return_value=client), patch("asyncio.sleep", sleep):
        path = asyncio.run(getattr(base, f"cache_{kind}_from_url")(
            "https://example.com/media", ext=ext, retries=2))
    assert Path(path).read_bytes() == content
    assert client.stream.call_count == 2
    sleep.assert_awaited_once_with(1.5)


@pytest.mark.parametrize("status,attempts", [(404, 1), (429, 3), (503, 3)])
def test_nonretryable_and_exhausted_errors(status, attempts, tmp_path, monkeypatch):
    from gateway.platforms.base import cache_image_from_url
    monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
    client = _stream_client(iter(_status_error(status) for _ in range(attempts)))
    sleep = AsyncMock()
    with patch("tools.url_safety.is_safe_url", return_value=True), \
         patch("httpx.AsyncClient", return_value=client), patch("asyncio.sleep", sleep):
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(cache_image_from_url("https://example.com/media", retries=2))
    assert client.stream.call_count == attempts
    assert sleep.await_count == attempts - 1
    assert not (tmp_path / "img").exists()


def test_blocks_private_dns_answer_at_connect_time(tmp_path, monkeypatch):
    from gateway.platforms.base import cache_image_from_url
    from httpcore._backends.auto import AutoBackend
    monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("httpx._utils.getproxies", dict)
    answers = [
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))],
    ]

    def resolve(host, port, *args, **kwargs):
        assert host == "rebind.test"
        return answers.pop(0)

    async def fail_connect(self, host, port, **kwargs):
        raise AssertionError(f"TCP connect attempted for {host}:{port}")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(AutoBackend, "connect_tcp", fail_connect)
    with pytest.raises(ValueError, match="during connect"):
        asyncio.run(cache_image_from_url("http://rebind.test/image.jpg", retries=0))
    assert answers == []


def test_image_blocks_private_redirect(tmp_path, monkeypatch):
    from gateway.platforms.base import cache_image_from_url
    monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
    redirect = MagicMock(is_redirect=True, next_request=MagicMock(url="http://169.254.169.254/metadata"))
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    def factory(*args, **kwargs):
        async def enter():
            for hook in kwargs["event_hooks"]["response"]:
                await hook(redirect)
            return redirect
        context = AsyncMock()
        context.__aenter__ = AsyncMock(side_effect=enter)
        context.__aexit__ = AsyncMock(return_value=False)
        client.stream = MagicMock(return_value=context)
        return client

    with patch("tools.url_safety.is_safe_url", side_effect=lambda url: url == "https://public.example.com/image.png"), \
         patch("httpx.AsyncClient", side_effect=factory):
        with pytest.raises(ValueError, match="Blocked redirect"):
            asyncio.run(cache_image_from_url("https://public.example.com/image.png", ext=".png"))
