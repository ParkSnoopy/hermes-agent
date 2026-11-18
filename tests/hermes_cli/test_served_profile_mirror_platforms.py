"""Shared API listener status is projected onto served profiles."""
import json
import os

import pytest


@pytest.fixture
def served_root(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    (root / "profiles" / "alpha").mkdir(parents=True)
    (root / "config.yaml").write_text("gateway: {multiplex_profiles: true}\n", encoding="utf-8")
    (root / "gateway.pid").write_text(json.dumps({"pid": os.getpid(), "hermes_home": str(root)}), encoding="utf-8")
    (root / "gateway_state.json").write_text(json.dumps({
        "pid": os.getpid(), "hermes_home": str(root), "gateway_state": "running",
        "served_profiles": ["default", "alpha", "beta"],
        "platforms": {
            "api_server": {"state": "connected", "listener_base": "http://127.0.0.1:45719"},
            "webhook": {"state": "fatal", "error_code": "port_in_use"},
            "alpha:msgraph_webhook": {"state": "connected"},
        }}), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.delenv("GATEWAY_MULTIPLEX_PROFILES", raising=False)
    import hermes_constants
    from gateway import status
    monkeypatch.setattr(status, "_read_process_cmdline", lambda pid: "hermes gateway run")
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None)
    return root


def test_served_profile_projects_the_default_listener_mirrors_with_their_url(served_root):
    from gateway.status import profile_platforms_from_multiplexer, resolve_gateway_liveness
    alpha = served_root / "profiles" / "alpha"
    live = resolve_gateway_liveness(profile_dir=alpha, health_probe=None, use_cache=False)
    plats = profile_platforms_from_multiplexer(live.runtime, "alpha")
    assert plats["api_server"]["state"] == "connected"
    assert plats["api_server"]["ingress_url"] == "http://127.0.0.1:45719/p/alpha/v1"
    assert "webhook" not in plats
    assert plats["msgraph_webhook"] == {"state": "connected"}
    assert "ingress_url" not in profile_platforms_from_multiplexer(live.runtime, "default").get("api_server", {})


def test_default_profile_keeps_its_flat_adapters_when_rekeyed(served_root):
    from gateway.status import profile_platforms_from_multiplexer, resolve_gateway_liveness
    standalone = {"gateway_state": "running", "served_profiles": [],
                  "platforms": {"webhook": {"state": "connected"}}}
    assert profile_platforms_from_multiplexer(standalone, "default") == {"webhook": {"state": "connected"}}
    live = resolve_gateway_liveness(profile_dir=served_root / "profiles" / "alpha", health_probe=None, use_cache=False)
    plats = profile_platforms_from_multiplexer(live.runtime, "default")
    assert plats["api_server"]["state"] == "connected"
    assert "listener_base" in plats["api_server"]
    assert plats["webhook"]["state"] == "fatal"
    assert all("ingress_url" not in entry for entry in plats.values() if isinstance(entry, dict))
    assert "msgraph_webhook" not in plats