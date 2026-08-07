"""Journal read/write service for API layer."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.journal_aggregation import PeriodBucket, compute_summary_metrics, summarize_periods
from app.services.journal_ai_coach import create_ai_run

JOURNAL_LIST_SELECT = """
    SELECT
        j.id,
        j.position_id,
        j.symbol,
        j.execution_mode,
        j.status,
        j.first_entry_fill_at,
        j.closed_at,
        j.weighted_entry_price,
        j.weighted_exit_price,
        j.gross_pnl,
        j.net_pnl,
        j.gross_r_multiple,
        j.net_r_multiple,
        j.hold_duration_hours,
        j.exit_outcome,
        j.setup_tags,
        j.execution_rating,
        j.charge_quality,
        j.pnl_mismatch,
        j.first_entry_quantity,
        j.final_entry_quantity,
        j.first_entry_price,
        m.regime
    FROM journal_entries j
    LEFT JOIN market_regime_snapshots m ON m.id = j.market_regime_snapshot_id
"""

LEASE_MINUTES = 5
PNG_MAX_BYTES = 5 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class JournalNotFoundError(LookupError):
    pass


class JournalConflictError(ValueError):
    pass


class ArtifactNotFoundError(LookupError):
    pass


async def list_journal_entries(
    db: AsyncSession,
    *,
    status: str | None = None,
    execution_mode: str | None = None,
    symbol: str | None = None,
    setup_tag: str | None = None,
    regime: str | None = None,
    exit_outcome: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    clauses = ["1=1"]
    params: dict[str, Any] = {"offset": offset, "limit": limit}

    if status:
        clauses.append("j.status = :status")
        params["status"] = status
    if execution_mode:
        clauses.append("j.execution_mode = :execution_mode")
        params["execution_mode"] = execution_mode
    if symbol:
        clauses.append("j.symbol ILIKE :symbol")
        params["symbol"] = f"%{symbol}%"
    if setup_tag:
        clauses.append("j.setup_tags ? :setup_tag")
        params["setup_tag"] = setup_tag
    if regime:
        clauses.append("m.regime = :regime")
        params["regime"] = regime
    if exit_outcome:
        clauses.append("j.exit_outcome = :exit_outcome")
        params["exit_outcome"] = exit_outcome
    if date_from:
        clauses.append("j.closed_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        clauses.append("j.closed_at < :date_to")
        params["date_to"] = date_to

    where_sql = " AND ".join(clauses)
    count_result = await db.execute(
        text(
            f"""
            SELECT COUNT(*)::integer
            FROM journal_entries j
            LEFT JOIN market_regime_snapshots m ON m.id = j.market_regime_snapshot_id
            WHERE {where_sql}
            """
        ),
        params,
    )
    total = int(count_result.scalar_one())

    result = await db.execute(
        text(
            f"""
            {JOURNAL_LIST_SELECT}
            WHERE {where_sql}
            ORDER BY COALESCE(j.closed_at, j.first_entry_fill_at) DESC NULLS LAST
            OFFSET :offset LIMIT :limit
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()], total


async def get_journal_entry(db: AsyncSession, journal_id: UUID) -> dict:
    result = await db.execute(
        text(
            f"""
            {JOURNAL_LIST_SELECT},
                j.entry_snapshot,
                j.exit_fills,
                j.exit_reasons,
                j.estimated_charges,
                j.actual_charges,
                j.risk_amount,
                j.pnl_mismatch_delta,
                j.notes,
                j.mistake_tags,
                j.emotion_tags,
                j.lessons,
                j.first_entry_price,
                j.first_entry_quantity,
                j.final_entry_quantity,
                j.entry_frozen_at,
                j.market_regime_snapshot_id,
                m.reference_eod_date,
                m.evidence AS regime_evidence,
                a.status AS artifact_status,
                a.content_hash AS artifact_content_hash
            FROM journal_entries j
            LEFT JOIN market_regime_snapshots m ON m.id = j.market_regime_snapshot_id
            LEFT JOIN journal_chart_artifacts a ON a.journal_entry_id = j.id
            WHERE j.id = :id
            """
        ),
        {"id": journal_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise JournalNotFoundError("Journal entry not found.")
    return dict(row)


async def update_journal_review(
    db: AsyncSession,
    journal_id: UUID,
    *,
    notes: str | None = None,
    execution_rating: int | None = None,
    setup_tags: list[str] | None = None,
    mistake_tags: list[str] | None = None,
    emotion_tags: list[str] | None = None,
    lessons: str | None = None,
) -> dict:
    await get_journal_entry(db, journal_id)
    await db.execute(
        text(
            """
            UPDATE journal_entries
            SET
                notes = COALESCE(:notes, notes),
                execution_rating = COALESCE(:execution_rating, execution_rating),
                setup_tags = COALESCE(CAST(:setup_tags AS jsonb), setup_tags),
                mistake_tags = COALESCE(CAST(:mistake_tags AS jsonb), mistake_tags),
                emotion_tags = COALESCE(CAST(:emotion_tags AS jsonb), emotion_tags),
                lessons = COALESCE(:lessons, lessons)
            WHERE id = :id
            """
        ),
        {
            "id": journal_id,
            "notes": notes,
            "execution_rating": execution_rating,
            "setup_tags": json.dumps(setup_tags) if setup_tags is not None else None,
            "mistake_tags": json.dumps(mistake_tags) if mistake_tags is not None else None,
            "emotion_tags": json.dumps(emotion_tags) if emotion_tags is not None else None,
            "lessons": lessons,
        },
    )
    return await get_journal_entry(db, journal_id)


async def reconcile_actual_charges(
    db: AsyncSession,
    journal_id: UUID,
    actual_charges: dict,
) -> dict:
    entry = await get_journal_entry(db, journal_id)
    if entry["status"] != "closed":
        raise JournalConflictError("Actual charges can only be set on closed trades.")

    total = Decimal(str(actual_charges.get("total", 0)))
    gross = Decimal(str(entry["gross_pnl"] or 0))
    net = gross - total

    await db.execute(
        text(
            """
            UPDATE journal_entries
            SET
                actual_charges = CAST(:actual_charges AS jsonb),
                charge_quality = 'reconciled',
                net_pnl = :net_pnl,
                net_r_multiple = CASE
                    WHEN risk_amount IS NULL OR risk_amount = 0 THEN net_r_multiple
                    ELSE :net_pnl / risk_amount
                END
            WHERE id = :id
            """
        ),
        {
            "id": journal_id,
            "actual_charges": json.dumps(actual_charges),
            "net_pnl": net,
        },
    )
    return await get_journal_entry(db, journal_id)


async def get_period_summary(
    db: AsyncSession,
    *,
    bucket: PeriodBucket,
    filters: dict,
) -> dict:
    trades, _ = await list_journal_entries(
        db,
        status="closed",
        execution_mode=filters.get("execution_mode"),
        symbol=filters.get("symbol"),
        setup_tag=filters.get("setup_tag"),
        regime=filters.get("regime"),
        exit_outcome=filters.get("exit_outcome"),
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
        offset=0,
        limit=1000,
    )
    enriched = []
    for trade in trades:
        charges = trade.get("estimated_charges") or {}
        if isinstance(charges, str):
            charges = json.loads(charges)
        trade["total_charges"] = charges.get("total", "0")
        enriched.append(trade)

    periods = summarize_periods(enriched, bucket)
    summary = compute_summary_metrics(enriched)
    reconciled = sum(1 for t in enriched if t.get("charge_quality") == "reconciled")
    return {
        "bucket": bucket,
        "periods": [
            {
                "period_key": p.period_key,
                "trade_count": p.trade_count,
                "wins": p.wins,
                "losses": p.losses,
                "win_rate": str(p.win_rate) if p.win_rate is not None else None,
                "gross_pnl": str(p.gross_pnl),
                "net_pnl": str(p.net_pnl),
                "total_charges": str(p.total_charges),
            }
            for p in periods
        ],
        "summary": {k: str(v) if isinstance(v, Decimal) else v for k, v in summary.items()},
        "charge_coverage": {
            "closed_trades": len(enriched),
            "reconciled_trades": reconciled,
            "coverage_pct": (reconciled * 100 / len(enriched)) if enriched else 0,
        },
    }


async def claim_chart_artifact(
    db: AsyncSession,
    *,
    claimer_id: str,
) -> dict | None:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=LEASE_MINUTES)
    result = await db.execute(
        text(
            """
            UPDATE journal_chart_artifacts
            SET
                status = 'claimed',
                claimed_by = :claimer_id,
                claimed_at = now(),
                lease_expires_at = :expires_at,
                capture_attempts = capture_attempts + 1
            WHERE id = (
                SELECT a.id
                FROM journal_chart_artifacts a
                JOIN journal_entries j ON j.id = a.journal_entry_id
                WHERE a.status IN ('pending', 'claimed')
                  AND (
                        a.status = 'pending'
                        OR a.lease_expires_at < now()
                  )
                ORDER BY a.created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING
                id,
                journal_entry_id,
                chart_source,
                capture_attempts
            """
        ),
        {"claimer_id": claimer_id, "expires_at": expires_at},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def upload_chart_artifact(
    db: AsyncSession,
    *,
    artifact_id: UUID,
    claimer_id: str,
    png_bytes: bytes,
) -> dict:
    if len(png_bytes) > PNG_MAX_BYTES:
        raise JournalConflictError("PNG exceeds 5 MiB maximum.")
    if not png_bytes.startswith(PNG_SIGNATURE):
        raise JournalConflictError("Invalid PNG signature.")

    content_hash = hashlib.sha256(png_bytes).hexdigest()
    result = await db.execute(
        text(
            """
            UPDATE journal_chart_artifacts
            SET
                status = 'captured',
                png_bytes = :png_bytes,
                content_hash = :content_hash,
                last_error = NULL,
                lease_expires_at = NULL
            WHERE id = :id
              AND claimed_by = :claimer_id
              AND status = 'claimed'
            RETURNING id, journal_entry_id, content_hash
            """
        ),
        {
            "id": artifact_id,
            "claimer_id": claimer_id,
            "png_bytes": png_bytes,
            "content_hash": content_hash,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ArtifactNotFoundError("Artifact claim not found or lease expired.")
    return dict(row)


async def get_chart_artifact_png(db: AsyncSession, journal_id: UUID) -> bytes | None:
    result = await db.execute(
        text(
            """
            SELECT png_bytes
            FROM journal_chart_artifacts
            WHERE journal_entry_id = :journal_id
              AND status = 'captured'
            """
        ),
        {"journal_id": journal_id},
    )
    return result.scalar_one_or_none()


async def enqueue_ai_coach_run(db: AsyncSession, filters: dict) -> UUID:
    return await create_ai_run(db, filters)


async def get_ai_run(db: AsyncSession, run_id: UUID) -> dict:
    result = await db.execute(
        text(
            """
            SELECT
                id,
                status,
                filters,
                input_hash,
                result,
                model,
                request_id,
                usage,
                error_message,
                created_at,
                completed_at
            FROM journal_ai_runs
            WHERE id = :id
            """
        ),
        {"id": run_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise JournalNotFoundError("AI run not found.")
    return dict(row)


async def list_ai_runs(db: AsyncSession, *, limit: int = 20) -> list[dict]:
    result = await db.execute(
        text(
            """
            SELECT
                id,
                status,
                filters,
                input_hash,
                model,
                created_at,
                completed_at,
                error_message
            FROM journal_ai_runs
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [dict(row) for row in result.mappings().all()]
