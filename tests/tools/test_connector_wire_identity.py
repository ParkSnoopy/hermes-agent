"""Connector request identity, local diagnostics, and connection initiators."""

import json
import logging

import pytest

from tools.connectors.gateway.client import ConnectorClient
from tools.managed_gateway_identity import user_agent


class _Response:
    def __init__(self, status, body, request_id="gw-req-1"):
        self.status_code = status
        self._body = body
        self.headers = {"x-request-id": request_id}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    def request(self, method, url, *, headers=None, json=None, timeout=None):
        self.sent.append({"url": url, "headers": dict(headers or {}), "json": json})
        return self.responses.pop(0)


def _client(transport, **kwargs):
    return ConnectorClient(
        transport=transport, endpoint_resolver=lambda: "https://gw.test",
        header_provider=lambda url: {"Authorization": "Bearer t"}, **kwargs,
    )


def test_connector_requests_do_not_send_correlation_or_fingerprint():
    transport = _Transport(_Response(200, {"schemas": {}}), _Response(200, {"schemas": {}}))
    client = _client(transport)
    client.schemas(["GMAIL_SEND_EMAIL"])
    client.schemas(["GMAIL_SEND_EMAIL"])

    assert user_agent().startswith("hermes-agent/")
    for sent in transport.sent:
        headers = sent["headers"]
        assert headers["User-Agent"] == user_agent()
        assert not any(k.lower().startswith("x-nous-hermes-") or k.lower() == "x-request-id" for k in headers)
        assert " " not in headers["User-Agent"]


def test_gateway_request_id_is_logged_on_failure_and_success(caplog):
    transport = _Transport(
        _Response(200, {"schemas": {}}, request_id="ok-id"),
        _Response(403, {"error": {"code": "TOOL_NOT_ALLOWED", "message": "no"}}, request_id="bad-id"),
    )
    client = _client(transport)
    with caplog.at_level(logging.DEBUG, logger="tools.managed_gateway_identity"):
        client.schemas(["X"])
        with pytest.raises(Exception) as raised:
            client.schemas(["X"])
    assert raised.value.request_id == "bad-id"
    lines = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert (logging.DEBUG, "connector connectors.schemas 200 - request_id=ok-id") in lines
    assert (logging.WARNING,
            "connector connectors.schemas 403 TOOL_NOT_ALLOWED request_id=bad-id") in lines


def test_a_not_yet_created_account_stays_debug(caplog):
    transport = _Transport(_Response(404, {"error": "connection_not_found"}))
    with caplog.at_level(logging.DEBUG, logger="tools.managed_gateway_identity"):
        assert _client(transport).account_status("acct-1") is None
    assert [r.levelno for r in caplog.records] == [logging.DEBUG]
    assert "connectors.accounts 404" in caplog.records[0].getMessage()


def _mint_ok(names):
    return _Response(200, {"results": [{"connector": n, "status": "initiated", "connectUrl": "https://l"} for n in names]})


def test_each_connect_path_names_its_initiator(monkeypatch):
    from tools.connectors import managed
    from tools.connectors.contract import Actor, TargetState
    from tools.connectors.operation import ConnectionOperation, Target
    from tools.connectors.run import reissue

    transport = _Transport(_mint_ok(["gmail"]), _mint_ok(["gmail"]), _mint_ok(["gmail"]))
    monkeypatch.setattr(managed, "ensure_guest_identity", lambda: None)
    real = ConnectorClient
    monkeypatch.setattr("tools.connectors.gateway.client.ConnectorClient", lambda **kw: real(
        transport=transport, endpoint_resolver=lambda: "https://gw.test",
        header_provider=lambda url: {"Authorization": "Bearer t"}, **kw))

    for initiator in (managed.AGENT_TOOL, managed.DESKTOP_PAGE):
        operation = ConnectionOperation([Target("gmail", "connector", "connect")])
        client = (managed.managed_client(creates_guest=True) if initiator == managed.AGENT_TOOL
                  else managed.managed_client(creates_guest=True, initiator=initiator))
        managed.mint(client, operation, ["gmail"], reinitiate=False, actor=Actor.backend_watcher)

    operation.transition("gmail", TargetState.failed, Actor.backend_watcher)
    assert reissue(operation, ["gmail"]) is None  # the card's Try again

    assert [sent["json"]["initiator"] for sent in transport.sent] == ["agent_tool", "desktop_page", "card_retry"]


def test_operation_logs_open_transitions_and_settle(caplog):
    from tools.connectors import live
    from tools.connectors.contract import Actor, SettleReason, TargetState
    from tools.connectors.operation import ConnectionOperation, Target

    operation = ConnectionOperation([Target("gmail", "connector", "connect")], session_key="s-log")
    with caplog.at_level(logging.INFO, logger="tools.connectors"):
        live.open(operation)
        operation.transition("gmail", TargetState.initiated, Actor.backend_watcher)
        operation.transition("gmail", TargetState.connected, Actor.backend_watcher)
        operation.settle(SettleReason.all_resolved)
        live.close(operation)
    op = operation.op_id
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO] == [
        f"connection op {op} opened: connector:gmail:connect",
        f"connection op {op}: gmail pending -> initiated by backend_watcher",
        f"connection op {op}: gmail initiated -> connected by backend_watcher",
        f"connection op {op} settled by all_resolved: gmail=connected",
    ]
    assert json.dumps(operation.snapshot())  # snapshot still serializes


def _outcomes(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "tools.managed_gateway_identity"]


def test_a_200_whose_body_failed_is_a_warning_with_the_request_id(caplog):
    """The outcome line follows the checked body: a failed call inside the execute envelope and a body
    that does not parse are both failures, and both keep the gateway's request id."""
    from tools.connectors.gateway.errors import ToolGatewayError
    from tools.connectors.gateway.merge import PlannedCall

    from tools.connectors.gateway import client as client_module

    failed_call = {"results": [{"error": {"code": "CONNECTION_REQUIRED", "message": "connect gmail"}}]}
    transport = _Transport(_Response(200, failed_call, request_id="exec-id"),
                           _Response(200, ValueError("not json"), request_id="junk-id"),
                           _Response(200, {"unexpected": True}, request_id="one-id"))
    client = _client(transport)
    plan = PlannedCall(position=0, name="gmail_send_email", connector="gmail", tool="GMAIL_SEND_EMAIL", arguments={})
    with caplog.at_level(logging.DEBUG, logger="tools.managed_gateway_identity"):
        # An older gateway answers the array form: a 200 envelope with the call's failure inside.
        client_module._LEGACY_EXECUTE_ORIGINS.add("https://gw.test")
        try:
            assert client.execute([plan])[0]["error"]["code"] == "CONNECTION_REQUIRED"
        finally:
            client_module._LEGACY_EXECUTE_ORIGINS.discard("https://gw.test")
        with pytest.raises(ToolGatewayError) as raised:
            client.schemas(["X"])
        with pytest.raises(ToolGatewayError):
            client.execute([plan])
    assert raised.value.request_id == "junk-id" and raised.value.code == "INVALID_RESPONSE"
    assert _outcomes(caplog) == [
        (logging.WARNING, "connector connectors.execute 200 CONNECTION_REQUIRED request_id=exec-id"),
        (logging.WARNING, "connector connectors.schemas 200 INVALID_RESPONSE request_id=junk-id"),
        (logging.WARNING, "connector connectors.execute 200 INVALID_RESPONSE request_id=one-id"),
    ]


def test_portal_tools_304_is_a_success_and_a_bad_body_a_failure(caplog):
    from tools.connectors.portal.client import NotModified, PortalConnectorClient
    from tools.connectors.portal.errors import PortalToolsUnavailable

    transport = _Transport(_Response(304, None, request_id="same-id"), _Response(200, [], request_id="bad-id"))
    client = PortalConnectorClient(transport=transport, endpoint_resolver=lambda: "https://portal.test",
                                   header_provider=lambda url: {"Authorization": "Bearer t"})
    with caplog.at_level(logging.DEBUG, logger="tools.managed_gateway_identity"):
        assert isinstance(client.tools("gmail", if_none_match="etag-1"), NotModified)
        with pytest.raises(PortalToolsUnavailable) as raised:
            client.tools("gmail")
    assert raised.value.request_id == "bad-id"
    assert _outcomes(caplog) == [
        (logging.DEBUG, "connector portal.connectors.tools 304 - request_id=same-id"),
        (logging.WARNING, "connector portal.connectors.tools 200 INVALID_RESPONSE request_id=bad-id"),
    ]


def test_try_again_mints_without_correlation_ids(monkeypatch, caplog):
    from tools.connectors import managed
    from tools.connectors.contract import Actor, TargetState
    from tools.connectors.operation import ConnectionOperation, Target
    from tools.connectors.run import reissue


    transport = _Transport(_mint_ok(["gmail"]))
    monkeypatch.setattr(managed, "ensure_guest_identity", lambda: None)
    real = ConnectorClient
    monkeypatch.setattr("tools.connectors.gateway.client.ConnectorClient", lambda **kw: real(
        transport=transport, endpoint_resolver=lambda: "https://gw.test",
        header_provider=lambda url: {"Authorization": "Bearer t"}, **kw))
    operation = ConnectionOperation([Target("gmail", "connector", "connect")])
    operation.transition("gmail", TargetState.initiated, Actor.backend_watcher)
    operation.transition("gmail", TargetState.failed, Actor.backend_watcher)

    with caplog.at_level(logging.DEBUG, logger="tools.managed_gateway_identity"):
        assert reissue(operation, ["gmail"]) is None
    headers = transport.sent[0]["headers"]
    assert not any(k.lower().startswith("x-nous-hermes-") or k.lower() == "x-request-id" for k in headers)
