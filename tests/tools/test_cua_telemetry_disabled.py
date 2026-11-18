"""The third-party computer-use driver must never enable its analytics."""


def test_cua_driver_telemetry_is_forced_off(monkeypatch):
    from tools.bot_desktop import runtime
    from tools.computer_use.cua_backend import cua_driver_child_env

    monkeypatch.setattr(runtime, "desktop_env", lambda env: env)
    env = cua_driver_child_env({"PATH": "/usr/bin", "CUA_DRIVER_RS_TELEMETRY_ENABLED": "1"})
    assert env["PATH"] == "/usr/bin"
    assert env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] == "0"
