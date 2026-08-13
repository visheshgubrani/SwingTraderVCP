"""Idempotent orchestration for the owner workstation's daily EOD scan."""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from arq.connections import ArqRedis
from arq.jobs import Job, JobStatus
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.screening_config import TechnicalScreeningConfig


ACTIVE_JOB_STATUSES = {
    JobStatus.deferred,
    JobStatus.queued,
    JobStatus.in_progress,
}


@dataclass(frozen=True)
class PersonalScanRun:
    scan_run_id: UUID
    status: str
    reused: bool
    as_of_date: datetime.date


def canonical_config_payload(config: TechnicalScreeningConfig) -> tuple[str, str]:
    """Return stable JSON and a compact identity for one scanner configuration."""
    payload = json.dumps(
        config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload, hashlib.sha256(payload.encode()).hexdigest()


def personal_scan_job_id(scan_run_id: UUID | str) -> str:
    return f"personal-scan:{scan_run_id}"


async def resolve_reference_eod_date() -> datetime.date:
    """Choose the latest candle date shared by the largest active-universe cohort."""
    async with async_session() as session:
        result = await session.execute(
            text(
                """
                WITH latest_by_instrument AS (
                    SELECT
                        i.id,
                        (MAX(c.candle_start) AT TIME ZONE 'Asia/Kolkata')::date
                            AS latest_candle_date
                    FROM instruments i
                    JOIN universe_memberships m
                        ON m.instrument_id = i.id
                       AND m.universe_code = 'NIFTY500'
                       AND m.member_to IS NULL
                    LEFT JOIN market_candles c
                        ON c.instrument_id = i.id
                       AND c.timeframe = '1d'
                    WHERE i.active = true
                    GROUP BY i.id
                )
                SELECT latest_candle_date
                FROM latest_by_instrument
                WHERE latest_candle_date IS NOT NULL
                GROUP BY latest_candle_date
                ORDER BY COUNT(*) DESC, latest_candle_date DESC
                LIMIT 1
                """
            )
        )
        reference_date = result.scalar_one_or_none()
    if reference_date is None:
        raise ValueError("No Nifty 500 EOD candles are available to scan.")
    return reference_date


async def _job_status(redis: ArqRedis, scan_run_id: UUID) -> JobStatus:
    return await Job(personal_scan_job_id(scan_run_id), redis).status()


def _running_run_is_stale(started_at: datetime.datetime | None) -> bool:
    if started_at is None:
        return True
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=datetime.timezone.utc)
    age = datetime.datetime.now(datetime.timezone.utc) - started_at
    return age.total_seconds() >= settings.personal_scan_running_stale_seconds


async def _mark_enqueue_failed(scan_run_id: UUID, message: str) -> None:
    async with async_session() as session:
        await session.execute(
            text(
                """
                UPDATE scan_runs
                SET status = 'failed', completed_at = now(), error_message = :error
                WHERE id = :scan_run_id AND status = 'queued'
                """
            ),
            {"scan_run_id": scan_run_id, "error": message[:1000]},
        )
        await session.commit()


async def ensure_personal_scan(
    redis: ArqRedis,
    *,
    config: TechnicalScreeningConfig | None = None,
    triggered_by: str = "manual",
) -> PersonalScanRun:
    """Create, reuse, and durably enqueue one personal run per EOD/config pair."""
    effective_config = config or TechnicalScreeningConfig()
    config_json, config_hash = canonical_config_payload(effective_config)
    as_of_date = await resolve_reference_eod_date()
    lock_key = f"personal-scan:{as_of_date.isoformat()}:{config_hash}"

    reused = False
    selected_id: UUID | None = None
    selected_status = "queued"

    async with async_session() as session:
        # Serialize contenders for the same date/configuration without imposing a
        # uniqueness constraint that would invalidate preserved legacy duplicates.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id, status, started_at
                    FROM scan_runs
                    WHERE visibility = 'personal'
                      AND as_of_date = :as_of_date
                      AND technical_config = CAST(:technical_config AS jsonb)
                      AND status IN ('queued', 'running', 'succeeded')
                    ORDER BY
                        CASE status
                            WHEN 'succeeded' THEN 1
                            WHEN 'running' THEN 2
                            ELSE 3
                        END,
                        created_at DESC
                    FOR UPDATE
                    """
                ),
                {
                    "as_of_date": as_of_date,
                    "technical_config": config_json,
                },
            )
        ).all()

        for row in rows:
            if row.status == "succeeded":
                selected_id = row.id
                selected_status = row.status
                reused = True
                break

            status = await _job_status(redis, row.id)
            if status in ACTIVE_JOB_STATUSES:
                selected_id = row.id
                selected_status = row.status
                reused = True
                break

            if row.status == "queued" and status == JobStatus.not_found:
                selected_id = row.id
                selected_status = row.status
                reused = True
                break

            if (
                row.status == "running"
                and status == JobStatus.not_found
                and not _running_run_is_stale(row.started_at)
            ):
                selected_id = row.id
                selected_status = row.status
                reused = True
                break

            await session.execute(
                text(
                    """
                    UPDATE scan_runs
                    SET
                        status = 'failed',
                        completed_at = now(),
                        error_message = :error
                    WHERE id = :scan_run_id
                      AND status IN ('queued', 'running')
                    """
                ),
                {
                    "scan_run_id": row.id,
                    "error": (
                        "Recovered orphaned personal scan: ARQ job is no longer active "
                        f"(status={status.value})."
                    ),
                },
            )

        if selected_id is None:
            inserted = await session.execute(
                text(
                    """
                    INSERT INTO scan_runs (
                        universe_code,
                        status,
                        triggered_by,
                        technical_config,
                        visibility,
                        as_of_date
                    )
                    VALUES (
                        'NIFTY500',
                        'queued',
                        :triggered_by,
                        CAST(:technical_config AS jsonb),
                        'personal',
                        :as_of_date
                    )
                    RETURNING id
                    """
                ),
                {
                    "triggered_by": triggered_by,
                    "technical_config": config_json,
                    "as_of_date": as_of_date,
                },
            )
            selected_id = inserted.scalar_one()
            selected_status = "queued"

        await session.commit()

    if selected_status == "queued":
        try:
            job = await redis.enqueue_job(
                "run_technical_scan",
                str(selected_id),
                _job_id=personal_scan_job_id(selected_id),
            )
            if job is None:
                status = await _job_status(redis, selected_id)
                if status not in ACTIVE_JOB_STATUSES:
                    raise RuntimeError(
                        f"Redis did not accept the scan job (status={status.value})."
                    )
        except Exception as exc:
            await _mark_enqueue_failed(selected_id, f"Enqueuing failed: {exc}")
            raise

    return PersonalScanRun(
        scan_run_id=selected_id,
        status=selected_status,
        reused=reused,
        as_of_date=as_of_date,
    )
