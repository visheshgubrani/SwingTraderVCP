"""Process journal fill outbox events asynchronously."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.domain.journal_aggregation import (
    INTENT_TO_EXIT_REASON,
    aggregate_exit_outcome,
    compute_gross_pnl,
    compute_r_multiple,
    hold_duration_hours,
    weighted_average_price,
)
from app.domain.journal_charges import FillLeg, charges_to_dict, estimate_cnc_charges
from app.domain.market_regime import (
    BENCHMARK_SYMBOL,
    CLASSIFIER_VERSION,
    classify_regime,
    is_stale_reference,
)
from app.services.historical_fetcher import latest_completed_eod_date

logger = logging.getLogger(__name__)

CANDLE_LIMIT = 300
MAX_BATCH = 20
MAX_ATTEMPTS = 5


async def run_journal_dispatcher(ctx) -> dict[str, Any]:
    """arq job: claim and process pending journal outbox events."""
    processed = 0
    failed = 0
    async with async_session() as db:
        events = await _claim_pending_events(db, limit=MAX_BATCH)
        await db.commit()

    for event in events:
        async with async_session() as db:
            try:
                await _process_outbox_event(db, event)
                await _mark_event_completed(db, event["id"])
                await db.commit()
                processed += 1
            except Exception as exc:
                await db.rollback()
                async with async_session() as err_db:
                    await _mark_event_failed(err_db, event["id"], str(exc))
                    await err_db.commit()
                failed += 1
                logger.exception("Journal outbox processing failed for %s", event["id"])

    if processed or failed:
        async with async_session() as db:
            remaining = await _count_pending(db)
            await db.commit()
        if remaining:
            await ctx["redis"].enqueue_job("run_journal_dispatcher")

    return {"processed": processed, "failed": failed}


async def _count_pending(db: AsyncSession) -> int:
    result = await db.execute(
        text(
            """
            SELECT COUNT(*)::integer
            FROM journal_fill_outbox
            WHERE status IN ('pending', 'processing')
              AND attempts < :max_attempts
            """
        ),
        {"max_attempts": MAX_ATTEMPTS},
    )
    return int(result.scalar_one())


async def _claim_pending_events(db: AsyncSession, *, limit: int) -> list[dict]:
    result = await db.execute(
        text(
            """
            UPDATE journal_fill_outbox
            SET
                status = 'processing',
                attempts = attempts + 1
            WHERE id IN (
                SELECT id
                FROM journal_fill_outbox
                WHERE status = 'pending'
                  AND attempts < :max_attempts
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            RETURNING id, order_fill_id, position_id, fill_side
            """
        ),
        {"limit": limit, "max_attempts": MAX_ATTEMPTS},
    )
    return [dict(row) for row in result.mappings().all()]


async def _mark_event_completed(db: AsyncSession, event_id: UUID) -> None:
    await db.execute(
        text(
            """
            UPDATE journal_fill_outbox
            SET status = 'completed', processed_at = now(), last_error = NULL
            WHERE id = :id
            """
        ),
        {"id": event_id},
    )


async def _mark_event_failed(db: AsyncSession, event_id: UUID, error: str) -> None:
    await db.execute(
        text(
            """
            UPDATE journal_fill_outbox
            SET
                status = CASE
                    WHEN attempts >= :max_attempts THEN 'failed'
                    ELSE 'pending'
                END,
                last_error = :error
            WHERE id = :id
            """
        ),
        {"id": event_id, "error": error[:2000], "max_attempts": MAX_ATTEMPTS},
    )


async def _process_outbox_event(db: AsyncSession, event: dict) -> None:
    fill = await _load_fill_context(db, event["order_fill_id"])
    if fill is None:
        raise RuntimeError("Fill context not found.")

    journal_id = await _get_journal_id(db, event["position_id"])
    if event["fill_side"] == "entry":
        if journal_id is None:
            await _create_journal_on_first_entry(db, fill)
        else:
            await _update_entry_aggregates(db, journal_id, event["position_id"])
        return

    if journal_id is None:
        raise RuntimeError("Exit fill received before journal entry was created.")
    await _append_exit_fill(db, journal_id, fill)
    position = await _load_position(db, event["position_id"])
    if position and position["state"] == "closed":
        await _finalize_journal_closure(db, journal_id, position)


async def _load_fill_context(db: AsyncSession, order_fill_id: UUID) -> dict | None:
    result = await db.execute(
        text(
            """
            SELECT
                f.id AS order_fill_id,
                f.order_intent_id,
                f.filled_at,
                f.quantity,
                f.price,
                o.intent_type,
                o.side,
                o.execution_mode,
                o.position_id,
                o.trade_instruction_id,
                p.instrument_id,
                p.side AS position_side,
                p.state AS position_state,
                p.screening_result_id,
                i.fyers_symbol AS symbol
            FROM order_fills f
            JOIN order_intents o ON o.id = f.order_intent_id
            JOIN positions p ON p.id = o.position_id
            JOIN instruments i ON i.id = p.instrument_id
            WHERE f.id = :order_fill_id
            """
        ),
        {"order_fill_id": order_fill_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def _get_journal_id(db: AsyncSession, position_id: UUID) -> UUID | None:
    result = await db.execute(
        text("SELECT id FROM journal_entries WHERE position_id = :position_id"),
        {"position_id": position_id},
    )
    row = result.scalar_one_or_none()
    return row


async def _load_position(db: AsyncSession, position_id: UUID) -> dict | None:
    result = await db.execute(
        text(
            """
            SELECT
                id,
                state,
                side,
                quantity,
                open_quantity,
                average_entry_price,
                current_stop_loss,
                current_target,
                trailing_rule,
                realized_pnl,
                opened_at,
                closed_at,
                trade_instruction_id,
                screening_result_id,
                instrument_id
            FROM positions
            WHERE id = :position_id
            """
        ),
        {"position_id": position_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def _create_journal_on_first_entry(db: AsyncSession, fill: dict) -> None:
    snapshot = await _build_entry_snapshot(db, fill)
    regime_id = await _capture_market_regime(db, fill["filled_at"])
    candles = await _load_chart_candles(db, fill["instrument_id"])

    journal_id = uuid4()
    await db.execute(
        text(
            """
            INSERT INTO journal_entries (
                id,
                position_id,
                instrument_id,
                execution_mode,
                status,
                symbol,
                entry_frozen_at,
                first_entry_fill_at,
                first_entry_price,
                first_entry_quantity,
                final_entry_quantity,
                weighted_entry_price,
                entry_snapshot,
                market_regime_snapshot_id,
                risk_amount
            )
            VALUES (
                :id,
                :position_id,
                :instrument_id,
                :execution_mode,
                'open',
                :symbol,
                :filled_at,
                :filled_at,
                :price,
                :quantity,
                :quantity,
                :price,
                CAST(:entry_snapshot AS jsonb),
                :regime_id,
                :risk_amount
            )
            """
        ),
        {
            "id": journal_id,
            "position_id": fill["position_id"],
            "instrument_id": fill["instrument_id"],
            "execution_mode": fill["execution_mode"],
            "symbol": fill["symbol"],
            "filled_at": fill["filled_at"],
            "price": fill["price"],
            "quantity": fill["quantity"],
            "entry_snapshot": json.dumps(snapshot),
            "regime_id": regime_id,
            "risk_amount": snapshot.get("risk_amount"),
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO journal_chart_artifacts (
                id,
                journal_entry_id,
                status,
                chart_source
            )
            VALUES (
                :id,
                :journal_entry_id,
                'pending',
                CAST(:chart_source AS jsonb)
            )
            ON CONFLICT (journal_entry_id) DO NOTHING
            """
        ),
        {
            "id": uuid4(),
            "journal_entry_id": journal_id,
            "chart_source": json.dumps(
                {
                    "symbol": fill["symbol"],
                    "candles": candles,
                    "entry_price": str(fill["price"]),
                    "stop_loss": snapshot.get("initial_stop_loss"),
                    "target": snapshot.get("initial_target"),
                }
            ),
        },
    )


async def _update_entry_aggregates(
    db: AsyncSession,
    journal_id: UUID,
    position_id: UUID,
) -> None:
    fills = await _load_entry_fills(db, position_id)
    avg = weighted_average_price([(f["quantity"], Decimal(str(f["price"]))) for f in fills])
    total_qty = sum(f["quantity"] for f in fills)
    await db.execute(
        text(
            """
            UPDATE journal_entries
            SET
                final_entry_quantity = :total_qty,
                weighted_entry_price = :avg_price
            WHERE id = :journal_id
            """
        ),
        {"journal_id": journal_id, "total_qty": total_qty, "avg_price": avg},
    )


async def _append_exit_fill(db: AsyncSession, journal_id: UUID, fill: dict) -> None:
    reason = INTENT_TO_EXIT_REASON.get(fill["intent_type"], "manual")
    exit_record = {
        "order_fill_id": str(fill["order_fill_id"]),
        "order_intent_id": str(fill["order_intent_id"]),
        "intent_type": fill["intent_type"],
        "quantity": fill["quantity"],
        "price": str(fill["price"]),
        "filled_at": fill["filled_at"].isoformat(),
        "exit_reason": reason,
    }
    await db.execute(
        text(
            """
            UPDATE journal_entries
            SET
                exit_fills = exit_fills || CAST(:exit_record AS jsonb),
                exit_reasons = (
                    SELECT COALESCE(jsonb_agg(DISTINCT value), '[]'::jsonb)
                    FROM jsonb_array_elements_text(
                        exit_reasons || CAST(:reason_array AS jsonb)
                    ) AS value
                )
            WHERE id = :journal_id
            """
        ),
        {
            "journal_id": journal_id,
            "exit_record": json.dumps([exit_record]),
            "reason_array": json.dumps([reason]),
        },
    )


async def _finalize_journal_closure(db: AsyncSession, journal_id: UUID, position: dict) -> None:
    entry_fills = await _load_entry_fills(db, position["id"])
    exit_fills = await _load_exit_fills(db, position["id"])
    entry_pairs = [(f["quantity"], Decimal(str(f["price"]))) for f in entry_fills]
    exit_pairs = [(f["quantity"], Decimal(str(f["price"]))) for f in exit_fills]
    exit_avg = weighted_average_price(exit_pairs)
    gross_pnl = compute_gross_pnl(
        side=position["side"],
        entry_fills=entry_pairs,
        exit_fills=exit_pairs,
    )

    all_fills: list[FillLeg] = []
    for f in entry_fills:
        all_fills.append(
            FillLeg(side="buy" if position["side"] == "long" else "sell", quantity=f["quantity"], price=Decimal(str(f["price"])))
        )
    for f in exit_fills:
        all_fills.append(
            FillLeg(side="sell" if position["side"] == "long" else "buy", quantity=f["quantity"], price=Decimal(str(f["price"])))
        )
    charge_breakdown = estimate_cnc_charges(all_fills)
    charges_dict = charges_to_dict(charge_breakdown)
    net_pnl = gross_pnl - charge_breakdown.total if gross_pnl is not None else None

    journal_row = await db.execute(
        text("SELECT risk_amount, exit_reasons FROM journal_entries WHERE id = :id"),
        {"id": journal_id},
    )
    journal = journal_row.mappings().one()
    risk_amount = (
        Decimal(str(journal["risk_amount"])) if journal["risk_amount"] is not None else None
    )
    gross_r = compute_r_multiple(gross_or_net_pnl=gross_pnl, risk_amount=risk_amount) if gross_pnl else None
    net_r = compute_r_multiple(gross_or_net_pnl=net_pnl, risk_amount=risk_amount) if net_pnl else None

    reasons_raw = journal["exit_reasons"] or []
    if isinstance(reasons_raw, str):
        reasons_raw = json.loads(reasons_raw)
    exit_outcome = aggregate_exit_outcome(reasons_raw)

    realized = Decimal(str(position["realized_pnl"]))
    pnl_mismatch = False
    pnl_delta = None
    if gross_pnl is not None and realized != gross_pnl:
        pnl_mismatch = True
        pnl_delta = realized - gross_pnl

    duration = hold_duration_hours(position["opened_at"], position["closed_at"])

    await db.execute(
        text(
            """
            UPDATE journal_entries
            SET
                status = 'closed',
                weighted_exit_price = :exit_avg,
                closed_at = :closed_at,
                hold_duration_hours = :hold_hours,
                exit_outcome = :exit_outcome,
                gross_pnl = :gross_pnl,
                estimated_charges = CAST(:estimated_charges AS jsonb),
                net_pnl = :net_pnl,
                gross_r_multiple = :gross_r,
                net_r_multiple = :net_r,
                pnl_mismatch = :pnl_mismatch,
                pnl_mismatch_delta = :pnl_delta
            WHERE id = :journal_id
            """
        ),
        {
            "journal_id": journal_id,
            "exit_avg": exit_avg,
            "closed_at": position["closed_at"],
            "hold_hours": duration,
            "exit_outcome": exit_outcome,
            "gross_pnl": gross_pnl,
            "estimated_charges": json.dumps(charges_dict),
            "net_pnl": net_pnl,
            "gross_r": gross_r,
            "net_r": net_r,
            "pnl_mismatch": pnl_mismatch,
            "pnl_delta": pnl_delta,
        },
    )


async def _load_entry_fills(db: AsyncSession, position_id: UUID) -> list[dict]:
    result = await db.execute(
        text(
            """
            SELECT f.quantity, f.price, f.filled_at, f.id AS order_fill_id
            FROM order_fills f
            JOIN order_intents o ON o.id = f.order_intent_id
            WHERE o.position_id = :position_id
              AND o.intent_type = 'entry'
            ORDER BY f.filled_at ASC, f.id ASC
            """
        ),
        {"position_id": position_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def _load_exit_fills(db: AsyncSession, position_id: UUID) -> list[dict]:
    result = await db.execute(
        text(
            """
            SELECT f.quantity, f.price, f.filled_at, o.intent_type
            FROM order_fills f
            JOIN order_intents o ON o.id = f.order_intent_id
            WHERE o.position_id = :position_id
              AND o.intent_type IN (
                    'stop_loss_exit',
                    'target_exit',
                    'trailing_exit',
                    'manual_exit'
              )
            ORDER BY f.filled_at ASC, f.id ASC
            """
        ),
        {"position_id": position_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def _build_entry_snapshot(db: AsyncSession, fill: dict) -> dict:
    instruction = None
    if fill.get("trade_instruction_id"):
        result = await db.execute(
            text(
                """
                SELECT
                    side,
                    quantity,
                    product_type,
                    entry_order_type,
                    planned_entry_price,
                    entry_limit_price,
                    initial_stop_loss,
                    initial_target,
                    trailing_rule,
                    risk_amount,
                    notes,
                    manual_confirmed_at
                FROM trade_instructions
                WHERE id = :id
                """
            ),
            {"id": fill["trade_instruction_id"]},
        )
        instruction = result.mappings().one_or_none()

    screening = None
    scan_run = None
    if fill.get("screening_result_id"):
        result = await db.execute(
            text(
                """
                SELECT
                    sr.result_rank,
                    sr.technical_passed,
                    sr.vcp_detected,
                    sr.close_price,
                    sr.sma_50,
                    sr.sma_200,
                    sr.avg_volume_20,
                    sr.pct_from_52w_high,
                    sr.technical_metrics,
                    sr.llm_status,
                    sr.llm_verdict,
                    sr.llm_flags,
                    sr.fundamental_status,
                    sr.fundamental_verdict,
                    sr.fundamental_scorecard,
                    sr.reviewer_status,
                    s.universe_code,
                    s.technical_config,
                    s.llm_config
                FROM screening_results sr
                JOIN scan_runs s ON s.id = sr.scan_run_id
                WHERE sr.id = :id
                """
            ),
            {"id": fill["screening_result_id"]},
        )
        row = result.mappings().one_or_none()
        if row:
            screening = dict(row)
            scan_run = {
                "universe_code": row["universe_code"],
                "technical_config": row["technical_config"],
                "llm_config": row["llm_config"],
            }

    position = await _load_position(db, fill["position_id"])
    risk_amount = None
    if instruction and instruction["risk_amount"] is not None:
        risk_amount = str(instruction["risk_amount"])

    return {
        "trade_instruction": dict(instruction) if instruction else None,
        "screening_result": screening,
        "scan_run": scan_run,
        "position_side": position["side"] if position else None,
        "initial_stop_loss": (
            str(instruction["initial_stop_loss"]) if instruction else None
        ),
        "initial_target": (
            str(instruction["initial_target"]) if instruction and instruction["initial_target"] else None
        ),
        "trailing_rule": instruction["trailing_rule"] if instruction else {},
        "risk_amount": risk_amount,
        "planned_entry_price": (
            str(instruction["planned_entry_price"]) if instruction else None
        ),
        "frozen_at": fill["filled_at"].isoformat(),
    }


async def _load_chart_candles(db: AsyncSession, instrument_id: UUID) -> list[dict]:
    result = await db.execute(
        text(
            """
            SELECT
                EXTRACT(EPOCH FROM candle_start)::bigint AS time,
                open_price AS open,
                high_price AS high,
                low_price AS low,
                close_price AS close,
                volume
            FROM market_candles
            WHERE instrument_id = :instrument_id
              AND timeframe = '1d'
            ORDER BY candle_start DESC
            LIMIT :limit
            """
        ),
        {"instrument_id": instrument_id, "limit": CANDLE_LIMIT},
    )
    rows = [dict(row) for row in result.mappings().all()]
    rows.reverse()
    for row in rows:
        for key in ("open", "high", "low", "close"):
            row[key] = float(row[key])
        row["volume"] = int(row["volume"])
    return rows


async def _capture_market_regime(db: AsyncSession, as_of: datetime) -> UUID | None:
    benchmark = await db.execute(
        text(
            """
            SELECT id FROM instruments
            WHERE fyers_symbol = :symbol AND active = true
            LIMIT 1
            """
        ),
        {"symbol": BENCHMARK_SYMBOL},
    )
    benchmark_id = benchmark.scalar_one_or_none()
    if benchmark_id is None:
        return await _insert_regime_snapshot(
            db,
            regime="unavailable",
            evidence={"reason": "benchmark_not_registered"},
            reference_date=None,
        )

    candles = await db.execute(
        text(
            """
            SELECT close_price, candle_start
            FROM market_candles
            WHERE instrument_id = :instrument_id
              AND timeframe = '1d'
            ORDER BY candle_start DESC
            LIMIT 220
            """
        ),
        {"instrument_id": benchmark_id},
    )
    candle_rows = candles.mappings().all()
    if len(candle_rows) < 200:
        return await _insert_regime_snapshot(
            db,
            regime="unavailable",
            evidence={"reason": "insufficient_benchmark_candles", "count": len(candle_rows)},
            reference_date=None,
        )

    closes = [Decimal(str(c["close_price"])) for c in reversed(candle_rows)]
    reference_date = candle_rows[0]["candle_start"].date()
    benchmark_price = closes[-1]
    sma_50 = sum(closes[-50:]) / Decimal("50")
    sma_200 = sum(closes[-200:]) / Decimal("200")
    sma_50_20d_ago = sum(closes[-70:-20]) / Decimal("50") if len(closes) >= 70 else None
    slope = sma_50 - sma_50_20d_ago if sma_50_20d_ago is not None else None

    breadth = await _compute_breadth(db, reference_date)
    stale = is_stale_reference(reference_date, latest_completed_eod_date())

    regime, evidence = classify_regime(
        benchmark_price=benchmark_price,
        sma_50=sma_50,
        sma_200=sma_200,
        sma_50_slope_20d=slope,
        constituents_above_sma_50=breadth["above_50"],
        constituents_total=breadth["total"],
        stale=stale,
        insufficient_data=breadth["total"] == 0,
    )
    evidence_dict = {
        "benchmark_price": str(benchmark_price),
        "sma_50": str(sma_50),
        "sma_200": str(sma_200),
        "sma_50_slope_20d": str(slope) if slope is not None else None,
        "breadth_above_sma_50_pct": str(evidence.breadth_above_sma_50_pct),
        "stale": stale,
    }
    return await _insert_regime_snapshot(
        db,
        regime=regime,
        evidence=evidence_dict,
        reference_date=reference_date,
        benchmark_price=benchmark_price,
        sma_50=sma_50,
        sma_200=sma_200,
        slope=slope,
        breadth_50=evidence.breadth_above_sma_50_pct,
    )


async def _compute_breadth(db: AsyncSession, reference_date: date) -> dict:
    result = await db.execute(
        text(
            """
            WITH constituents AS (
                SELECT i.id AS instrument_id
                FROM instruments i
                JOIN universe_memberships m ON m.instrument_id = i.id
                WHERE m.universe_code = 'NIFTY500'
                  AND m.member_to IS NULL
                  AND i.active = true
            ),
            latest_closes AS (
                SELECT DISTINCT ON (c.instrument_id)
                    c.instrument_id,
                    c.close_price,
                    c.candle_start
                FROM market_candles c
                JOIN constituents ct ON ct.instrument_id = c.instrument_id
                WHERE c.timeframe = '1d'
                  AND c.candle_start::date <= :reference_date
                ORDER BY c.instrument_id, c.candle_start DESC
            ),
            sma50 AS (
                SELECT
                    c.instrument_id,
                    AVG(c.close_price) AS sma_50
                FROM market_candles c
                JOIN latest_closes lc ON lc.instrument_id = c.instrument_id
                WHERE c.timeframe = '1d'
                  AND c.candle_start <= lc.candle_start
                  AND c.candle_start > lc.candle_start - INTERVAL '80 days'
                GROUP BY c.instrument_id
                HAVING COUNT(*) >= 50
            )
            SELECT
                COUNT(*)::integer AS total,
                COUNT(*) FILTER (
                    WHERE lc.close_price > s.sma_50
                )::integer AS above_50
            FROM latest_closes lc
            JOIN sma50 s ON s.instrument_id = lc.instrument_id
            """
        ),
        {"reference_date": reference_date},
    )
    row = result.mappings().one()
    return {"total": row["total"] or 0, "above_50": row["above_50"] or 0}


async def _insert_regime_snapshot(
    db: AsyncSession,
    *,
    regime: str,
    evidence: dict,
    reference_date: date | None,
    benchmark_price: Decimal | None = None,
    sma_50: Decimal | None = None,
    sma_200: Decimal | None = None,
    slope: Decimal | None = None,
    breadth_50: Decimal | None = None,
) -> UUID:
    regime_id = uuid4()
    await db.execute(
        text(
            """
            INSERT INTO market_regime_snapshots (
                id,
                reference_eod_date,
                classifier_version,
                regime,
                benchmark_symbol,
                benchmark_price,
                benchmark_price_source,
                benchmark_price_at,
                sma_50,
                sma_200,
                sma_50_slope_20d,
                breadth_above_sma_50_pct,
                evidence
            )
            VALUES (
                :id,
                COALESCE(:reference_date, CURRENT_DATE),
                :classifier_version,
                :regime,
                :benchmark_symbol,
                :benchmark_price,
                'eod_close',
                now(),
                :sma_50,
                :sma_200,
                :slope,
                :breadth_50,
                CAST(:evidence AS jsonb)
            )
            """
        ),
        {
            "id": regime_id,
            "reference_date": reference_date,
            "classifier_version": CLASSIFIER_VERSION,
            "regime": regime,
            "benchmark_symbol": BENCHMARK_SYMBOL,
            "benchmark_price": benchmark_price,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "slope": slope,
            "breadth_50": breadth_50,
            "evidence": json.dumps(evidence),
        },
    )
    return regime_id
