"""Native session edits remain available without foreign-history RPCs."""

from tui_gateway.contracts import CANONICAL_METHODS, METHODS


def test_native_import_is_a_session_mutation_not_a_foreign_history_rpc():
    for method in ("session.foreign.list", "session.foreign.preview", "session.foreign.import"):
        assert method not in METHODS
        assert method not in CANONICAL_METHODS

    mutation = CANONICAL_METHODS["session.mutate"].params.model_validate({
        "session_id": "native-session",
        "request_id": "import-request",
        "expected_revision": 0,
        "operation": "import",
        "payload": {"sessions": []},
    })
    assert mutation.model_dump()["operation"] == "import"
