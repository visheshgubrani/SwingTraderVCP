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


async def enqueue_single_proposal(
    redis: Any,
    screening_result_id: str,
) -> bool:
    """Queue one shortlist candidate on the dedicated concurrency-1 worker.

    Always treated as a manual operator action: a unique job id lets the same
    stock be re-run after a prompt or geometry change without waiting for a
    full top-N batch.
    """
    job = await redis.enqueue_job(
        "run_single_proposal",
        str(screening_result_id),
        _job_id=f"p10-proposals:single:{screening_result_id}:{uuid4()}",
        _queue_name=settings.proposal_queue_name,
    )
    return job is not None
