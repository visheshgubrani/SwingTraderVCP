"""Weekday Fyers re-auth reminder.

Fyers access tokens expire at 06:30 IST and cannot be refreshed. This job
pings the owner on Telegram when today's 2FA login has not been completed.
It never calls validate-refresh-token and never places orders.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.auth_service import _emit_system_event, get_auth_status_from_db
from app.services.telegram_notifier import (
    TelegramConfigError,
    TelegramSendError,
    send_telegram_message,
    telegram_configured,
)

logger = logging.getLogger(__name__)


def build_auth_reminder_message() -> str:
    app_url = (settings.frontend_public_url or "").rstrip("/") or "the personal app"
    return (
        "Fyers session expired at 06:30 IST. "
        f"Open {app_url} and complete today's 2FA login."
    )


async def run_auth_reminder(ctx: dict[str, Any]) -> dict[str, Any]:
    """
    arq job: remind the owner to complete daily Fyers OAuth when auth is unhealthy.
    Records execution in `job_runs` with required `triggered_by` (AUTH-001).
    """
    job_id = str(ctx.get("job_id", "scheduled"))
    triggered_by = str(ctx.get("triggered_by", "scheduler"))

    async with async_session() as db:
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
                    'auth_reminder',
                    :job_key,
                    :triggered_by,
                    'running',
                    now(),
                    :input_payload
                )
                RETURNING id
            """),
            {
                "job_key": f"auth_reminder_{job_id}",
                "triggered_by": triggered_by,
                "input_payload": json.dumps(
                    {"job_id": job_id, "triggered_by": triggered_by}
                ),
            },
        )
        run_id = result.scalar()
        await db.commit()

        try:
            status = await get_auth_status_from_db(db)
            if status.get("healthy"):
                await _emit_system_event(
                    db,
                    "info",
                    "auth_reminder_skipped",
                    {"reason": "already_healthy"},
                )
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
                        "result_payload": json.dumps(
                            {"status": "skipped", "reason": "already_healthy"}
                        ),
                    },
                )
                await db.commit()
                logger.info("Auth reminder skipped; token already healthy (job_run=%s)", run_id)
                return {
                    "status": "skipped",
                    "reason": "already_healthy",
                    "run_id": str(run_id),
                }

            if not telegram_configured():
                await _emit_system_event(
                    db,
                    "critical",
                    "auth_reminder_failed",
                    {"reason": "telegram_not_configured"},
                )
                await db.execute(
                    text("""
                        UPDATE job_runs
                        SET status = 'failed',
                            completed_at = now(),
                            error_message = 'Telegram is not configured',
                            result_payload = :result_payload
                        WHERE id = :run_id
                    """),
                    {
                        "run_id": run_id,
                        "result_payload": json.dumps(
                            {"status": "failed", "reason": "telegram_not_configured"}
                        ),
                    },
                )
                await db.commit()
                logger.error("Auth reminder failed: Telegram not configured (job_run=%s)", run_id)
                return {
                    "status": "failed",
                    "reason": "telegram_not_configured",
                    "run_id": str(run_id),
                }

            await send_telegram_message(build_auth_reminder_message())
            await _emit_system_event(
                db,
                "info",
                "auth_reminder_sent",
                {"channel": "telegram"},
            )
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
                    "result_payload": json.dumps({"status": "sent", "channel": "telegram"}),
                },
            )
            await db.commit()
            logger.info("Auth reminder sent via Telegram (job_run=%s)", run_id)
            return {"status": "sent", "run_id": str(run_id)}

        except (TelegramConfigError, TelegramSendError) as exc:
            logger.error("Auth reminder Telegram failure: %s", exc)
            await _emit_system_event(
                db,
                "critical",
                "auth_reminder_failed",
                {"reason": "telegram_send_failed"},
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
                    "result_payload": json.dumps(
                        {"status": "failed", "reason": "telegram_send_failed"}
                    ),
                },
            )
            await db.commit()
            return {
                "status": "failed",
                "reason": "telegram_send_failed",
                "run_id": str(run_id),
            }

        except Exception as exc:
            logger.exception("Auth reminder job crashed")
            await _emit_system_event(
                db,
                "critical",
                "auth_reminder_failed",
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
