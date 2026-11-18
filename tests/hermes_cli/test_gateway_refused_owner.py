"""A refused local control channel stays an authorization failure."""
from pathlib import Path

from hermes_cli import gateway_runtime as runtime


def test_discovery_routes_a_refused_control_channel_through_refused(monkeypatch, tmp_path):
    from hermes_cli import gateway_runtime_discovery as discovery

    home = tmp_path / "home"
    home.mkdir()
    seen = []
    refused = runtime._refused

    def record_refusal(path):
        seen.append(Path(path))
        return refused(path)

    def refuse_identify(*_args, **_kwargs):
        raise PermissionError("runtime control socket inaccessible")

    monkeypatch.setattr(runtime, "_refused", record_refusal)
    monkeypatch.setattr(discovery, "query_identify", refuse_identify)
    result = runtime.discover_gateway_endpoint(home, timeout=1)
    assert (result.state, result.reason_code) == ("inaccessible", "authorization")
    assert [p.resolve() for p in seen] == [home.resolve()]