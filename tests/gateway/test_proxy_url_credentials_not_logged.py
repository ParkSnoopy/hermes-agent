"""Proxy credentials must never appear in shared connector or gateway logs."""
import asyncio
import logging
import types
from unittest.mock import MagicMock, patch
import pytest
from gateway.config import Platform

SECRET = "S3cretPass"



def _proxy_lines(caplog):
    return [r.getMessage() for r in caplog.records if "proxy" in r.getMessage().lower()]



SOCKS_PROXY = "socks5://alice:***@127.0.0.1:1080"



def _assert_socks_warning_without_secret(caplog):
    lines = [r.getMessage() for r in caplog.records if "SOCKS proxy" in r.getMessage()]
    assert lines, "the missing-aiohttp_socks warning must still fire"
    assert all(SECRET not in line and "alice" not in line for line in lines), lines
    assert any("127.0.0.1:1080" in line for line in lines), lines



def test_shared_socks_connector_warning_logs_proxy_without_credentials(monkeypatch, caplog):
    import sys

    from gateway.platforms import base as gw_base

    monkeypatch.setitem(sys.modules, "aiohttp_socks", None)  # import raises ImportError
    with caplog.at_level(logging.WARNING):
        assert gw_base._aiohttp_socks_connector(SOCKS_PROXY) is None
    _assert_socks_warning_without_secret(caplog)



TOKEN = "Zq9xK7tWv3"



SECRET_SHAPES = [
    "http://bob:{T}@proxy:8080",
    "http://{T}@proxy:8080",  # token as the username
    "http://bob:{T}ab/cd@proxy:8080",  # unencoded '/' in the password
    "http://bob:ab/{T}@proxy:8080",
    "socks5://bob:SEK?{T}@proxy:1080",  # unencoded '?'
    "socks5://bob:{T}?x@proxy:1080",
    "http://bob:ab#{T}@proxy:8080",  # unencoded '#'
    "http://bob:{T}#x@proxy:8080",
    "http://bob:***@b:{T}@proxy:8080",  # several '@'
    "http://bob:{T}@[::1]:3128",
    "http://bob:{T}@[::1",
    "bob:{T}@proxy:8080",
    "http:/bob:{T}@proxy:8080",
    "http://bob:{T}@proxy:8080/x?y#z",
    "http://bob:{T}",  # userinfo with no host
    "proxy:8080?auth={T}",  # no scheme, secret in the query
    "proxy:8080#{T}",
    "bob:{T}",  # no scheme, no '@': nothing parses as an authority
    "//bob:{T}",
    "http:bob:{T}",
    "bob:{T}/",
    "proxy:8080/{T}",
    "proxy:8080;{T}",
    "http://proxy:8080/x/{T}",  # secret as the last path segment
    "http://proxy:8080/{T}.json",  # secret shaped like a media file name
    "http://bob:" + "p" * 70 + "{T}@" + "h" * 60 + ".example:8080",  # past the 80-char cut
    "http://bob:" + "p" * 70 + "{T}/x@proxy:8080",  # token straddles the cut
]



def _assert_token_absent(text):
    # No 4+ char prefix either, so a token cut in half by truncation cannot leak.
    assert all(TOKEN[:n] not in text for n in range(4, len(TOKEN) + 1)), text



@pytest.mark.parametrize("shape", SECRET_SHAPES)
def test_proxy_secret_never_logged_in_any_shape(monkeypatch, caplog, shape):
    import sys

    from gateway.platforms import base as gw_base

    proxy = shape.format(T=TOKEN)
    monkeypatch.setitem(sys.modules, "aiohttp_socks", None)
    with caplog.at_level(logging.WARNING):
        gw_base._aiohttp_socks_connector("socks5://" + proxy.split("://", 1)[-1])
    assert _proxy_lines(caplog), "the adapter must still announce that a proxy is in use"
    _assert_token_absent("\n".join(r.getMessage() for r in caplog.records))



@pytest.mark.parametrize("status, chunks", [
    (500, []),  # HTTP error line
    (200, ['data: {"choices":[{"delta":{"content":"hi"}}]}\n\n']),  # no [DONE] + response line
    (None, []),  # connection error line
])
def test_gateway_proxy_mode_logs_upstream_url_without_credentials(monkeypatch, caplog, status, chunks):
    """``GATEWAY_PROXY_URL`` is logged on every proxied turn; its userinfo must not be."""
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource

    monkeypatch.setenv("GATEWAY_PROXY_URL", f"http://bob:{TOKEN}@127.0.0.1:8642")
    monkeypatch.delenv("GATEWAY_PROXY_KEY", raising=False)
    runner = object.__new__(GatewayRunner)
    runner.adapters, runner._running_agents, runner._agent_cache = {}, {}, {}
    runner.config = MagicMock()

    class _Resp:
        def __init__(self):
            self.status, self.content = status, self

        async def text(self):
            return "boom"

        async def iter_any(self):
            for chunk in chunks:
                yield chunk.encode()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def post(self, url, **kw):
            if status is None:
                raise ConnectionError("refused")
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    aiohttp_stub = types.SimpleNamespace(ClientSession=lambda **kw: _Session(), ClientTimeout=MagicMock())
    source = SessionSource(platform=Platform.API_SERVER, chat_id="!r:s", chat_type="group", user_id="@u:s")
    with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}), \
            patch("gateway.run._load_gateway_config", return_value={}), \
            caplog.at_level(logging.INFO):
        asyncio.run(runner._run_agent_via_proxy(
            message="hi", context_prompt="", history=[], source=source, session_id="s"))
    lines = [r.getMessage() for r in caplog.records if "127.0.0.1:8642" in r.getMessage()]
    assert lines, "the proxy-mode turn must still log which upstream it used"
    _assert_token_absent("\n".join(r.getMessage() for r in caplog.records))



@pytest.mark.parametrize("proxy_url", [
    f"http://bob:ab/{TOKEN}@127.0.0.1:1",  # unencoded '/' in the password
    f"http://bob:{TOKEN}#x@127.0.0.1:1",  # unencoded '#'
    f"http://bob:{TOKEN}@[::1",  # unclosed IPv6 bracket
    f"bob:{TOKEN}@127.0.0.1:1",  # no scheme
    f"bob:{TOKEN}",
    f"http://bob:{TOKEN}",  # userinfo with no host
])
def test_gateway_proxy_mode_malformed_url_error_never_echoes_secret(monkeypatch, caplog, proxy_url):
    """aiohttp's InvalidUrlClientError/NonHttpUrlClientError text is the whole request URL; the
    turn logs it and sends it to the chat as the error reply."""
    pytest.importorskip("aiohttp")
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource

    monkeypatch.setenv("GATEWAY_PROXY_URL", proxy_url)
    monkeypatch.delenv("GATEWAY_PROXY_KEY", raising=False)
    runner = object.__new__(GatewayRunner)
    runner.adapters, runner._running_agents, runner._agent_cache = {}, {}, {}
    runner.config = MagicMock()
    source = SessionSource(platform=Platform.API_SERVER, chat_id="!r:s", chat_type="group", user_id="@u:s")
    with patch("gateway.run._load_gateway_config", return_value={}), caplog.at_level(logging.INFO):
        result = asyncio.run(runner._run_agent_via_proxy(
            message="hi", context_prompt="", history=[], source=source, session_id="s"))
    assert any("Proxy connection error" in r.getMessage() for r in caplog.records)
    assert result["final_response"].startswith("⚠️")
    _assert_token_absent(result["final_response"])
    _assert_token_absent("\n".join(r.getMessage() for r in caplog.records))

