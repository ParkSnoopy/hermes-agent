"""Local quickstart reprices with the installed engine's budget."""

from hermes_cli.local_runtime import local_models as lm
from hermes_cli.local_runtime.catalog import CATALOG, VariantChoice
from hermes_cli.local_runtime.estimator import HardwareBudget


def test_quickstart_runs_install_download_and_activation_in_order(tmp_path, monkeypatch):
    calls = []
    entry = CATALOG[0]
    variant = entry.variants[0]
    plan = [("url", tmp_path / "model", 10)]
    monkeypatch.setattr(lm, "_JOBS", {})
    monkeypatch.setattr(lm.hardware, "probe_budget", lambda **kw: None)
    monkeypatch.setattr(lm, "_quickstart_target", lambda body, budget: (entry, variant))
    monkeypatch.setattr(lm, "_runtime_target", lambda: ("b1", "cpu"))
    monkeypatch.setattr(lm.binaries, "installed_engine", lambda backend: None)
    monkeypatch.setattr(lm, "_download_plan", lambda entry, variant: plan)
    monkeypatch.setattr(lm, "_install_engine_job", lambda job, backend: calls.append("install"))
    monkeypatch.setattr(lm, "_run_download_plan", lambda *args: calls.append("download"))
    monkeypatch.setattr(lm, "_set_runtime_enabled", lambda enabled: {"enabled": enabled})
    monkeypatch.setattr(lm, "_ensure_server", lambda *args, **kwargs: calls.append("server"))
    monkeypatch.setattr(lm, "_assign_default", lambda *args: calls.append("assign"))

    def run_inline(job, name, body, *, on_exit, **kwargs):
        try:
            body()
        finally:
            on_exit()

    monkeypatch.setattr(lm, "_spawn_job", run_inline)
    result = lm.local_models_quickstart(lm.QuickstartBody())
    assert result["needs_runtime"] is True
    assert result["needs_download"] is True
    assert result["download_bytes"] == 10
    assert calls == ["install", "download", "server", "assign"]
    assert lm._JOBS[result["job_id"]]["phase"] == "done"


def test_quickstart_reprices_after_installing_the_engine(tmp_path, monkeypatch):
    gib = 1 << 30
    as_card = HardwareBudget(usable_vram_bytes=22 * gib, total_device_bytes=24 * gib,
                             ram_available_bytes=128 * gib, uma=False)
    fit = CATALOG[1]
    monkeypatch.setattr(lm.hardware, "probe_budget", lambda **kw: as_card)
    monkeypatch.setattr(lm.catalog, "recommended_entry",
                        lambda budget, entries=None, backend="auto": (fit, "best-fits"))
    monkeypatch.setattr(lm.catalog, "select_variant", lambda entry, budget: VariantChoice(
        variant=entry.variants[0], zero_spill=True, reason_key="best-fits"))
    monkeypatch.setattr(lm, "_engine_too_old", lambda min_engine: False)
    monkeypatch.setattr(lm, "_download_plan", lambda entry, variant: [("url", tmp_path / "model", 10)])
    job = {}
    entry, choice, plan = lm._repriced_quickstart(job, lm.QuickstartBody(), CATALOG[0].variants[0])
    assert entry.id == fit.id
    assert choice.model_id == fit.variants[0].model_id
    assert job["model_id"] == fit.id
    assert plan[0][1] == tmp_path / "model"
