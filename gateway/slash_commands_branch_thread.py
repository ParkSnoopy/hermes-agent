"""Shared argument parsing for in-place CLI and gateway session branches."""

BRANCH_HERE_FLAG = "--here"


def parse_branch_args(raw: str) -> tuple[bool, str]:
    """Return whether --here was supplied and the optional branch title."""
    text = (raw or "").strip()
    head, _, rest = text.partition(" ")
    if head.lower() == BRANCH_HERE_FLAG:
        return True, rest.strip()
    return False, text
