"""Classic CLI ``/browser`` on the shared gateway (#134889 CLI-9): ``browser.manage``, as Ink and
Desktop send it. Split from ``cli_commands_mixin`` (the file is over its size target)."""
from __future__ import annotations

import os
from urllib.parse import urlparse

from hermes_cli.browser_connect import DEFAULT_BROWSER_CDP_URL
from hermes_cli.cli_commands_session_tools import _say_block


def _gateway_is_remote() -> bool:
    """True when this CLI attaches to an explicitly named gateway WebSocket that is not loopback
    (``HERMES_TUI_GATEWAY_URL``): Desktop's ``mode === 'remote'``."""
    raw = os.environ.get("HERMES_TUI_GATEWAY_URL", "").strip()
    if not raw:
        return False
    host = (urlparse(raw).hostname or "").lower()
    return host not in {"127.0.0.1", "localhost", "::1"}


def gateway_browser(cli, connection, word: str, rest: str) -> None:
    """``/browser`` on the shared gateway (#134889 CLI-9): ``browser.manage``, the verb Ink and
    Desktop send. The browser tools run in the gateway, so changing this process's
    ``BROWSER_CDP_URL`` would change nothing the agent uses. ``connect``/``disconnect`` manage a
    browser on the gateway HOST: only against a local gateway (Desktop's rule). The gateway scopes
    them to this session's profile."""
    from hermes_cli.gateway_client import GatewayClientError
    if word in {"connect", "disconnect"} and _gateway_is_remote():
        return _say_block('/browser connect and /browser disconnect manage a Chromium-family browser on the gateway host — only available when connected to a local gateway.')
    params = {"action": word, "session_id": cli.session_id}
    if word == "connect":
        normalized = _normalize_cdp_url(rest or DEFAULT_BROWSER_CDP_URL)
        if normalized is None:
            return
        params["url"] = normalized[0]
        print(f"   {('Checking Chromium-family browser remote debugging at {url}...').format(url=params['url'])}")
    try:
        result = connection.call("browser.manage", **params) or {}
    except GatewayClientError as exc:
        return _say_block(f"   ⚠ {exc}")
    for message in result.get("messages") or ():
        print(f"   {message}")
    if word == "disconnect":
        return _say_block('🌐 Browser disconnected from live Chromium-family browser', f"   {'Browser tools reverted to default mode (local headless or cloud provider)'}")
    if word == "connect":
        if not result.get("connected"):
            return _say_block('Browser not connected — start a Chromium-family browser with remote debugging and retry /browser connect')
        return _say_block('🌐 Browser connected to live Chromium-family browser via CDP', f"   {('Endpoint: {url}').format(url=result.get('url') or '')}")
    if result.get("connected"):
        return _say_block('🌐 Browser: connected to live Chromium-family browser via CDP', f"   {('Endpoint: {url}').format(url=result.get('url') or '')}")
    _say_block("🌐 Browser: the gateway's default browser backend (no live CDP connection)",
               f"   {'/browser connect      — connect to your live Chromium-family browser'}", f"   {'/browser disconnect   — revert to default'}")


def _normalize_cdp_url(cdp_url: str):
    """Validate a /browser connect URL: ``(cdp_url, port)`` or None after printing the error.
    A ``/devtools/browser/<id>`` path is kept verbatim; anything else is reduced to the origin."""
    parsed = urlparse(cdp_url if "://" in cdp_url else f"http://{cdp_url}")
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        _say_block(f"   {('⚠ Unsupported browser url scheme: {scheme} (expected one of: http, https, ws, wss)').format(scheme=parsed.scheme or '(missing)')}")
        return None
    try:
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
    except ValueError:
        _say_block(f"   {(f'⚠ Invalid port in browser url: {cdp_url}')}")
        return None
    if not parsed.hostname:
        _say_block(f"   {(f'⚠ Missing host in browser url: {cdp_url}')}")
        return None
    if parsed.path.startswith("/devtools/browser/"):
        return parsed.geturl(), port
    return parsed._replace(path="", params="", query="", fragment="").geturl(), port

