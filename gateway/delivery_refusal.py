"""Classify definite delivery refusals without retrying a forbidden destination."""

EGRESS_DECLINE_CODE = "egress_declined"


def declined_send(result):
    raw = getattr(result, "raw_response", None)
    if isinstance(raw, dict):
        if raw.get("success") or raw.get("ambiguous"):
            return False
        return (raw.get("code") == EGRESS_DECLINE_CODE
                or "egress declined:" in str(raw.get("error") or "").lower())
    error = str(getattr(result, "error", None) or "").lower()
    return "ack lost" not in error and "egress declined:" in error
