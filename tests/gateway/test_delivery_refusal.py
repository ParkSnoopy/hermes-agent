from types import SimpleNamespace

import pytest

from gateway.delivery_refusal import declined_send


@pytest.mark.parametrize(('raw', 'error', 'expected'), [
    ({'success': False, 'code': 'egress_declined'}, None, True),
    ({'success': False, 'error': 'Egress declined: denied'}, None, True),
    ({'success': False, 'ambiguous': True, 'code': 'egress_declined'}, None, False),
    ({'success': True, 'code': 'egress_declined'}, None, False),
    (None, 'egress declined: denied', True),
    (None, 'egress declined: ack lost', False),
    (None, 'connection failed', False),
])
def test_delivery_refusal(raw, error, expected):
    assert declined_send(SimpleNamespace(raw_response=raw, error=error)) is expected
