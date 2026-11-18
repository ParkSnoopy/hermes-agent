"""CLI local-model orchestration without a server or downloads."""

from types import SimpleNamespace

from hermes_cli.local_runtime import local_models as lm


def test_assignment_writes_active_profile_and_clears_remote_endpoint(tmp_path, monkeypatch):
    from hermes_cli import auth, config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setattr(auth, "deactivate_provider", lambda: None)
    default = tmp_path / "default"
    named = tmp_path / "named"
    monkeypatch.setenv("HERMES_HOME", str(default))
    config.save_config({"model": {"default": "old", "provider": "custom"}})
    token = set_hermes_home_override(named)
    try:
        config.save_config({"model": {"default": "old-named", "provider": "custom",
                                     "base_url": "https://example.invalid", "api_key": "old"}})
        job = lm._job("test", "model")
        lm._assign_default(job, "local-model")
        model = config.load_config()["model"]
        assert model["default"] == "local-model"
        assert model["provider"] == "llamacpp"
        assert not model.get("base_url")
        assert not model.get("api_key")
    finally:
        reset_hermes_home_override(token)
    assert config.load_config()["model"]["default"] == "old"


def test_quickstart_sequences_install_download_and_assignment(monkeypatch):
    entry = SimpleNamespace(id="tiny", display_name="Tiny")
    variant = SimpleNamespace(model_id="tiny-q4")
    calls = []
    monkeypatch.setattr(lm.hardware, "probe_budget", lambda **kw: object())
    monkeypatch.setattr(lm, "_quickstart_target", lambda body, budget: (entry, variant))
    monkeypatch.setattr(lm, "_runtime_target", lambda: ("b1", "cpu"))
    monkeypatch.setattr(lm.binaries, "installed_engine", lambda backend: None)
    monkeypatch.setattr(lm, "_download_plan", lambda entry, variant: [("url", lm.Path("missing"), 10)])
    monkeypatch.setattr(lm, "_install_engine_job", lambda job, backend: calls.append("install"))
    monkeypatch.setattr(lm, "_repriced_quickstart", lambda job, body, v: (entry, variant, [("url", lm.Path("missing"), 10)]))
    monkeypatch.setattr(lm, "_run_download_plan", lambda *args: calls.append("download"))
    monkeypatch.setattr(lm, "_set_runtime_enabled", lambda enabled: {"enabled": enabled})
    monkeypatch.setattr(lm, "_ensure_server", lambda *args, **kwargs: calls.append("start"))
    monkeypatch.setattr(lm, "_assign_default", lambda job, model: calls.append(("assign", model)))

    def run_job(job, name, body, **kwargs):
        try:
            body()
            job["status"] = "done"
        finally:
            kwargs["on_exit"]()

    monkeypatch.setattr(lm, "_spawn_job", run_job)
    result = lm.local_models_quickstart(lm.QuickstartBody(model_id="tiny"))
    assert result["needs_runtime"] and result["needs_download"]
    assert calls == ["install", "download", "start", ("assign", "tiny-q4")]
    assert lm._job_view(lm._JOBS[result["job_id"]])["status"] == "done"