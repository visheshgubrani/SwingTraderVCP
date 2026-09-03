"""Dedicated serial P10 proposal worker.

This worker owns no broker/account context. It freezes scanner-selected EOD
OHLCV, renders deterministic charts, audits each provider attempt, and hands a
strict pattern read to deterministic proposal construction.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import signal
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Collection, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from arq import cron, run_worker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.domain.p10_geometry import (
    CandleData,
    compute_structural_facts,
    derive_chart_geometry,
    evaluate_structural_gates,
    format_candidate_summary,
    structural_facts_to_dict,
)
from app.domain.p10_sizing import TEMPLATE_CONFIG, EntryTemplate
from app.redis_pool import redis_settings_from_config, tune_arq_redis_pool
from app.services.distributed_lease import (
    acquire_distributed_lease,
    release_distributed_lease,
    renew_distributed_lease,
)
from app.services.p10_forming_watch import (
    FORMING_RECHECK_CAP,
    close_forming_watch,
    expire_stale_forming_watches,
    load_forming_rechecks,
    upsert_forming_watch,
)
from app.services.proposal_generator import (
    GEOMETRY_VERSION,
    PROMPT_VERSION,
    PROPOSAL_STRUCTURAL_FORMING,
    PROPOSAL_STRUCTURAL_INVALID,
    ProposalBuildResult,
    ProposalProviderError,
    SCHEMA_VERSION,
    structural_rejection_message,
    calculate_next_session_and_deadline,
    call_gemini_vision_for_proposal,
    compute_frozen_source_hash,
    generate_trade_proposal_from_analysis,
    proposal_prompt_hash,
)
from app.services.execution_engine import publish_tick_subscriptions
from app.services.proposal_renderer import render_proposal_charts


logger = logging.getLogger("proposal_worker")
IST_TZ = ZoneInfo("Asia/Kolkata")
CandidateOutcome = Literal[
    "generated", "rejected", "uncertain", "existing", "failed", "timed_out"
]
PROPOSAL_BATCH_HARD_CAP = 20
_LOCK_KEY = "proposal_worker:singleton"
_LOCK_TTL_SECONDS = 30
_LOCK_REFRESH_SECONDS = 10


@dataclass(frozen=True)
class ProposalPersistenceResult:
    proposal_id: str
    created: bool
    status: str


def proposal_batch_deadline(
    *,
    as_of_date: dt.date | None = None,
    now: dt.datetime | None = None,
    holidays: Collection[dt.date] = (),
    manual: bool = False,
    scan_completed_at: dt.datetime | None = None,
    budget_minutes: int | None = None,
) -> dt.datetime:
    """Return the hard stop for a proposal batch.

    Proposals for an EOD session D0 target the next trading session D1.
    The batch deadline is the approval deadline (09:00 IST on D1, before market opens).
    For manual runs on past dates where the D1 approval deadline has already passed,
    a 24-hour window from now is provided so operator inspection and tests can complete.
    """
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    if as_of_date is not None:
        _, approval_deadline = calculate_next_session_and_deadline(
            as_of_date,
            holidays=holidays,
        )
        if manual and current >= approval_deadline:
            return current + dt.timedelta(hours=24)
        return approval_deadline

    # Fallback if as_of_date was not provided
    if scan_completed_at is not None:
        if scan_completed_at.tzinfo is None:
            scan_completed_at = scan_completed_at.replace(tzinfo=dt.timezone.utc)
        started = current if manual else scan_completed_at
        return started + dt.timedelta(minutes=budget_minutes or 45)
    return current + dt.timedelta(hours=24)


def cap_proposal_batch_limit(limit: int, configured_limit: int) -> int:
    return min(limit, configured_limit, PROPOSAL_BATCH_HARD_CAP)


def _holiday_dates() -> set[dt.date]:
    try:
        return {dt.date.fromisoformat(value) for value in settings.nse_trading_holidays}
    except ValueError as exc:
        raise RuntimeError("NSE_TRADING_HOLIDAYS contains an invalid ISO date") from exc


async def _control_is_paused(session: AsyncSession, control_key: str) -> bool:
    result = await session.execute(
        text("SELECT enabled FROM system_controls WHERE control_key = :key"),
        {"key": control_key},
    )
    value = result.scalar_one_or_none()
    return True if value is None else bool(value)


async def expire_unapproved_proposals(session: AsyncSession) -> int:
    result = await session.execute(
        text(
            """
            UPDATE trade_proposals
            SET status = 'expired_unapproved', updated_at = now()
            WHERE status = 'pending_approval' AND approval_deadline <= now()
            RETURNING id
            """
        )
    )
    rows = result.all()
    await session.commit()
    return len(rows)


async def expire_unapproved_job(ctx: dict[str, Any]) -> int:
    del ctx
    async with async_session() as session:
        return await expire_unapproved_proposals(session)


async def finalize_interrupted_proposal_run(
    session: AsyncSession,
    *,
    automation_run_id: str,
    batch_deadline: dt.datetime,
    now: dt.datetime | None = None,
    reason: str = "The proposal worker stopped before the batch reached a terminal state.",
) -> bool:
    """Close an orphaned run and any in-flight attempt without retrying inference."""
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    if batch_deadline.tzinfo is None:
        batch_deadline = batch_deadline.replace(tzinfo=dt.timezone.utc)

    run_status = "timed_out" if current >= batch_deadline else "failed"
    attempt_status = "timed_out" if run_status == "timed_out" else "failed"
    error_code = (
        "proposal_batch_deadline_exceeded"
        if run_status == "timed_out"
        else "proposal_worker_interrupted"
    )
    message = (
        f"{reason} Batch deadline: {batch_deadline.isoformat()}. "
        "No inference request was retried automatically."
    )
    attempts = await session.execute(
        text(
            """
            UPDATE proposal_attempts
            SET status = :attempt_status,
                error_type = COALESCE(error_type, :error_code),
                error_message = COALESCE(error_message, :error_message),
                completed_at = COALESCE(completed_at, :now)
            WHERE automation_run_id = :run_id
              AND status = 'running'
            RETURNING id
            """
        ),
        {
            "run_id": automation_run_id,
            "attempt_status": attempt_status,
            "error_code": error_code,
            "error_message": message,
            "now": current,
        },
    )
    interrupted_attempts = len(attempts.all())
    result = await session.execute(
        text(
            """
            UPDATE automation_runs
            SET status = :status,
                candidates_processed = LEAST(
                    candidates_total,
                    candidates_processed + :interrupted_attempts
                ),
                proposals_failed = proposals_failed + :failed_attempts,
                error_code = :error_code,
                error_message = :error_message,
                completed_at = :now,
                updated_at = :now
            WHERE id = :run_id
              AND status = 'running'
            RETURNING id
            """
        ),
        {
            "run_id": automation_run_id,
            "status": run_status,
            "interrupted_attempts": interrupted_attempts,
            "failed_attempts": interrupted_attempts if run_status == "failed" else 0,
            "error_code": error_code,
            "error_message": message,
            "now": current,
        },
    )
    finalized = result.scalar_one_or_none() is not None
    await session.commit()
    return finalized


async def recover_interrupted_proposal_runs(session: AsyncSession) -> int:
    """Finalize runs left running by a previous proposal-worker process."""
    rows = (
        await session.execute(
            text(
                """
                SELECT id, batch_deadline
                FROM automation_runs
                WHERE status = 'running'
                ORDER BY started_at
                FOR UPDATE
                """
            )
        )
    ).mappings().all()
    recovered = 0
    now = dt.datetime.now(dt.timezone.utc)
    for row in rows:
        finalized = await finalize_interrupted_proposal_run(
            session,
            automation_run_id=str(row["id"]),
            batch_deadline=row["batch_deadline"],
            now=now,
            reason="The proposal worker restarted while this batch was active.",
        )
        recovered += int(finalized)
    return recovered


async def _load_frozen_candles(
    session: AsyncSession,
    *,
    instrument_id: str,
    as_of_date: dt.date,
) -> list[CandleData]:
    result = await session.execute(
        text(
            """
            SELECT open_price, high_price, low_price, close_price, volume,
                   (candle_start AT TIME ZONE 'Asia/Kolkata')::date AS candle_date
            FROM market_candles
            WHERE instrument_id = :instrument_id
              AND timeframe = '1d'
              AND (candle_start AT TIME ZONE 'Asia/Kolkata')::date <= :as_of_date
            ORDER BY candle_start DESC
            LIMIT 252
            """
        ),
        {"instrument_id": instrument_id, "as_of_date": as_of_date},
    )
    rows = list(reversed(result.mappings().all()))
    if len(rows) != 252 or rows[-1]["candle_date"] != as_of_date:
        raise ValueError(
            f"Frozen EOD input requires exactly 252 sessions ending {as_of_date}; "
            f"received {len(rows)}"
        )
    return [
        CandleData(
            open=float(row["open_price"]),
            high=float(row["high_price"]),
            low=float(row["low_price"]),
            close=float(row["close_price"]),
            volume=int(row["volume"]),
            date=row["candle_date"].isoformat(),
        )
        for row in rows
    ]


async def _insert_attempt(
    session: AsyncSession,
    *,
    automation_run_id: str,
    candidate: Any,
    attempt_number: int,
    source_hash: str,
    charts: Any,
    policy_version: int,
    candidate_summary: str,
) -> str:
    result = await session.execute(
        text(
            """
            INSERT INTO proposal_attempts (
                automation_run_id, screening_result_id, instrument_id, symbol,
                attempt_number, status, source_hash, renderer_version,
                prompt_version, schema_version, model, risk_policy_version,
                geometry_version,
                prompt_hash, input_hash,
                context_image_hash, detail_image_hash, context_image, detail_image
            ) VALUES (
                :automation_run_id, :screening_result_id, :instrument_id, :symbol,
                :attempt_number, 'running', :source_hash, :renderer_version,
                :prompt_version, :schema_version, :model, :risk_policy_version,
                :geometry_version,
                :prompt_hash, :input_hash,
                :context_image_hash, :detail_image_hash, :context_image, :detail_image
            )
            ON CONFLICT (automation_run_id, screening_result_id, attempt_number)
            DO UPDATE SET status = 'running', started_at = now(), completed_at = NULL,
                          error_type = NULL, error_message = NULL,
                          error_details = '{}'::jsonb
            RETURNING id
            """
        ),
        {
            "automation_run_id": automation_run_id,
            "screening_result_id": candidate.screening_result_id,
            "instrument_id": candidate.instrument_id,
            "symbol": candidate.symbol,
            "attempt_number": attempt_number,
            "source_hash": source_hash,
            "renderer_version": charts.renderer_version,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "model": settings.vcp_vision_model,
            "risk_policy_version": policy_version,
            "geometry_version": GEOMETRY_VERSION,
            "prompt_hash": proposal_prompt_hash(
                tick_size=Decimal(str(candidate.tick_size)),
                candidate_summary=candidate_summary,
            ),
            "input_hash": hashlib.sha256(
                json.dumps(
                    {
                        "source_hash": source_hash,
                        "context_image_hash": charts.context_hash,
                        "detail_image_hash": charts.detail_hash,
                        "renderer_version": charts.renderer_version,
                        "geometry_version": GEOMETRY_VERSION,
                        "prompt_version": PROMPT_VERSION,
                        "schema_version": SCHEMA_VERSION,
                        "model": settings.vcp_vision_model,
                        "risk_policy_version": policy_version,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "context_image_hash": charts.context_hash,
            "detail_image_hash": charts.detail_hash,
            "context_image": charts.context_png,
            "detail_image": charts.detail_png,
        },
    )
    attempt_id = str(result.scalar_one())
    await session.commit()
    return attempt_id


async def _finish_attempt(
    session: AsyncSession,
    *,
    attempt_id: str,
    status: str,
    output: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    cost: float = 0,
    request_id: str | None = None,
    error: BaseException | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    error_details: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            """
            UPDATE proposal_attempts
            SET status = :status, structured_output = CAST(:output AS jsonb),
                provider_usage = CAST(:usage AS jsonb), provider_cost = :cost,
                provider_request_id = :request_id,
                error_type = :error_type, error_message = :error_message,
                error_details = CAST(:error_details AS jsonb),
                completed_at = now()
            WHERE id = :attempt_id
            """
        ),
        {
            "attempt_id": attempt_id,
            "status": status,
            "output": json.dumps(output) if output is not None else None,
            "usage": json.dumps(usage or {}),
            "cost": Decimal(str(cost)),
            "request_id": request_id,
            "error_type": error_type or (type(error).__name__ if error else None),
            "error_details": json.dumps(error_details or {}),
            "error_message": (
                error_message[:1000]
                if error_message is not None
                else (str(error)[:1000] if error else None)
            ),
        },
    )
    await session.commit()


async def _persist_proposal(
    session: AsyncSession,
    *,
    automation_run_id: str,
    proposal: dict[str, Any],
    charts: Any,
    request_id: str | None,
    usage: dict[str, Any],
    cost: float,
    redis: Any = None,
    rollout_stage: str = "shadow",
) -> ProposalPersistenceResult:
    auto_arm = (
        settings.execution_mode == "paper"
        and settings.paper_auto_arm_proposals
        and rollout_stage == "paper"
        and proposal.get("live_eligible") is True
    )
    proposal_status = "approved" if auto_arm else proposal.get("status", "pending_approval")
    initial_leg_status = "armed" if auto_arm else "planned"
    result = await session.execute(
        text(
            """
            INSERT INTO trade_proposals (
                automation_run_id, screening_result_id, instrument_id, symbol, as_of_date,
                status, approval_deadline, entry_session_date, proposal_hash, source_hash,
                renderer_version, prompt_version, schema_version, model, confidence,
                geometry_version,
                entry_template, pivot_price, initial_stop, stop_distance_pct, chase_ceiling,
                t1, t2, t3, risk_policy_id, risk_policy_version, risk_budget_pct,
                approved_risk_budget_amount,
                leg_count, leg_risk_allocations, relative_volume_threshold,
                entry_trigger_policy_version,
                gemini_evidence, geometry, context_image_hash, detail_image_hash,
                context_image, detail_image, live_eligible, generated_at,
                provider_request_id, provider_usage, provider_cost
            ) VALUES (
                :automation_run_id, :screening_result_id, :instrument_id, :symbol, :as_of_date,
                :status, :approval_deadline, :entry_session_date, :proposal_hash, :source_hash,
                :renderer_version, :prompt_version, :schema_version, :model, :confidence,
                :geometry_version,
                :entry_template, :pivot_price, :initial_stop, :stop_distance_pct, :chase_ceiling,
                :t1, :t2, :t3, :risk_policy_id, :risk_policy_version, :risk_budget_pct,
                :approved_risk_budget_amount,
                :leg_count, CAST(:leg_risk_allocations AS jsonb), :relative_volume_threshold,
                :entry_trigger_policy_version,
                CAST(:gemini_evidence AS jsonb), CAST(:geometry AS jsonb),
                :context_image_hash, :detail_image_hash, :context_image, :detail_image,
                :live_eligible, :generated_at, :provider_request_id,
                CAST(:provider_usage AS jsonb), :provider_cost
            )
            ON CONFLICT (
                screening_result_id, source_hash, model, prompt_version,
                schema_version, renderer_version, geometry_version,
                risk_policy_version, entry_trigger_policy_version
            ) DO NOTHING
            RETURNING id
            """
        ),
        {
            **proposal,
            "status": proposal_status,
            "automation_run_id": automation_run_id,
            "leg_risk_allocations": json.dumps(proposal["leg_risk_allocations"]),
            "gemini_evidence": json.dumps(proposal["gemini_evidence"]),
            "geometry": json.dumps(proposal["geometry"]),
            "context_image": charts.context_png,
            "detail_image": charts.detail_png,
            "provider_request_id": request_id,
            "provider_usage": json.dumps(usage),
            "provider_cost": Decimal(str(cost)),
        },
    )
    proposal_id = result.scalar_one_or_none()
    if proposal_id is None:
        existing = (
            await session.execute(
                text(
                    """
                    SELECT id, status
                    FROM trade_proposals
                    WHERE screening_result_id = :screening_result_id
                      AND source_hash = :source_hash
                      AND model = :model
                      AND prompt_version = :prompt_version
                      AND schema_version = :schema_version
                      AND renderer_version = :renderer_version
                      AND geometry_version = :geometry_version
                      AND risk_policy_version = :risk_policy_version
                      AND entry_trigger_policy_version =
                          :entry_trigger_policy_version
                    """
                ),
                proposal,
            )
        ).mappings().one()
        return ProposalPersistenceResult(
            proposal_id=str(existing["id"]),
            created=False,
            status=str(existing["status"]),
        )

    if auto_arm:
        await session.execute(
            text(
                """
                INSERT INTO proposal_decisions (
                    proposal_id, decision, expected_proposal_hash, notes
                ) VALUES (
                    :proposal_id, 'approved', :expected_hash, 'Auto-armed (paper trading mode)'
                )
                """
            ),
            {
                "proposal_id": proposal_id,
                "expected_hash": proposal["proposal_hash"],
            },
        )

    template = EntryTemplate(proposal["entry_template"])
    allocations = TEMPLATE_CONFIG[template]["leg_allocations"]
    for leg_index, allocation in enumerate(allocations, start=1):
        initial = leg_index == 1
        leg_status = initial_leg_status if initial else "planned"
        hold_required = 0
        base_required = 0
        if not initial:
            hold_required = 1 if template == EntryTemplate.THREE_LEG_FRONT and leg_index == 2 else 2
            base_required = 2 if template == EntryTemplate.THREE_LEG_FRONT and leg_index == 2 else 3
        await session.execute(
            text(
                """
                INSERT INTO entry_legs (
                    proposal_id, leg_index, risk_allocation_pct, status, trigger_type,
                    trigger_price, chase_ceiling, relative_volume_threshold,
                    hold_required, base_required, eligible_session_start, eligible_session_end
                ) VALUES (
                    :proposal_id, :leg_index, :risk_allocation_pct, :status, :trigger_type,
                    :trigger_price, :chase_ceiling, :relative_volume_threshold,
                    :hold_required, :base_required, :eligible_start, :eligible_end
                )
                """
            ),
            {
                "proposal_id": proposal_id,
                "leg_index": leg_index,
                "risk_allocation_pct": allocation,
                "status": leg_status,
                "trigger_type": "pivot" if initial else "base_breakout",
                "trigger_price": proposal["pivot_price"] if initial else None,
                "chase_ceiling": proposal["chase_ceiling"] if initial else None,
                "relative_volume_threshold": proposal["relative_volume_threshold"],
                "hold_required": hold_required,
                "base_required": base_required,
                "eligible_start": proposal["entry_session_date"] if initial else None,
                "eligible_end": proposal["entry_session_date"] if initial else None,
            },
        )
    await session.commit()
    if auto_arm:
        logger.info(
            "Proposal %s (%s) AUTO-ARMED in paper mode (Leg 1 armed)",
            proposal_id,
            proposal["symbol"],
        )
        if redis is not None:
            try:
                await publish_tick_subscriptions(redis, [proposal["symbol"]])
            except Exception:
                logger.exception("Failed to publish tick subscription for %s", proposal["symbol"])
    return ProposalPersistenceResult(
        proposal_id=str(proposal_id),
        created=True,
        status=str(proposal_status),
    )


async def process_proposal_candidate(
    *,
    automation_run_id: str,
    candidate: Any,
    as_of_date: dt.date,
    deadline: dt.datetime,
    redis: Any = None,
) -> CandidateOutcome:
    """Process one candidate under the structural-first audit lifecycle.

    Dispositions:
      proposal  - Gemini valid + structural gates ok + numeric gates + geometry
      forming   - immature / still developing / not yet tightened (watch row)
      invalid   - structural breakdown (undercut, distribution, climax-fade,
                  flat shelf) or model-level not_vcp / numeric-gate failure
      existing  - identical immutable proposal already exists (no-op)
      failed    - provider / infrastructure failure
    Structural gates run on RAW geometry before inference and short-circuit
    hard-invalid charts (no provider cost); Gemini can never hide a lower low.
    """
    async with async_session() as session:
        candles = await _load_frozen_candles(
            session,
            instrument_id=str(candidate.instrument_id),
            as_of_date=as_of_date,
        )
        policy = (
            await session.execute(
                text(
                    """
                    SELECT id, version, risk_per_trade_pct, deployable_capital_override
                    FROM risk_policies
                    WHERE is_active = true
                    ORDER BY version DESC
                    LIMIT 1
                    """
                )
            )
        ).mappings().one_or_none()
        if policy is None:
            raise RuntimeError("No active risk policy; proposal generation fails closed")
        if policy["deployable_capital_override"] is None:
            raise RuntimeError(
                "Active risk policy has no operator-configured deployable capital"
            )

        stage_row = (
            await session.execute(
                text("SELECT stage FROM p10_rollout_state WHERE id = true")
            )
        ).mappings().one_or_none()
        current_stage = stage_row["stage"] if stage_row else "shadow"

    tick_size = Decimal(str(candidate.tick_size))
    annotations = derive_chart_geometry(candles, tick_size=tick_size)
    facts = compute_structural_facts(
        candles, annotations.contractions, tick_size=tick_size
    )
    verdict = evaluate_structural_gates(facts)
    candidate_summary = format_candidate_summary(annotations.contractions, facts=facts)
    charts = await asyncio.to_thread(
        render_proposal_charts, candles=candles, symbol=candidate.symbol
    )
    source_hash = compute_frozen_source_hash(candles)

    for attempt_number in range(1, settings.proposal_max_attempts + 1):
        remaining = (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if remaining <= 0:
            return "timed_out"
        timeout = min(settings.proposal_attempt_timeout_seconds, remaining)
        async with async_session() as session:
            attempt_id = await _insert_attempt(
                session,
                automation_run_id=automation_run_id,
                candidate=candidate,
                attempt_number=attempt_number,
                source_hash=source_hash,
                charts=charts,
                policy_version=int(policy["version"]),
                candidate_summary=candidate_summary,
            )
        logger.info(
            "Processing candidate %s (attempt %s/%s) for automation run %s "
            "structural_disposition=%s codes=%s",
            candidate.symbol,
            attempt_number,
            settings.proposal_max_attempts,
            automation_run_id,
            verdict.disposition,
            ",".join(verdict.codes) or "-",
        )

        if verdict.disposition == "invalid":
            # Deterministic structural breakdown: no inference needed.
            async with async_session() as session:
                await _finish_attempt(
                    session,
                    attempt_id=attempt_id,
                    status="invalid",
                    error_type=PROPOSAL_STRUCTURAL_INVALID,
                    error_message=structural_rejection_message(facts, verdict),
                    error_details={
                        "gate_disposition": verdict.disposition,
                        "gate_codes": list(verdict.codes),
                        "gate_details": verdict.details,
                        "structural_facts": structural_facts_to_dict(facts),
                    },
                )
                await close_forming_watch(
                    session,
                    instrument_id=str(candidate.instrument_id),
                    status="broken_down",
                )
                await session.commit()
            return "rejected"

        try:
            ai_output, usage, cost, request_id = await asyncio.wait_for(
                call_gemini_vision_for_proposal(
                    context_png=charts.context_png,
                    detail_png=charts.detail_png,
                    model=settings.vcp_vision_model,
                    tick_size=tick_size,
                    candidate_summary=candidate_summary,
                ),
                timeout=timeout,
            )
            logger.info(
                "Gemini vision output for %s: classification=%s, pattern_type=%s, "
                "primary_reason=%s, dry_up=%s, tightening=%s, confidence=%s",
                candidate.symbol,
                ai_output.classification,
                getattr(ai_output, "pattern_type", None),
                getattr(ai_output, "primary_reason", None),
                ai_output.volume_dry_up,
                ai_output.progressive_tightening,
                ai_output.confidence,
            )
        except TimeoutError as exc:
            logger.warning(
                "Proposal attempt %s for %s timed out: %s",
                attempt_number,
                candidate.symbol,
                exc,
            )
            async with async_session() as session:
                await _finish_attempt(session, attempt_id=attempt_id, status="timed_out", error=exc)
            if dt.datetime.now(dt.timezone.utc) >= deadline:
                return "timed_out"
            if attempt_number < settings.proposal_max_attempts:
                delay = min(3.0 * attempt_number, max(0.0, (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds() - 2.0))
                if delay > 0:
                    await asyncio.sleep(delay)
            continue
        except ProposalProviderError as exc:
            logger.warning(
                "Proposal attempt %s for %s returned unusable provider JSON: %s",
                attempt_number,
                candidate.symbol,
                exc,
            )
            async with async_session() as session:
                await _finish_attempt(
                    session,
                    attempt_id=attempt_id,
                    status="failed",
                    error=exc,
                    error_type=exc.error_type,
                    error_message=str(exc),
                    error_details=exc.details,
                )
            if attempt_number == settings.proposal_max_attempts:
                return "failed"
            delay = min(3.0 * attempt_number, max(0.0, (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds() - 2.0))
            if delay > 0:
                await asyncio.sleep(delay)
            continue
        except Exception as exc:
            logger.warning(
                "Proposal attempt %s for %s failed: %s",
                attempt_number,
                candidate.symbol,
                exc,
            )
            async with async_session() as session:
                await _finish_attempt(session, attempt_id=attempt_id, status="failed", error=exc)
            if attempt_number == settings.proposal_max_attempts:
                return "failed"
            delay = min(3.0 * attempt_number, max(0.0, (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds() - 2.0))
            if delay > 0:
                await asyncio.sleep(delay)
            continue

        output_json = ai_output.model_dump(mode="json")
        if ai_output.classification != "valid":
            if ai_output.classification == "forming":
                outcome: CandidateOutcome = "uncertain"
                attempt_status = "partial"
                ai_error_type = "proposal_ai_forming"
                ai_error_message = "Gemini classified the pattern as forming."
            else:
                outcome = "rejected"
                attempt_status = "invalid"
                ai_error_type = "proposal_ai_invalid"
                ai_error_message = (
                    f"Gemini returned classification={ai_output.classification!r}."
                )
            async with async_session() as session:
                await _finish_attempt(
                    session,
                    attempt_id=attempt_id,
                    status=attempt_status,
                    output=output_json,
                    usage=usage,
                    cost=cost,
                    request_id=request_id,
                    error_type=ai_error_type,
                    error_message=ai_error_message,
                    error_details={
                        "structural_facts": structural_facts_to_dict(facts),
                        "gate_details": verdict.details,
                    },
                )
                if ai_output.classification == "forming":
                    if ai_output.forming_state == "breaking_down":
                        await close_forming_watch(
                            session,
                            instrument_id=str(candidate.instrument_id),
                            status="broken_down",
                        )
                    else:
                        await upsert_forming_watch(
                            session,
                            instrument_id=str(candidate.instrument_id),
                            screening_result_id=str(candidate.screening_result_id),
                            symbol=candidate.symbol,
                            as_of_date=as_of_date,
                            forming_state=ai_output.forming_state or "developing",
                            llm_snapshot=output_json,
                            python_candidates=[
                                {
                                    "index": wave.index,
                                    "high_date": wave.high_date,
                                    "low_date": wave.low_date,
                                    "depth_pct": str(wave.depth_pct),
                                }
                                for wave in annotations.contractions
                            ],
                            attempt_id=attempt_id,
                            holidays=_holiday_dates(),
                        )
                elif ai_output.classification == "not_vcp":
                    await close_forming_watch(
                        session,
                        instrument_id=str(candidate.instrument_id),
                        status="broken_down",
                    )
                await session.commit()
            return outcome

        # classification == valid below this point.
        if verdict.disposition == "forming":
            # Gemini says valid but the deterministic structure is still
            # immature / not yet tightened: route into the forming watch.
            async with async_session() as session:
                await _finish_attempt(
                    session,
                    attempt_id=attempt_id,
                    status="partial",
                    output=output_json,
                    usage=usage,
                    cost=cost,
                    request_id=request_id,
                    error_type=PROPOSAL_STRUCTURAL_FORMING,
                    error_message=structural_rejection_message(facts, verdict),
                    error_details={
                        "gate_disposition": verdict.disposition,
                        "gate_codes": list(verdict.codes),
                        "gate_details": verdict.details,
                        "structural_facts": structural_facts_to_dict(facts),
                        "llm_snapshot": output_json,
                    },
                )
                await upsert_forming_watch(
                    session,
                    instrument_id=str(candidate.instrument_id),
                    screening_result_id=str(candidate.screening_result_id),
                    symbol=candidate.symbol,
                    as_of_date=as_of_date,
                    forming_state="developing",
                    llm_snapshot=output_json,
                    python_candidates=[
                        {
                            "index": wave.index,
                            "high_date": wave.high_date,
                            "low_date": wave.low_date,
                            "depth_pct": str(wave.depth_pct),
                        }
                        for wave in annotations.contractions
                    ],
                    attempt_id=attempt_id,
                    holidays=_holiday_dates(),
                )
                await session.commit()
            return "uncertain"

        build_result: ProposalBuildResult = generate_trade_proposal_from_analysis(
            symbol=candidate.symbol,
            as_of_date=as_of_date,
            screening_result_id=str(candidate.screening_result_id),
            instrument_id=str(candidate.instrument_id),
            candles=candles,
            ai_output=ai_output,
            rendered_charts=charts,
            model=settings.vcp_vision_model,
            tick_size=tick_size,
            python_candidates=annotations.contractions,
            structural_facts=facts,
            structural_verdict=verdict,
            risk_policy_id=str(policy["id"]),
            risk_policy_version=int(policy["version"]),
            risk_per_trade_pct=Decimal(str(policy["risk_per_trade_pct"])),
            approved_risk_budget_amount=(
                (
                    Decimal(str(policy["deployable_capital_override"]))
                    * (Decimal("0.25") if current_stage == "reduced_live" else Decimal("1.0"))
                )
                * Decimal(str(policy["risk_per_trade_pct"]))
            ),
            holidays=_holiday_dates(),
        )
        if not build_result.accepted:
            code = build_result.rejection_code or "proposal_rejected"
            logger.info(
                "Proposal candidate %s rejected code=%s detail=%s",
                candidate.symbol,
                code,
                build_result.rejection_message,
            )
            async with async_session() as session:
                if code == PROPOSAL_STRUCTURAL_FORMING:
                    # Defensive double-guard: generator flagged forming that
                    # the worker pre-check missed. Route to the forming watch.
                    await upsert_forming_watch(
                        session,
                        instrument_id=str(candidate.instrument_id),
                        screening_result_id=str(candidate.screening_result_id),
                        symbol=candidate.symbol,
                        as_of_date=as_of_date,
                        forming_state="developing",
                        llm_snapshot=output_json,
                        python_candidates=[
                            {
                                "index": wave.index,
                                "high_date": wave.high_date,
                                "low_date": wave.low_date,
                                "depth_pct": str(wave.depth_pct),
                            }
                            for wave in annotations.contractions
                        ],
                        attempt_id=attempt_id,
                        holidays=_holiday_dates(),
                    )
                elif code not in {
                    "proposal_risk_budget_missing",
                    PROPOSAL_STRUCTURAL_INVALID,
                }:
                    # Structural invalid double-guard + every model/numeric/
                    # geometry rejection means the pattern is broken down.
                    # Budget/operational failures leave any watch untouched.
                    await close_forming_watch(
                        session,
                        instrument_id=str(candidate.instrument_id),
                        status="broken_down",
                    )
                await _finish_attempt(
                    session,
                    attempt_id=attempt_id,
                    status="invalid",
                    output=output_json,
                    usage=usage,
                    cost=cost,
                    request_id=request_id,
                    error_type=code,
                    error_message=build_result.rejection_message,
                    error_details=build_result.rejection_details,
                )
                await session.commit()
            return "rejected"

        async with async_session() as session:
            persistence = await _persist_proposal(
                session,
                automation_run_id=automation_run_id,
                proposal=build_result.proposal,
                charts=charts,
                request_id=request_id,
                usage=usage,
                cost=cost,
                redis=redis,
                rollout_stage=current_stage,
            )
            if not persistence.created:
                message = (
                    "An identical immutable proposal already exists with status "
                    f"{persistence.status!r}; no new proposal row was created. "
                    "Open the matching status tab or All Trades to review it."
                )
                await close_forming_watch(
                    session,
                    instrument_id=str(candidate.instrument_id),
                    status="promoted",
                    proposal_id=persistence.proposal_id,
                )
                await _finish_attempt(
                    session,
                    attempt_id=attempt_id,
                    status="invalid",
                    output=output_json,
                    usage=usage,
                    cost=cost,
                    request_id=request_id,
                    error_type="proposal_already_exists",
                    error_message=message,
                    error_details={
                        "existing_proposal_id": persistence.proposal_id,
                        "existing_proposal_status": persistence.status,
                    },
                )
                await session.commit()
                return "existing"
            await close_forming_watch(
                session,
                instrument_id=str(candidate.instrument_id),
                status="promoted",
                proposal_id=persistence.proposal_id,
            )
            await _finish_attempt(
                session,
                attempt_id=attempt_id,
                status="valid",
                output=output_json,
                usage=usage,
                cost=cost,
                request_id=request_id,
            )
        return "generated"

    return "failed"


async def run_eod_proposal_batch(
    ctx: dict[str, Any],
    scan_run_id: str,
    limit: int = 20,
    manual: bool = False,
) -> dict[str, Any]:
    redis = ctx.get("redis") if isinstance(ctx, dict) else None
    if not manual and not settings.proposal_automation_enabled:
        return {"status": "disabled", "scan_run_id": scan_run_id}

    effective_limit = cap_proposal_batch_limit(limit, settings.proposal_batch_limit)
    async with async_session() as session:
        if await _control_is_paused(session, "proposal_processing_paused"):
            return {"status": "paused", "scan_run_id": scan_run_id}

        if not manual:
            existing = (
                await session.execute(
                    text(
                        """
                        SELECT id, status
                        FROM automation_runs
                        WHERE scan_run_id = :scan_run_id
                        LIMIT 1
                        """
                    ),
                    {"scan_run_id": scan_run_id},
                )
            ).mappings().one_or_none()
            if existing is not None:
                logger.info(
                    "Automatic proposal batch for scan %s already exists (id=%s, status=%s); skipping duplicate",
                    scan_run_id,
                    existing["id"],
                    existing["status"],
                )
                return {
                    "status": "already_exists",
                    "scan_run_id": scan_run_id,
                    "automation_run_id": str(existing["id"]),
                }

        scan_run_row = (
            await session.execute(
                text(
                    """
                    SELECT as_of_date, completed_at
                    FROM scan_runs
                    WHERE id = :scan_run_id
                      AND visibility = 'personal'
                      AND triggered_by <> 'manual_shadow'
                    """
                ),
                {"scan_run_id": scan_run_id},
            )
        ).mappings().one_or_none()
        if scan_run_row is None:
            return {"status": "no_scan_run", "scan_run_id": scan_run_id}
        if scan_run_row["completed_at"] is None:
            raise RuntimeError("Proposal shortlist has no durable scan completion time")

        as_of_date = scan_run_row["as_of_date"]
        scan_completed_at = scan_run_row["completed_at"]

        result = await session.execute(
            text(
                """
                SELECT sr.id AS screening_result_id, sr.instrument_id,
                       i.fyers_symbol AS symbol, i.tick_size, i.lot_size,
                       sr.result_rank, sr.technical_score, r.as_of_date,
                       r.completed_at AS scan_completed_at
                FROM screening_results sr
                JOIN instruments i ON i.id = sr.instrument_id
                JOIN scan_runs r ON r.id = sr.scan_run_id
                WHERE sr.scan_run_id = :scan_run_id
                  AND r.visibility = 'personal'
                  AND r.triggered_by <> 'manual_shadow'
                  AND sr.technical_passed = true
                  AND sr.result_rank IS NOT NULL
                  AND (
                      :p7_enabled = false
                      OR COALESCE(
                          (sr.technical_metrics ->> 'fundamental_selected')::boolean,
                          false
                      ) = true
                  )
                ORDER BY sr.result_rank ASC
                LIMIT :limit
                """
            ),
            {
                "scan_run_id": scan_run_id,
                "limit": effective_limit,
                "p7_enabled": settings.p7_fundamental_pass_enabled,
            },
        )
        candidates = list(result.all())

        expired = await expire_stale_forming_watches(
            session, as_of_date=as_of_date, holidays=_holiday_dates()
        )
        if expired:
            logger.info("Expired %s stale forming watches as_of=%s", expired, as_of_date)

        raw_rechecks = await load_forming_rechecks(
            session, as_of_date=as_of_date, cap=FORMING_RECHECK_CAP
        )
        shortlist_instrument_ids = {
            str(c.instrument_id)
            for c in candidates
            if getattr(c, "instrument_id", None) is not None
        }
        forming_rechecks = []
        seen_recheck_ids = set()
        for recheck in raw_rechecks:
            inst_id = str(recheck.instrument_id) if getattr(recheck, "instrument_id", None) else None
            if not inst_id or inst_id in shortlist_instrument_ids or inst_id in seen_recheck_ids:
                continue
            seen_recheck_ids.add(inst_id)
            forming_rechecks.append(recheck)

        total_candidates = len(candidates) + len(forming_rechecks)
        if total_candidates == 0:
            return {"status": "no_candidates", "scan_run_id": scan_run_id}

        now = dt.datetime.now(dt.timezone.utc)
        deadline = proposal_batch_deadline(
            as_of_date=as_of_date,
            now=now,
            holidays=_holiday_dates(),
            manual=manual,
        )
        run_result = await session.execute(
            text(
                """
                INSERT INTO automation_runs (
                    scan_run_id, status, candidates_total, batch_deadline
                ) VALUES (:scan_run_id, 'running', :total, :deadline)
                RETURNING id
                """
            ),
            {"scan_run_id": scan_run_id, "total": total_candidates, "deadline": deadline},
        )
        automation_run_id = str(run_result.scalar_one())
        await session.commit()

    try:
        return await _process_automation_candidates(
            automation_run_id=automation_run_id,
            scan_run_id=scan_run_id,
            candidates=candidates,
            forming_rechecks=forming_rechecks,
            total_candidates=total_candidates,
            as_of_date=as_of_date,
            deadline=deadline,
            manual=manual,
            redis=redis,
        )
    except asyncio.CancelledError:
        async with async_session() as session:
            await finalize_interrupted_proposal_run(
                session,
                automation_run_id=automation_run_id,
                batch_deadline=deadline,
            )
        raise
    except Exception:
        async with async_session() as session:
            await finalize_interrupted_proposal_run(
                session,
                automation_run_id=automation_run_id,
                batch_deadline=deadline,
            )
        raise


async def run_single_proposal(
    ctx: dict[str, Any],
    screening_result_id: str,
) -> dict[str, Any]:
    """Operator-triggered single-stock P10 generation on the serial worker."""
    redis = ctx.get("redis") if isinstance(ctx, dict) else None
    async with async_session() as session:
        if await _control_is_paused(session, "proposal_processing_paused"):
            return {"status": "paused", "screening_result_id": screening_result_id}
        result = await session.execute(
            text(
                """
                SELECT sr.id AS screening_result_id, sr.instrument_id,
                       i.fyers_symbol AS symbol, i.tick_size, i.lot_size,
                       sr.result_rank, sr.technical_score, r.as_of_date,
                       r.completed_at AS scan_completed_at, r.id AS scan_run_id
                FROM screening_results sr
                JOIN instruments i ON i.id = sr.instrument_id
                JOIN scan_runs r ON r.id = sr.scan_run_id
                WHERE sr.id = :screening_result_id
                  AND r.visibility = 'personal'
                  AND r.triggered_by <> 'manual_shadow'
                  AND r.status = 'succeeded'
                  AND sr.technical_passed = true
                  AND sr.result_rank IS NOT NULL
                  AND (
                      :p7_enabled = false
                      OR COALESCE(
                          (sr.technical_metrics ->> 'fundamental_selected')::boolean,
                          false
                      ) = true
                  )
                """
            ),
            {
                "screening_result_id": screening_result_id,
                "p7_enabled": settings.p7_fundamental_pass_enabled,
            },
        )
        candidate = result.one_or_none()
        if candidate is None:
            return {"status": "no_candidates", "screening_result_id": screening_result_id}
        as_of_date = candidate.as_of_date
        scan_completed_at = candidate.scan_completed_at
        scan_run_id = str(candidate.scan_run_id)
        if scan_completed_at is None:
            raise RuntimeError("Proposal shortlist has no durable scan completion time")
        now = dt.datetime.now(dt.timezone.utc)
        deadline = proposal_batch_deadline(
            as_of_date=as_of_date,
            now=now,
            holidays=_holiday_dates(),
            manual=True,
        )
        run_result = await session.execute(
            text(
                """
                INSERT INTO automation_runs (
                    scan_run_id, status, candidates_total, batch_deadline
                ) VALUES (:scan_run_id, 'running', 1, :deadline)
                RETURNING id
                """
            ),
            {"scan_run_id": scan_run_id, "deadline": deadline},
        )
        automation_run_id = str(run_result.scalar_one())
        await session.commit()

    try:
        return await _process_automation_candidates(
            automation_run_id=automation_run_id,
            scan_run_id=scan_run_id,
            candidates=[candidate],
            forming_rechecks=[],
            total_candidates=1,
            as_of_date=as_of_date,
            deadline=deadline,
            manual=True,
            redis=redis,
        )
    except asyncio.CancelledError:
        async with async_session() as session:
            await finalize_interrupted_proposal_run(
                session,
                automation_run_id=automation_run_id,
                batch_deadline=deadline,
            )
        raise
    except Exception:
        async with async_session() as session:
            await finalize_interrupted_proposal_run(
                session,
                automation_run_id=automation_run_id,
                batch_deadline=deadline,
            )
        raise


async def _process_automation_candidates(
    *,
    automation_run_id: str,
    scan_run_id: str,
    candidates: list[Any],
    forming_rechecks: list[Any] | None = None,
    total_candidates: int | None = None,
    as_of_date: dt.date,
    deadline: dt.datetime,
    manual: bool,
    redis: Any = None,
) -> dict[str, Any]:
    counts = {key: 0 for key in ("generated", "rejected", "uncertain", "existing", "failed", "timed_out")}
    processed = 0
    all_candidates = list(candidates) + list(forming_rechecks or [])
    total = total_candidates if total_candidates is not None else len(all_candidates)

    for index, candidate in enumerate(all_candidates):
        if dt.datetime.now(dt.timezone.utc) >= deadline:
            remaining = len(all_candidates) - index
            counts["timed_out"] += remaining
            logger.warning(
                "Proposal batch for scan %s hit the deadline with %s candidates unprocessed "
                "(deadline=%s manual=%s processed=%s)",
                scan_run_id,
                remaining,
                deadline.isoformat(),
                manual,
                processed,
            )
            break
        try:
            outcome = await process_proposal_candidate(
                automation_run_id=automation_run_id,
                candidate=candidate,
                as_of_date=as_of_date,
                deadline=deadline,
                redis=redis,
            )
        except Exception:
            logger.exception("Proposal candidate %s failed", candidate.symbol)
            outcome = "failed"
        counts[outcome] += 1
        processed += 1
        async with async_session() as session:
            await session.execute(
                text(
                    """
                    UPDATE automation_runs
                    SET candidates_processed = :processed,
                        proposals_generated = :generated,
                        proposals_rejected = :rejected,
                        proposals_uncertain = :uncertain,
                        proposals_failed = :failed,
                        updated_at = now()
                    WHERE id = :run_id AND status = 'running'
                    """
                ),
                {
                    "run_id": automation_run_id,
                    "processed": processed,
                    "generated": counts["generated"],
                    "rejected": counts["rejected"],
                    "uncertain": counts["uncertain"],
                    "failed": counts["failed"],
                },
            )
            await session.commit()
        logger.info(
            "Proposal candidate %s outcome=%s (%s/%s)",
            candidate.symbol,
            outcome,
            processed,
            total,
        )

    terminal_status = "timed_out" if counts["timed_out"] else "completed"
    error_message = None
    if terminal_status == "timed_out" and processed == 0:
        error_message = (
            "Batch deadline already passed before any candidate was processed "
            f"(deadline {deadline.isoformat()})."
        )
        logger.warning(
            "Proposal batch for scan %s timed out before processing any candidate; "
            "deadline=%s manual=%s",
            scan_run_id,
            deadline.isoformat(),
            manual,
        )
    async with async_session() as session:
        await session.execute(
            text(
                """
                UPDATE automation_runs
                SET status = :status, candidates_processed = :processed,
                    proposals_generated = :generated,
                    proposals_rejected = :rejected,
                    proposals_uncertain = :uncertain,
                    proposals_failed = :failed,
                    error_message = :error_message,
                    completed_at = now(), updated_at = now()
                WHERE id = :run_id
                """
            ),
            {
                "run_id": automation_run_id,
                "status": terminal_status,
                "processed": processed,
                "error_message": error_message,
                "generated": counts["generated"],
                "rejected": counts["rejected"],
                "uncertain": counts["uncertain"],
                "failed": counts["failed"],
            },
        )
        await session.commit()
    return {
        "status": terminal_status,
        "scan_run_id": scan_run_id,
        "automation_run_id": automation_run_id,
        "proposals_existing": counts["existing"],
        "counts": counts,
    }


async def _renew_proposal_worker_lease(redis: Any, worker_id: str) -> None:
    missed_count = 0
    while True:
        await asyncio.sleep(_LOCK_REFRESH_SECONDS)
        refreshed = await renew_distributed_lease(
            redis,
            _LOCK_KEY,
            worker_id,
            _LOCK_TTL_SECONDS,
        )
        if refreshed:
            missed_count = 0
        else:
            missed_count += 1
            logger.warning(
                "Proposal worker failed to renew singleton lease (consecutive misses: %d)",
                missed_count,
            )
            if missed_count >= 3:
                logger.critical("Proposal worker lost its singleton lease after 3 attempts; stopping.")
                os.kill(os.getpid(), signal.SIGTERM)
                return


async def worker_on_startup(ctx: dict[str, Any]) -> None:
    await tune_arq_redis_pool(ctx["redis"])
    worker_id = str(uuid4())
    ctx["lease_owner"] = worker_id
    if not await acquire_distributed_lease(
        ctx["redis"],
        _LOCK_KEY,
        worker_id,
        _LOCK_TTL_SECONDS,
    ):
        raise RuntimeError("Another proposal worker owns the singleton lease.")
    async with async_session() as session:
        recovered = await recover_interrupted_proposal_runs(session)
    if recovered:
        logger.warning(
            "Finalized %s proposal batch(es) left running by an earlier worker process.",
            recovered,
        )
    ctx["lease_renew_task"] = asyncio.create_task(
        _renew_proposal_worker_lease(ctx["redis"], worker_id)
    )


async def worker_on_shutdown(ctx: dict[str, Any]) -> None:
    renew_task = ctx.get("lease_renew_task")
    if renew_task is not None:
        renew_task.cancel()
        try:
            await renew_task
        except asyncio.CancelledError:
            pass
    owner_id = ctx.get("lease_owner")
    if owner_id:
        await release_distributed_lease(ctx["redis"], _LOCK_KEY, owner_id)


class WorkerSettings:
    functions = [run_eod_proposal_batch, run_single_proposal, expire_unapproved_job]
    cron_jobs = [
        cron(
            expire_unapproved_job,
            name="expire_unapproved_trade_proposals",
            minute=set(range(60)),
            second=5,
            timeout=30,
            max_tries=1,
        )
    ]
    queue_name = settings.proposal_queue_name
    max_jobs = 1
    max_tries = 1
    retry_jobs = False
    job_timeout = 7200
    on_startup = worker_on_startup
    on_shutdown = worker_on_shutdown
    timezone = IST_TZ
    redis_settings = redis_settings_from_config()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
