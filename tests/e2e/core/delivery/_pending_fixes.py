"""Expected failures for live gaps.

``known_failure`` is a run-time xfail keyed on the gap's
own assertion message, so the cell XFAILs only while it fails exactly that way, fails loudly on
any other failure, and simply passes once someone fixes the gap.
"""

from __future__ import annotations

import contextlib
import re
from typing import Callable, Iterator, Optional

import pytest

from tests.e2e.core._pending_fixes import strict_acceptance


@contextlib.contextmanager
def known_failure(pattern: str, reason: str,
                  on_xfail: Optional[Callable[[], None]] = None) -> Iterator[None]:
    """Run-time xfail for a live gap without a fix PR: an ``AssertionError`` raised inside the
    block whose message matches ``pattern`` XFAILs the cell; any other failure propagates, and a
    clean pass stays a pass. Wrap only the final assertions, after every wait has settled, so a
    lost reply, a failed restart or a timeout can never be mistaken for the gap. Strict
    acceptance refuses owned gaps before running any xfail-only bookkeeping."""
    try:
        yield
    except AssertionError as exc:
        if not re.search(pattern, str(exc)) or strict_acceptance(reason):
            raise
        if on_xfail is not None:
            on_xfail()
        pytest.xfail(f"{reason} [observed: {str(exc).splitlines()[0][:240]}]")
