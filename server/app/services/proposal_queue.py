"""Queue boundary for the dedicated concurrency-one P10 worker."""

from __future__ import annotations

from typing import Any

from app.config import settings


async def enqueue_proposal_batch(redis: Any, scan_run_id: str) -> bool:
    if not settings.proposal_automation_enabled:
        return False
    job = await redis.enqueue_job(
        "run_eod_proposal_batch",
        str(scan_run_id),
        _job_id=f"p10-proposals:{scan_run_id}",
        _queue_name=settings.proposal_queue_name,
    )
    return job is not None
