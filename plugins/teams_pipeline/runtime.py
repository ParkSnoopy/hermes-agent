"""Gateway runtime wiring for the Teams meeting pipeline plugin."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from gateway.config import Platform
from plugins.teams_pipeline.pipeline import TeamsMeetingPipeline
from plugins.teams_pipeline.store import TeamsPipelineStore, resolve_teams_pipeline_store_path
from plugins.teams_pipeline.subscriptions import build_graph_client

logger = logging.getLogger(__name__)

def build_pipeline_runtime_config(gateway_config: Any) -> dict[str, Any]:
    """Read pipeline settings from the retained Graph webhook configuration."""
    webhook = gateway_config.platforms.get(Platform.MSGRAPH_WEBHOOK)
    return dict((webhook.extra or {}).get("meeting_pipeline") or {}) if webhook else {}


def build_pipeline_runtime(gateway: Any) -> TeamsMeetingPipeline:
    teams_sender = None
    pipeline_config = build_pipeline_runtime_config(gateway.config)
    if (pipeline_config.get("teams_delivery") or {}).get("enabled"):
        from plugins.teams_pipeline.summary_writer import TeamsSummaryWriter
        teams_sender = TeamsSummaryWriter()
    return TeamsMeetingPipeline(
        graph_client=build_graph_client(), store=TeamsPipelineStore(resolve_teams_pipeline_store_path()),
        config=pipeline_config, teams_sender=teams_sender,
    )


def bind_gateway_runtime(gateway: Any) -> bool:
    """Attach the Teams pipeline runtime to the msgraph webhook adapter."""
    adapter = gateway.adapters.get(Platform.MSGRAPH_WEBHOOK)
    if adapter is None:
        return False
    if getattr(gateway, "_teams_pipeline_runtime", None) is not None:
        return True
    try:
        runtime = build_pipeline_runtime(gateway)
    except Exception as exc:
        # Without a runtime, notifications must still be acked (and dropped) so Graph does not retry-storm.
        gateway._teams_pipeline_runtime_error = str(exc)
        logger.warning(
            "Teams pipeline runtime unavailable: %s. Installing a drop-scheduler "
            "so Graph notifications ack cleanly without piling up unbound.", exc,
        )

        async def _drop(notification: dict[str, Any], event: Any) -> None:
            logger.debug("Dropping Graph notification because runtime is unavailable: id=%s resource=%s",
                         notification.get("id"), notification.get("resource"))
        adapter.set_notification_scheduler(_drop)
        return False

    # Graph wants a prompt 202 once the work is durable: accept (persist the job) inside the request,
    # run the fetch/transcript/summary pipeline detached. An accept failure still fails the request.
    background = getattr(adapter, "_background_tasks", set())
    # Claim a job before scheduling its external effects: a no-id duplicate in one Graph batch
    # returns the same still-"received" job (run_job persists progress only once it runs).
    scheduled: set[str] = set()

    async def _schedule(notification: dict[str, Any], event: Any) -> None:
        job = runtime.create_job_from_notification(notification)
        if job.status != "received" or job.job_id in scheduled:  # duplicate of a running or finished job
            return
        scheduled.add(job.job_id)
        task = asyncio.create_task(runtime.run_job(job.job_id))
        background.add(task)
        task.add_done_callback(background.discard)
        task.add_done_callback(lambda _task: scheduled.discard(job.job_id))
    adapter.set_notification_scheduler(_schedule)
    gateway._teams_pipeline_runtime = runtime
    gateway._teams_pipeline_runtime_error = None
    return True
