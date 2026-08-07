"""Journal AI coach orchestration and deterministic metrics."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.domain.journal_aggregation import compute_summary_metrics
from app.services.fundamental_data import canonical_json_hash
from app.services.journal_llm import JournalLLMError, OpenRouterJournalCoachClient

logger = logging.getLogger(__name__)

BATCH_SIZE = 25
PROMPT_VERSION = "journal_coach_v1"


async def run_journal_ai_coach(ctx, run_id: str) -> dict[str, Any]:
    """arq job: build deterministic metrics and synthesize coach report."""
    async with async_session() as db:
        run = await _load_run(db, UUID(run_id))
        if run is None:
            return {"status": "not_found"}
        if run["status"] not in {"queued", "running"}:
            return {"status": "skipped", "reason": run["status"]}

        await _update_run_status(db, UUID(run_id), "running")
        await db.commit()

    try:
        async with async_session() as db:
            trades = await _load_filtered_trades(db, run["filters"])
            metrics = _build_deterministic_metrics(trades)
            coach_input = {
                "prompt_version": PROMPT_VERSION,
                "filters": run["filters"],
                "summary": metrics["summary"],
                "batches": metrics["batches"],
                "setup_breakdown": metrics["setup_breakdown"],
                "regime_breakdown": metrics["regime_breakdown"],
                "review_coverage": metrics["review_coverage"],
            }
            input_hash = canonical_json_hash(coach_input)

            cached = await _find_cached_run(db, input_hash)
            if cached and cached["id"] != UUID(run_id):
                await _complete_run(
                    db,
                    UUID(run_id),
                    result=cached["result"],
                    request_id=cached.get("request_id"),
                    usage=cached.get("usage") or {},
                    input_hash=input_hash,
                )
                await db.commit()
                return {"status": "reused", "input_hash": input_hash}

            if not settings.openrouter_api_key:
                raise JournalLLMError("OpenRouter API key is not configured.")

            client = OpenRouterJournalCoachClient(
                api_key=settings.openrouter_api_key,
                api_url=settings.openrouter_api_url,
                model=settings.openrouter_model,
                prompt_version=PROMPT_VERSION,
                app_title=settings.openrouter_app_title,
                http_referer=settings.openrouter_http_referer,
                max_tokens=settings.openrouter_max_tokens,
                temperature=settings.openrouter_temperature,
            )
            llm_result = await client.analyze(coach_input)
            result_payload = llm_result.report.model_dump()
            await _complete_run(
                db,
                UUID(run_id),
                result=result_payload,
                request_id=llm_result.request_id,
                usage=llm_result.usage,
                input_hash=input_hash,
            )
            await db.commit()
            return {"status": "succeeded", "input_hash": input_hash}
    except Exception as exc:
        async with async_session() as db:
            await _fail_run(db, UUID(run_id), str(exc))
            await db.commit()
        logger.exception("Journal AI coach failed for run %s", run_id)
        return {"status": "failed", "error": str(exc)}


async def _load_run(db: AsyncSession, run_id: UUID) -> dict | None:
    result = await db.execute(
        text(
            """
            SELECT id, status, filters, input_hash, result, request_id, usage
            FROM journal_ai_runs
            WHERE id = :id
            """
        ),
        {"id": run_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def _update_run_status(db: AsyncSession, run_id: UUID, status: str) -> None:
    await db.execute(
        text("UPDATE journal_ai_runs SET status = :status WHERE id = :id"),
        {"id": run_id, "status": status},
    )


async def _complete_run(
    db: AsyncSession,
    run_id: UUID,
    *,
    result: dict,
    request_id: str | None,
    usage: dict,
    input_hash: str,
) -> None:
    await db.execute(
        text(
            """
            UPDATE journal_ai_runs
            SET
                status = 'succeeded',
                result = CAST(:result AS jsonb),
                request_id = :request_id,
                usage = CAST(:usage AS jsonb),
                input_hash = :input_hash,
                completed_at = now(),
                error_message = NULL
            WHERE id = :id
            """
        ),
        {
            "id": run_id,
            "result": json.dumps(result),
            "request_id": request_id,
            "usage": json.dumps(usage),
            "input_hash": input_hash,
        },
    )


async def _fail_run(db: AsyncSession, run_id: UUID, error: str) -> None:
    await db.execute(
        text(
            """
            UPDATE journal_ai_runs
            SET
                status = 'failed',
                error_message = :error,
                completed_at = now()
            WHERE id = :id
            """
        ),
        {"id": run_id, "error": error[:2000]},
    )


async def _find_cached_run(db: AsyncSession, input_hash: str) -> dict | None:
    result = await db.execute(
        text(
            """
            SELECT id, result, request_id, usage
            FROM journal_ai_runs
            WHERE input_hash = :input_hash
              AND status = 'succeeded'
            ORDER BY completed_at DESC NULLS LAST
            LIMIT 1
            """
        ),
        {"input_hash": input_hash},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def _load_filtered_trades(db: AsyncSession, filters: dict) -> list[dict]:
    clauses = ["j.status = 'closed'"]
    params: dict[str, Any] = {"limit": filters.get("limit", 500)}

    if filters.get("execution_mode"):
        clauses.append("j.execution_mode = :execution_mode")
        params["execution_mode"] = filters["execution_mode"]
    if filters.get("symbol"):
        clauses.append("j.symbol ILIKE :symbol")
        params["symbol"] = f"%{filters['symbol']}%"
    if filters.get("setup_tag"):
        clauses.append("j.setup_tags ? :setup_tag")
        params["setup_tag"] = filters["setup_tag"]
    if filters.get("regime"):
        clauses.append("m.regime = :regime")
        params["regime"] = filters["regime"]
    if filters.get("exit_outcome"):
        clauses.append("j.exit_outcome = :exit_outcome")
        params["exit_outcome"] = filters["exit_outcome"]
    if filters.get("date_from"):
        clauses.append("j.closed_at >= :date_from")
        params["date_from"] = filters["date_from"]
    if filters.get("date_to"):
        clauses.append("j.closed_at < :date_to")
        params["date_to"] = filters["date_to"]

    where_sql = " AND ".join(clauses)
    result = await db.execute(
        text(
            f"""
            SELECT
                j.id,
                j.symbol,
                j.execution_mode,
                j.closed_at,
                j.gross_pnl,
                j.net_pnl,
                j.gross_r_multiple,
                j.net_r_multiple,
                j.hold_duration_hours,
                j.exit_outcome,
                j.setup_tags,
                j.mistake_tags,
                j.emotion_tags,
                j.execution_rating,
                j.notes,
                j.lessons,
                j.estimated_charges,
                j.actual_charges,
                j.charge_quality,
                j.pnl_mismatch,
                m.regime
            FROM journal_entries j
            LEFT JOIN market_regime_snapshots m
                ON m.id = j.market_regime_snapshot_id
            WHERE {where_sql}
            ORDER BY j.closed_at DESC
            LIMIT :limit
            """
        ),
        params,
    )
    trades = []
    for row in result.mappings().all():
        trade = dict(row)
        charges = trade.get("actual_charges") or trade.get("estimated_charges") or {}
        if isinstance(charges, str):
            charges = json.loads(charges)
        trade["total_charges"] = charges.get("total", "0")
        trades.append(trade)
    return trades


def _build_deterministic_metrics(trades: list[dict]) -> dict[str, Any]:
    summary = compute_summary_metrics(trades)
    batches = [
        _trade_batch_payload(trades[i : i + BATCH_SIZE], batch_index)
        for batch_index, i in enumerate(range(0, len(trades), BATCH_SIZE))
    ]
    setup_breakdown = _cohort_breakdown(trades, field="setup_tags")
    regime_breakdown = _cohort_breakdown(trades, field="regime")
    reviewed = sum(1 for t in trades if t.get("execution_rating") or t.get("notes"))
    return {
        "summary": _serialize_summary(summary),
        "batches": batches,
        "setup_breakdown": setup_breakdown,
        "regime_breakdown": regime_breakdown,
        "review_coverage": {
            "total_trades": len(trades),
            "reviewed_trades": reviewed,
            "coverage_pct": (reviewed * 100 / len(trades)) if trades else 0,
        },
    }


def _trade_batch_payload(trades: list[dict], batch_index: int) -> dict:
    return {
        "batch_index": batch_index,
        "trade_count": len(trades),
        "trades": [
            {
                "id": str(t["id"]),
                "symbol": t["symbol"],
                "net_pnl": str(t.get("net_pnl") or 0),
                "net_r_multiple": str(t.get("net_r_multiple") or ""),
                "exit_outcome": t.get("exit_outcome"),
                "regime": t.get("regime"),
                "setup_tags": t.get("setup_tags") or [],
                "mistake_tags": t.get("mistake_tags") or [],
                "execution_rating": t.get("execution_rating"),
            }
            for t in trades
        ],
    }


def _cohort_breakdown(trades: list[dict], *, field: str) -> list[dict]:
    cohorts: dict[str, list[dict]] = {}
    for trade in trades:
        if field == "regime":
            keys = [trade.get("regime") or "unknown"]
        else:
            keys = trade.get(field) or ["untagged"]
            if not keys:
                keys = ["untagged"]
        for key in keys:
            cohorts.setdefault(str(key), []).append(trade)

    breakdown = []
    for cohort, items in cohorts.items():
        nets = [float(item.get("net_pnl") or 0) for item in items]
        wins = sum(1 for n in nets if n > 0)
        breakdown.append(
            {
                "cohort": cohort,
                "trade_count": len(items),
                "win_rate_pct": (wins * 100 / len(items)) if items else 0,
                "net_pnl": sum(nets),
                "trade_ids": [str(item["id"]) for item in items[:20]],
            }
        )
    return sorted(breakdown, key=lambda item: item["trade_count"], reverse=True)


def _serialize_summary(summary: dict) -> dict:
    serialized = {}
    for key, value in summary.items():
        if isinstance(value, Decimal):
            serialized[key] = str(value)
        else:
            serialized[key] = value
    return serialized


async def create_ai_run(db: AsyncSession, filters: dict) -> UUID:
    run_id = uuid4()
    placeholder_hash = canonical_json_hash({"filters": filters, "pending": True})
    await db.execute(
        text(
            """
            INSERT INTO journal_ai_runs (
                id,
                status,
                filters,
                input_hash,
                model
            )
            VALUES (
                :id,
                'queued',
                CAST(:filters AS jsonb),
                :input_hash,
                :model
            )
            """
        ),
        {
            "id": run_id,
            "filters": json.dumps(filters),
            "input_hash": placeholder_hash,
            "model": settings.openrouter_model,
        },
    )
    return run_id
