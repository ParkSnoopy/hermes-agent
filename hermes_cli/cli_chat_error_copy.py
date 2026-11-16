"""Plain-language copy for chat-turn failures shown in the CLI response panel.

The chat panel used to echo ``Error: HTTP 401: Invalid API key`` as the assistant's answer. These
helpers map the classifier verdict (``agent/error_classifier.py``) to WHAT happened + WHAT TO DO,
and demote the raw provider text to a ``Details:`` line.
"""

from __future__ import annotations


_SUMMARY_LIMIT = 120

# FailoverReason.value -> catalog key suffix. Reasons sharing copy point at one key.
_REASON_KEYS: dict[str, str] = {
    "auth": "auth",
    "auth_permanent": "auth",
    "billing": "billing",
    "model_not_found": "model_not_found",
    "rate_limit": "rate_limit",
    "upstream_rate_limit": "rate_limit",
    "upstream_blocked": "upstream_blocked",
    "overloaded": "overloaded",
    "server_error": "server_error",
    "timeout": "timeout",
}
_UNKNOWN_KEY = "unknown"


def _short(text: str, limit: int | None = _SUMMARY_LIMIT) -> str:
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return first if limit is None or len(first) <= limit else first[: limit - 1].rstrip() + "…"


def refusal_text(error: BaseException | str) -> str:
    """The refusal as the client shows it: the reason code, plus the check that fired when the owner
    named one (``invalid_params (toolsets: unknown or invalid toolset name: 'mcp-codegraph')``).

    The gateway sends ``field``/``detail`` in the JSON-RPC error's ``data``
    (``gateway/session_controls.py``), and ``hermes_cli.gateway_client.rpc_error`` carries the
    bounded scalars onto ``GatewayRPCError.data``. A refusal from anywhere else has no ``data`` and
    reads exactly as before."""
    if str(error) == "launch_credentials_unavailable":
        # The chat's launch key changed: every retry repeats the refusal, so name the way out.
        from agent.turn_failure_copy import launch_credentials_copy

        return launch_credentials_copy(error)
    data = getattr(error, "data", None)
    data = data if isinstance(data, dict) else {}
    field, detail = str(data.get("field") or "").strip(), str(data.get("detail") or "").strip()
    if not field and not detail:
        return str(error)
    return f"{error} ({field or 'unnamed field'}{': ' + detail if detail else ''})"


def chat_error_response(
    error: Exception | str, *, provider: str = "", model: str = "", failure_reason: str | None = None,
) -> str:
    """Two-line panel text: plain sentence with the fix command, then ``Details: <raw>``.

    ``failure_reason`` is the verdict the turn loop already stamped on its result
    (``agent/turn_failure_copy.stamp_failure``). When present it is used as-is instead of
    re-classifying a summarised string (which has no status code and almost always lands on
    'unknown'); for the loop's own site codes the ``error`` text is already the user-facing copy,
    so it is returned verbatim rather than wrapped and demoted to a Details line."""
    reason = str(failure_reason or "").strip()
    if reason:
        from agent.turn_failure_copy import SITE_FAILURE_CODES

        text = str(error or "").strip()
        if reason in SITE_FAILURE_CODES and text:
            return text
    else:
        from agent.error_classifier import classify_api_error

        exc = error if isinstance(error, Exception) else Exception(str(error))
        reason = classify_api_error(exc, provider=provider or "", model=model or "").reason.value
    lead = {'auth': 'Your {provider} key was rejected. Run `hermes model` to re-enter it.', 'billing': 'Your {provider} account is out of credit. Top up at the provider, or run /model to switch.', 'model_not_found': "'{model}' isn't available on {provider}. Run /model to pick a valid model.", 'rate_limit': 'Rate limited by {provider}; wait a minute or /model to switch.', 'upstream_blocked': 'A firewall/CDN in front of {provider} blocked the request (not your key). Set a User-Agent via extra_headers, or /model to switch.', 'overloaded': '{provider} is overloaded right now. Send /retry in a moment, or /model to switch.', 'server_error': '{provider} had an internal error. Send /retry in a moment, or /model to switch.', 'timeout': '{provider} did not answer in time. Send /retry, or /model to switch.', 'unknown': 'The model request failed. Run /model to switch or `hermes doctor` to check the setup.', 'the_provider': 'the provider', 'the_current_model': 'the current model', 'with_details': '{lead}\nDetails: {details}', 'agent_init_failed': "Hermes couldn't start the model connection: {error}. Your message was not sent. Run `hermes doctor` to check the setup, or /model to pick a different provider."}[_REASON_KEYS.get(reason, _UNKNOWN_KEY)].format(
        provider=provider or 'the provider',
        model=model or 'the current model',
    )
    return (f'{lead}\nDetails: {_short(refusal_text(error), 300)}')


def agent_init_failure_message(error: BaseException) -> str:
    """Copy for a failed AIAgent build on first message: the user's turn was dropped.

    A gateway that cannot be reached keeps its whole first line: its discovery detail is already
    bounded (``DiscoveryError.bounded_detail``) and ends in the repair command, which the summary
    limit would cut mid-path."""
    from hermes_cli.gateway_client import GatewayUnavailableError

    limit = None if isinstance(error, GatewayUnavailableError) else _SUMMARY_LIMIT
    return (f"Hermes couldn't start the model connection: {_short(refusal_text(error), limit) or type(error).__name__}. Your message was not sent. Run `hermes doctor` to check the setup, or /model to pick a different provider.")
