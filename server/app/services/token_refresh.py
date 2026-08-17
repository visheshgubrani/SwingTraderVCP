"""
Scheduled Fyers token refresh job.

Runs daily via arq cron (default 08:50 IST) to refresh the Fyers access token
before market open. All Fyers clients obtain tokens via auth_service, never
directly from here.

This job is the ONLY scheduled path for token refresh. Lazy/on-demand refresh
is a fallback in auth_service.get_valid_access_token(), not the primary path.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from arq.connections import ArqRedis
from sqlalchemy import text

from app.database import async_session
from app.services.auth_service import refresh_and_save, _emit_system_event

logger = logging.getLogger(__name__)


async def run_token_refresh(ctx: dict[str, Any]) -> dict[str, Any]:
    """
    arq job: refresh the Fyers access token using the stored refresh token.
    Records execution in `job_runs` table with required `triggered_by` (AUTH-001).
    Returns {"status": "refreshed"} on success, {"status": "failed", ...} on failure.
    """
    redis: ArqRedis = ctx["redis"]
    job_id = str(ctx.get("job_id", "scheduled"))
    triggered_by = str(ctx.get("triggered_by", "scheduler"))

    async with async_session() as db:
        # Record job start in job_runs with mandatory triggered_by (AUTH-001)
        result = await db.execute(
            text("""
                INSERT INTO job_runs (
                    job_type,
                    job_key,
                    triggered_by,
                    status,
                    started_at,
                    input_payload
                )
                VALUES (
                    'token_refresh',
                    :job_key,
                    :triggered_by,
                    'running',
                    now(),
                    :input_payload
                )
                RETURNING id
            """),
            {
                "job_key": f"token_refresh_{job_id}",
                "triggered_by": triggered_by,
                "input_payload": json.dumps({"job_id": job_id, "triggered_by": triggered_by}),
            },
        )
        run_id = result.scalar()
        await db.commit()

        try:
            new_token = await refresh_and_save(db, redis)

            if new_token:
                await db.execute(
                    text("""
                        UPDATE job_runs
                        SET status = 'succeeded',
                            completed_at = now(),
                            result_payload = :result_payload
                        WHERE id = :run_id
                    """),
                    {
                        "run_id": run_id,
                        "result_payload": json.dumps({"status": "refreshed"}),
                    },
                )
                await db.commit()
                logger.info("Token refresh succeeded (job_run=%s)", run_id)
                return {"status": "refreshed", "run_id": str(run_id)}

            # refresh_and_save already emitted system_event
            await db.execute(
                text("""
                    UPDATE job_runs
                    SET status = 'failed',
                        completed_at = now(),
                        error_message = 'Token refresh failed — see system_events',
                        result_payload = :result_payload
                    WHERE id = :run_id
                """),
                {
                    "run_id": run_id,
                    "result_payload": json.dumps({"status": "failed", "reason": "refresh_rejected"}),
                },
            )
            await db.commit()
            logger.error("Token refresh failed (job_run=%s)", run_id)
            return {"status": "failed", "run_id": str(run_id)}

        except Exception as exc:
            logger.exception("Token refresh job crashed")
            await _emit_system_event(
                db,
                "critical",
                "auth_refresh_crashed",
                {"error": str(exc)},
            )
            await db.execute(
                text("""
                    UPDATE job_runs
                    SET status = 'failed',
                        completed_at = now(),
                        error_message = :error,
                        result_payload = :result_payload
                    WHERE id = :run_id
                """),
                {
                    "run_id": run_id,
                    "error": str(exc),
                    "result_payload": json.dumps({"status": "crashed", "error": str(exc)}),
                },
            )
            await db.commit()
            return {"status": "crashed", "error": str(exc)}
