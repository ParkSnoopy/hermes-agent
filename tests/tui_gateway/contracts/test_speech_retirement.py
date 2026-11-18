"""Speech is not advertised; text and transport contracts remain available."""

import pytest
from pydantic import ValidationError

from tui_gateway.contracts import CANONICAL_METHODS, EVENTS, METHODS, SERVER_REQUESTS


def test_speech_contracts_are_not_advertised():
    for table in (METHODS, CANONICAL_METHODS, EVENTS, SERVER_REQUESTS):
        assert not [name for name in table if name.startswith(("voice.", "wake."))]
    assert {"ping", "gateway.capabilities", "client.capabilities", "prompt.submit", "image.attach"} <= METHODS.keys()
    assert "clarify" in SERVER_REQUESTS


def test_canonical_corrections_are_not_voice_stop_results():
    result = CANONICAL_METHODS["session.steer"].result
    with pytest.raises(ValidationError):
        result.model_validate({"voice_stopped": True})
    accepted = {"status": "queued", "text": "stop", "execution_generation": 1, "authority_epoch": 1}
    assert result.model_validate(accepted).model_dump() == {**accepted, "delivered": None}
