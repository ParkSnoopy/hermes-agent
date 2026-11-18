"""Interim metadata preserves the caller's thread metadata without mutating it."""

from gateway.run import _interim_metadata


class TestInterimMetadataHelper:


    def test_does_not_mutate_input(self):
        src = {"thread_id": "t1"}
        result = _interim_metadata(src)
        assert "_interim_send" not in src
        assert result == {"thread_id": "t1", "_interim_send": True}
