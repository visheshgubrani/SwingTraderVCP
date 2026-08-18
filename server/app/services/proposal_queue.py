"""Queue boundary for the dedicated concurrency-one P10 worker."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.config import settings


async def enqueue_proposal_batch(
    redis: Any,
    scan_run_id: str,
    *,
    manual: bool = False,
    limit: int = 20,
) -> bool:
    if not manual and not settings.proposal_automation_enabled:
        return False
    job_id = (
        f"p10-proposals:manual:{scan_run_id}:{uuid4()}"
        if manual
        else f"p10-proposals:{scan_run_id}"
    )
    job = await redis.enqueue_job(
        "run_eod_proposal_batch",
        str(scan_run_id),
        min(limit, settings.proposal_batch_limit, 20),
        manual,
        _job_id=job_id,
        _queue_name=settings.proposal_queue_name,
    )
    return job is not None
