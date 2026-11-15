"""Manual cron run prompt consumption and scheduling."""

from unittest.mock import patch



class TestManualRunPromptConsumption:
    """The stamped transient prompt reaches the fire that consumes the
    manual occurrence, and only that fire."""

    def test_run_one_job_consumes_stamped_prompt(self):
        from cron import scheduler

        captured = {}

        def fake_body(job, **kwargs):
            captured["extra_prompt"] = kwargs.get("extra_prompt")
            return True

        job = {
            "id": "j1",
            "manual_run_at": "2026-08-28T00:00:00+00:00",
            "manual_run_prompt": "focus on EU numbers",
        }
        with patch.object(scheduler, "_run_one_job_body", side_effect=fake_body), patch.object(
            scheduler, "_run_with_fire_claim_heartbeat", side_effect=lambda j, fn: fn(None)
        ):
            assert scheduler.run_one_job(job) is True
        assert captured["extra_prompt"] == "focus on EU numbers"

    def test_explicit_extra_prompt_wins_over_stamp(self):
        from cron import scheduler

        captured = {}

        def fake_body(job, **kwargs):
            captured["extra_prompt"] = kwargs.get("extra_prompt")
            return True

        job = {
            "id": "j1",
            "manual_run_at": "2026-08-28T00:00:00+00:00",
            "manual_run_prompt": "stale stamp",
        }
        with patch.object(scheduler, "_run_one_job_body", side_effect=fake_body), patch.object(
            scheduler, "_run_with_fire_claim_heartbeat", side_effect=lambda j, fn: fn(None)
        ):
            scheduler.run_one_job(job, extra_prompt="direct context")
        assert captured["extra_prompt"] == "direct context"

    def test_stamp_ignored_without_manual_run_intent(self):
        """A leftover prompt with no manual_run_at (defensive) is not injected."""
        from cron import scheduler

        captured = {}

        def fake_body(job, **kwargs):
            captured["extra_prompt"] = kwargs.get("extra_prompt")
            return True

        job = {"id": "j1", "manual_run_prompt": "orphaned"}
        with patch.object(scheduler, "_run_one_job_body", side_effect=fake_body), patch.object(
            scheduler, "_run_with_fire_claim_heartbeat", side_effect=lambda j, fn: fn(None)
        ):
            scheduler.run_one_job(job)
        assert captured["extra_prompt"] is None


class TestTriggerJobPromptStamp:
    def test_trigger_stamps_and_mark_run_clears(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        import cron.jobs as jobs_mod
        import importlib

        importlib.reload(jobs_mod)
        job = jobs_mod.create_job(
            prompt="daily report", schedule="0 9 * * *", name="stamp-test"
        )
        triggered = jobs_mod.trigger_job(job["id"], extra_prompt="just EU today")
        assert triggered["manual_run_prompt"] == "just EU today"
        assert triggered["manual_run_at"] == triggered["next_run_at"]

        jobs_mod.mark_job_run(job["id"], success=True)
        after = jobs_mod.get_job(job["id"])
        assert "manual_run_prompt" not in after
        assert "manual_run_at" not in after

    def test_retrigger_without_prompt_clears_stale_stamp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        import cron.jobs as jobs_mod
        import importlib

        importlib.reload(jobs_mod)
        job = jobs_mod.create_job(
            prompt="daily report", schedule="0 9 * * *", name="stamp-test-2"
        )
        jobs_mod.trigger_job(job["id"], extra_prompt="first context")
        retriggered = jobs_mod.trigger_job(job["id"])
        assert retriggered.get("manual_run_prompt") is None
