"""Outbound send bodies must not carry lone surrogate code points (#113799).

``hermes send MESSAGE`` passes argv through surrogateescape decoding on macOS
shells; a lone surrogate then crashes the UTF-8 marshal inside platform SDK
request bodies (feishu/lark) and the message is lost after the retries.
The local outbound send entry scrubs text before it reaches the adapter.
"""

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.config import Platform
from gateway.outbound import send_outbound

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _run_async_immediately(coro):
    return asyncio.run(coro)


def _make_config():
    api_cfg = SimpleNamespace(enabled=True, token=None, extra={})
    return SimpleNamespace(
        platforms={Platform.API_SERVER: api_cfg},
        get_home_channel=lambda _platform: None,
    )


def _send(message):
    send_mock = AsyncMock(return_value=SimpleNamespace(success=True, message_id="test"))
    adapter = SimpleNamespace(send=send_mock)
    with patch("gateway.config.load_gateway_config", return_value=_make_config()), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("model_tools._run_async", side_effect=_run_async_immediately), \
         patch("gateway.outbound._live_adapter", return_value=(SimpleNamespace(), adapter)), \
         patch("gateway.outbound._standalone_send_policy", return_value=None), \
         patch("gateway.mirror.mirror_to_session", return_value=True):
        result = json.loads(
            send_outbound({"target": "api_server:12345", "message": message})
        )
    return result, send_mock


def test_lone_surrogates_are_scrubbed_before_the_platform_send():
    result, send_mock = _send("警报 \ud800\udfff done")

    assert result["success"] is True
    delivered = send_mock.await_args.kwargs["content"]
    assert not _SURROGATE_RE.search(delivered)
    assert delivered == "警报 \ufffd\ufffd done"


def test_surrogate_free_message_is_sent_unchanged():
    result, send_mock = _send("all clean: 你好 \U0001f44d")

    assert result["success"] is True
    assert send_mock.await_args.kwargs["content"] == "all clean: 你好 \U0001f44d"
