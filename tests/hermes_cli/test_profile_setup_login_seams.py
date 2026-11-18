"""Native model setup and reasoning-effort hooks consult the owning provider profile."""

from __future__ import annotations

import json
import sys

import pytest

NAME = "acme-device"

_PLUGIN_SOURCE = '''
import json, os
from providers import register_provider
from providers.base import ProviderProfile

def _log(event, **kw):
    with open(os.environ["ACME_DEVICE_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": event, **kw}) + "\\n")

def flow(config, current_model, args):
    from hermes_constants import get_hermes_home
    _log("flow", current_model=current_model, home=str(get_hermes_home()))

register_provider(ProviderProfile(
    name="acme-device", display_name="Acme Device", base_url="https://acme.example/v1",
    env_vars=("ACME_DEVICE_KEY",),
    model_setup_flow=flow, setup_reasoning_efforts=lambda model: () if model == "plain" else ("low", "high"),
))
'''


def _rediscover() -> None:
    import providers as _pkg
    _pkg._discovered = False
    for mod in [m for m in sys.modules if m.startswith("_hermes_user_provider")]:
        del sys.modules[mod]


@pytest.fixture
def acme(tmp_path, monkeypatch):
    """Install the fixture plugin into profile ``coder`` only; return (coder home, log reader)."""
    root = tmp_path / "hermes"
    coder = root / "profiles" / "coder"
    plugin_dir = coder / "plugins" / "model-providers" / NAME
    plugin_dir.mkdir(parents=True)
    (coder / "config.yaml").write_text("{}\n")  # identity marker: a bare dir is not a profile
    (plugin_dir / "plugin.yaml").write_text(
        f"name: {NAME}\nkind: model-provider\nversion: 0.0.1\ndescription: model setup seam fixture\n")
    (plugin_dir / "__init__.py").write_text(_PLUGIN_SOURCE)
    log = tmp_path / "acme.jsonl"
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("ACME_DEVICE_LOG", str(log))
    _rediscover()

    def events() -> list[dict]:
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    yield coder, events
    import providers as _pkg
    _pkg._PROVIDER_LIST_CACHE = None
    _rediscover()


def test_hermes_model_dispatch_and_effort_picker_consult_the_profile(acme, monkeypatch):
    coder, events = acme
    monkeypatch.setenv("HERMES_HOME", str(coder))
    _rediscover()
    from hermes_cli.main_provider_setup import _main_model_reasoning_efforts
    from hermes_cli.model_setup_flows import _profile_model_setup_flow

    flow = _profile_model_setup_flow(NAME)
    assert flow is not None
    flow({}, "acme-1", None)
    assert [e for e in events() if e["event"] == "flow"] == [
        {"event": "flow", "current_model": "acme-1", "home": str(coder)}]
    assert _main_model_reasoning_efforts("acme-1", NAME) == ["low", "high"]
    assert _main_model_reasoning_efforts("plain", NAME) is None
    assert _profile_model_setup_flow("openrouter") is None  # no bespoke step: the generic dispatch
