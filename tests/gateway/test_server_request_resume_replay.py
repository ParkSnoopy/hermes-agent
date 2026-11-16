"""Only replayable owner requests are offered to a reconnecting viewer."""
from __future__ import annotations

from tests.waiting import wait_until


class _Transport:
    def write(self, frame):
        return True

    def close(self):
        pass


def test_only_replayable_requests_are_handed_to_a_reconnecting_viewer():
    import threading
    from gateway.session_events import SessionEvents
    from gateway.session_server_requests import ask_viewer, ask_viewer_result, open_requests, waiting, withdraw
    events = SessionEvents()
    events.fanout.attach(_Transport())
    asks = [threading.Thread(target=ask_viewer, args=(events, "s1", "one_shot", {}),
                             kwargs={"timeout": 10, "replay": False}),
            threading.Thread(target=ask_viewer_result, args=(events, "s1", "clarify", {"question": "name"}),
                             kwargs={"timeout": 10})]
    for ask in asks:
        ask.start()
    try:
        wait_until(lambda: waiting(events, "s1", "one_shot") and waiting(events, "s1", "clarify"), timeout=5)
        assert [entry["method"] for entry in open_requests(events, "s1")] == ["clarify"]
    finally:
        withdraw(events, "s1", "one_shot")
        withdraw(events, "s1", "clarify")
        for ask in asks:
            ask.join(5)
