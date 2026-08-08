"""Ordered P7 worker: Upstox facts -> authoritative rules -> optional AI opinion."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.fundamental_controls import is_fundamental_control_paused
from app.services.fundamental_data import (
    FundamentalsAuthError,
    FundamentalsDataUnavailable,
    FundamentalsError,
    UpstoxFundamentalsClient,
    canonical_json_hash,
    normalize_fundamentals,
)
from app.services.fundamental_llm import (
    FundamentalLLMError,
    FundamentalLLMResult,
    OpenRouterFundamentalClient,
    PreparedFundamentalRequest,
)
from app.services.fundamental_rules import FACTS_SCHEMA_VERSION, RUBRIC_VERSION, score_minervini_inspired

logger = logging.getLogger(__name__)
GLOBAL_P7_LOCK = 7_000_007


@dataclass(frozen=True)
class Survivor:
    result_id: UUID
    scan_run_id: UUID
    instrument_id: UUID
    isin: str | None
    symbol: str
    company_name: str | None
    rank: int = 9999


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: UUID
    facts: dict[str, Any]
    fetched_at: datetime.datetime
    latest_annual_period: str | None
    latest_quarterly_period: str | None
    cache_hit: bool


def p7_run_config(*, technical_rank_limit: int = 20) -> dict[str, Any]:
    return {
        "enabled": settings.p7_fundamental_pass_enabled,
        "fundamentals_provider": "upstox",
        "statement_type": "consolidated",
        "snapshot_ttl_hours": settings.fundamentals_snapshot_ttl_hours,
        "model_provider": "openrouter",
        "model": settings.openrouter_model,
        "prompt_version": settings.openrouter_prompt_version,
        "reasoning_enabled": True,
        "reasoning_effort": settings.openrouter_reasoning_effort,
        "reasoning_excluded": True,
        "response_schema": "fundamental_second_opinion_v1",
        "rubric_version": RUBRIC_VERSION,
        "token_budget": settings.fundamental_run_token_budget,
        "selection": {"source": "technical_score_rank", "rank_limit": technical_rank_limit},
    }


async def _load_survivors(scan_run_id: str) -> list[Survivor]:
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT s.id AS result_id, s.scan_run_id, s.instrument_id, i.isin,
                       i.symbol, i.name AS company_name, s.result_rank
                FROM screening_results s
                JOIN instruments i ON i.id = s.instrument_id
                WHERE s.scan_run_id = :scan_run_id
                  AND s.technical_passed = true
                  AND COALESCE((s.technical_metrics ->> 'fundamental_selected')::boolean, false) = true
                  AND s.llm_status IN ('queued', 'failed', 'skipped')
                ORDER BY s.result_rank ASC NULLS LAST, s.created_at ASC
                LIMIT 20
                """
            ),
            {"scan_run_id": scan_run_id},
        )
        return [
            Survivor(
                result_id=row.result_id,
                scan_run_id=row.scan_run_id,
                instrument_id=row.instrument_id,
                isin=row.isin,
                symbol=row.symbol,
                company_name=row.company_name,
                rank=int(row.result_rank or 9999),
            )
            for row in result.all()
        ]


async def _ensure_analysis_run(scan_run_id: str, job_id: str) -> UUID:
    async with async_session() as db:
        existing = await db.execute(
            text(
                """
                SELECT id FROM fundamental_analysis_runs
                WHERE scan_run_id = :scan_run_id AND status IN ('queued', 'running')
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"scan_run_id": scan_run_id},
        )
        run_id = existing.scalar_one_or_none()
        if run_id is not None:
            return run_id
        inserted = await db.execute(
            text(
                """
                INSERT INTO fundamental_analysis_runs (scan_run_id, queue_job_id, config)
                VALUES (:scan_run_id, :queue_job_id, CAST(:config AS jsonb))
                RETURNING id
                """
            ),
            {"scan_run_id": scan_run_id, "queue_job_id": job_id, "config": json.dumps(p7_run_config())},
        )
        run_id = inserted.scalar_one()
        await db.commit()
        return run_id


async def _finish_unprocessed_results(
    scan_run_id: str,
    *,
    llm_status: str,
    ai_status: str,
    reason: str,
) -> None:
    """Move untouched queued results to a terminal state so UI polling stops."""
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE screening_results
                SET
                    llm_status = :llm_status,
                    ai_status = :ai_status,
                    llm_flags = COALESCE(llm_flags, '{}'::jsonb)
                        || CAST(:operational_state AS jsonb)
                WHERE scan_run_id = :scan_run_id
                  AND technical_passed = true
                  AND COALESCE(
                        (technical_metrics ->> 'fundamental_selected')::boolean,
                        false
                  ) = true
                  AND llm_status IN ('queued', 'running')
                """
            ),
            {
                "scan_run_id": scan_run_id,
                "llm_status": llm_status,
                "ai_status": ai_status,
                "operational_state": json.dumps(
                    {
                        "operational_status": {
                            "status": ai_status,
                            "message": reason,
                        }
                    }
                ),
            },
        )
        await db.commit()


async def _seed_items(analysis_run_id: UUID, survivors: list[Survivor]) -> None:
    if not survivors:
        return
    async with async_session() as db:
        await db.execute(
            text(
                """
                INSERT INTO fundamental_analysis_items (analysis_run_id, screening_result_id, rank)
                VALUES (:analysis_run_id, :screening_result_id, :rank)
                ON CONFLICT (analysis_run_id, screening_result_id) DO NOTHING
                """
            ),
            [
                {"analysis_run_id": analysis_run_id, "screening_result_id": item.result_id, "rank": item.rank}
                for item in survivors
            ],
        )
        await db.commit()


async def _set_run(analysis_run_id: UUID, *, status: str | None = None, survivor: Survivor | None = None, error: str | None = None, completed: bool = False) -> None:
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE fundamental_analysis_runs
                SET status = COALESCE(:status, status),
                    current_rank = COALESCE(:rank, current_rank),
                    current_symbol = COALESCE(:symbol, current_symbol),
                    heartbeat_at = now(),
                    error_message = COALESCE(:error, error_message),
                    started_at = CASE WHEN :status = 'running' THEN COALESCE(started_at, now()) ELSE started_at END,
                    completed_at = CASE WHEN :completed THEN now() ELSE completed_at END
                WHERE id = :id
                """
            ),
            {"id": analysis_run_id, "status": status, "rank": survivor.rank if survivor else None, "symbol": survivor.symbol if survivor else None, "error": error, "completed": completed},
        )
        await db.commit()


async def _set_item(analysis_run_id: UUID, survivor: Survivor, *, status: str, snapshot_id: UUID | None = None, error: Exception | None = None, analysis_key: str | None = None, usage: dict[str, int] | None = None, cost: float = 0.0, provider_requests: int = 0) -> None:
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE fundamental_analysis_items
                SET status = :status, snapshot_id = COALESCE(:snapshot_id, snapshot_id),
                    analysis_key = COALESCE(:analysis_key, analysis_key), provider_requests = provider_requests + :provider_requests,
                    input_tokens = input_tokens + :input_tokens,
                    reasoning_tokens = reasoning_tokens + :reasoning_tokens,
                    output_tokens = output_tokens + :output_tokens,
                    cached_tokens = cached_tokens + :cached_tokens,
                    cost = cost + :cost,
                    error_code = :error_code, error_message = :error_message,
                    started_at = CASE WHEN :status IN ('fetching', 'scoring', 'ai_running') THEN COALESCE(started_at, now()) ELSE started_at END,
                    completed_at = CASE WHEN :status IN ('succeeded', 'rules_only', 'failed', 'cancelled', 'budget_exhausted') THEN now() ELSE completed_at END
                WHERE analysis_run_id = :analysis_run_id AND screening_result_id = :result_id
                """
            ),
            {
                "analysis_run_id": analysis_run_id, "result_id": survivor.result_id, "status": status,
                "snapshot_id": snapshot_id, "analysis_key": analysis_key,
                "input_tokens": (usage or {}).get("input", 0), "reasoning_tokens": (usage or {}).get("reasoning", 0), "provider_requests": provider_requests,
                "output_tokens": (usage or {}).get("output", 0), "cached_tokens": (usage or {}).get("cached", 0), "cost": cost,
                "error_code": type(error).__name__ if error else None, "error_message": str(error)[:500] if error else None,
            },
        )
        if usage:
            await db.execute(
                text(
                    """
                    UPDATE fundamental_analysis_runs
                    SET input_tokens = input_tokens + :input_tokens,
                        reasoning_tokens = reasoning_tokens + :reasoning_tokens,
                        output_tokens = output_tokens + :output_tokens,
                        cached_tokens = cached_tokens + :cached_tokens,
                        total_cost = total_cost + :cost, heartbeat_at = now()
                    WHERE id = :id
                    """
                ),
                {"id": analysis_run_id, "input_tokens": usage.get("input", 0), "reasoning_tokens": usage.get("reasoning", 0), "output_tokens": usage.get("output", 0), "cached_tokens": usage.get("cached", 0), "cost": cost},
            )
        if provider_requests:
            await db.execute(
                text("UPDATE fundamental_analysis_runs SET provider_requests = provider_requests + :count, heartbeat_at = now() WHERE id = :id"),
                {"id": analysis_run_id, "count": provider_requests},
            )
        await db.commit()


async def _cached_snapshot(survivor: Survivor) -> Snapshot | None:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=settings.fundamentals_snapshot_ttl_hours)
    async with async_session() as db:
        result = await db.execute(
            text("""SELECT id, normalized_facts, fetched_at, latest_annual_period, latest_quarterly_period
                    FROM fundamental_snapshots WHERE instrument_id = :instrument_id AND provider = 'upstox'
                    AND statement_type = 'consolidated' AND fetched_at >= :cutoff
                    AND normalized_facts ->> 'schema_version' = :facts_schema_version
                    ORDER BY fetched_at DESC LIMIT 1"""),
            {"instrument_id": survivor.instrument_id, "cutoff": cutoff, "facts_schema_version": FACTS_SCHEMA_VERSION},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return Snapshot(row.id, dict(row.normalized_facts or {}), row.fetched_at, row.latest_annual_period, row.latest_quarterly_period, True)


async def _linked_snapshot(survivor: Survivor) -> Snapshot | None:
    """Reuse the result's reproducible snapshot regardless of cache TTL."""

    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT f.id, f.normalized_facts, f.fetched_at,
                       f.latest_annual_period, f.latest_quarterly_period
                FROM screening_results s
                JOIN fundamental_snapshots f ON f.id = s.fundamental_snapshot_id
                WHERE s.id = :result_id
                  AND f.provider = 'upstox'
                  AND f.statement_type = 'consolidated'
                  AND f.normalized_facts ->> 'schema_version' = :facts_schema_version
                """
            ),
            {
                "result_id": survivor.result_id,
                "facts_schema_version": FACTS_SCHEMA_VERSION,
            },
        )
        row = result.one_or_none()
        if row is None:
            return None
        return Snapshot(
            row.id,
            dict(row.normalized_facts or {}),
            row.fetched_at,
            row.latest_annual_period,
            row.latest_quarterly_period,
            True,
        )


async def _get_snapshot(
    survivor: Survivor,
    client: UpstoxFundamentalsClient,
    *,
    force_refresh: bool = False,
) -> Snapshot:
    cached = None
    if not force_refresh:
        cached = await _linked_snapshot(survivor) or await _cached_snapshot(survivor)
    if cached:
        return cached
    if not survivor.isin:
        raise FundamentalsDataUnavailable("Instrument has no ISIN")
    bundle = await client.fetch_company_bundle(survivor.isin, statement_type="consolidated")
    facts = normalize_fundamentals(bundle, isin=survivor.isin, symbol=survivor.symbol, company_name=survivor.company_name)
    periods = facts.get("periods", {})
    fetched_at = datetime.datetime.now(datetime.timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            text("""INSERT INTO fundamental_snapshots (instrument_id, provider, statement_type, fetched_at, latest_annual_period, latest_quarterly_period, raw_payload, normalized_facts, content_hash)
                    VALUES (:instrument_id, 'upstox', 'consolidated', :fetched_at, :annual, :quarterly, CAST(:raw AS jsonb), CAST(:facts AS jsonb), :hash) RETURNING id"""),
            {"instrument_id": survivor.instrument_id, "fetched_at": fetched_at, "annual": periods.get("latest_annual"), "quarterly": periods.get("latest_quarterly"), "raw": json.dumps(bundle, separators=(",", ":")), "facts": json.dumps(facts, separators=(",", ":")), "hash": canonical_json_hash(bundle)},
        )
        snapshot_id = result.scalar_one()
        await db.commit()
    return Snapshot(snapshot_id, facts, fetched_at, periods.get("latest_annual"), periods.get("latest_quarterly"), False)


def _usage(usage: dict[str, Any]) -> dict[str, int]:
    details = usage.get("completion_tokens_details", {}) if isinstance(usage.get("completion_tokens_details"), dict) else {}
    prompt_details = usage.get("prompt_tokens_details", {}) if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    return {
        "input": int(usage.get("prompt_tokens", 0) or 0),
        "output": int(usage.get("completion_tokens", 0) or 0),
        "reasoning": int(details.get("reasoning_tokens", 0) or 0),
        "cached": int(prompt_details.get("cached_tokens", 0) or 0),
    }


async def _run_tokens(analysis_run_id: UUID) -> int:
    async with async_session() as db:
        result = await db.execute(text("SELECT input_tokens + reasoning_tokens + output_tokens FROM fundamental_analysis_runs WHERE id = :id"), {"id": analysis_run_id})
        return int(result.scalar_one() or 0)


async def _cached_annotation(analysis_key: str) -> dict[str, Any] | None:
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT payload, request_id, usage, cost, model,
                       reasoning_effort, prompt_version, input_hash
                FROM fundamental_annotations
                WHERE analysis_key = :analysis_key
                """
            ),
            {"analysis_key": analysis_key},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None


async def _start_ai_attempt(
    analysis_run_id: UUID,
    survivor: Survivor,
    client: OpenRouterFundamentalClient,
    prepared: PreparedFundamentalRequest,
) -> tuple[UUID, int]:
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                WITH analysis_item AS (
                    SELECT id
                    FROM fundamental_analysis_items
                    WHERE analysis_run_id = :analysis_run_id
                      AND screening_result_id = :result_id
                ), numbered AS (
                    SELECT analysis_item.id AS analysis_item_id,
                           COALESCE(MAX(attempt.attempt_number), 0) + 1 AS attempt_number
                    FROM analysis_item
                    LEFT JOIN fundamental_ai_attempts attempt
                      ON attempt.analysis_item_id = analysis_item.id
                    GROUP BY analysis_item.id
                )
                INSERT INTO fundamental_ai_attempts (
                    analysis_item_id, attempt_number, status, model,
                    reasoning_effort, prompt_version, response_schema,
                    input_hash, request_payload
                )
                SELECT analysis_item_id, attempt_number, 'started', :model,
                       :reasoning, :prompt, 'fundamental_second_opinion_v1',
                       :input_hash, CAST(:request_payload AS jsonb)
                FROM numbered
                RETURNING id, attempt_number
                """
            ),
            {
                "analysis_run_id": analysis_run_id,
                "result_id": survivor.result_id,
                "model": client.model,
                "reasoning": client.reasoning_effort,
                "prompt": client.prompt_version,
                "input_hash": prepared.input_hash,
                "request_payload": json.dumps(
                    prepared.request_payload,
                    separators=(",", ":"),
                ),
            },
        )
        row = result.one()
        await db.commit()
        return row.id, int(row.attempt_number)


async def _finish_ai_attempt(
    attempt_id: UUID,
    *,
    status: str,
    result: FundamentalLLMResult | None = None,
    error: FundamentalLLMError | None = None,
) -> None:
    response_payload = result.response_payload if result else (
        error.response_payload if error else None
    )
    usage = result.usage if result else (error.usage if error else {})
    cost = result.cost if result else (error.cost if error else 0.0)
    request_id = result.request_id if result else (error.request_id if error else None)
    http_status = error.http_status if error else 200
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE fundamental_ai_attempts
                SET status = :status,
                    response_payload = CAST(:response_payload AS jsonb),
                    http_status = :http_status,
                    request_id = :request_id,
                    usage = CAST(:usage AS jsonb),
                    cost = :cost,
                    error_code = :error_code,
                    error_message = :error_message,
                    completed_at = now()
                WHERE id = :attempt_id
                """
            ),
            {
                "attempt_id": attempt_id,
                "status": status,
                "response_payload": (
                    json.dumps(response_payload, separators=(",", ":"))
                    if response_payload is not None
                    else None
                ),
                "http_status": http_status,
                "request_id": request_id,
                "usage": json.dumps(usage, separators=(",", ":")),
                "cost": cost,
                "error_code": type(error).__name__ if error else None,
                "error_message": str(error)[:500] if error else None,
            },
        )
        await db.commit()


async def _store_annotation(
    analysis_key: str,
    client: OpenRouterFundamentalClient,
    result: FundamentalLLMResult,
    source_attempt_id: UUID,
) -> None:
    async with async_session() as db:
        await db.execute(
            text("""INSERT INTO fundamental_annotations (analysis_key, model, reasoning_effort, prompt_version, input_hash, payload, request_id, usage, cost, source_attempt_id)
                    VALUES (:key, :model, :reasoning, :prompt, :input_hash, CAST(:payload AS jsonb), :request_id, CAST(:usage AS jsonb), :cost, :source_attempt_id)
                    ON CONFLICT (analysis_key) DO NOTHING"""),
            {
                "key": analysis_key,
                "model": client.model,
                "reasoning": client.reasoning_effort,
                "prompt": client.prompt_version,
                "input_hash": result.input_hash,
                "payload": json.dumps(result.opinion.model_dump(mode="json")),
                "request_id": result.request_id,
                "usage": json.dumps(result.usage),
                "cost": result.cost,
                "source_attempt_id": source_attempt_id,
            },
        )
        await db.commit()


async def _update_fundamental_result(
    survivor: Survivor,
    *,
    snapshot: Snapshot | None,
    scorecard: dict[str, Any],
    error: Exception | None = None,
) -> None:
    flags = {
        "schema_version": "fundamental_result_v4",
        "rules": scorecard,
        "assessment": scorecard,
        "summary": "Minervini-inspired fundamental fit is available." if snapshot else None,
        "strengths": [],
        "highlights": [],
        "risks": [],
        "review_focus": [],
        "criteria": scorecard.get("criteria", []),
        "red_flags": scorecard.get("red_flags", []),
        "missing_data": snapshot.facts.get("missing_data", []) if snapshot else [],
        "provider_limitations": scorecard.get("provider_limitations", []),
        "ai_opinion": None,
        "ai_skip_reason": None,
        "ai_error": None,
        "fundamental_error": (
            {"type": type(error).__name__, "message": str(error)[:500]}
            if error
            else None
        ),
        "provenance": {"snapshot_id": str(snapshot.snapshot_id) if snapshot else None, "snapshot_cache_hit": snapshot.cache_hit if snapshot else False, "rubric_version": RUBRIC_VERSION},
    }
    async with async_session() as db:
        await db.execute(
            text("""UPDATE screening_results
                    SET fundamental_status = :fundamental_status, fundamental_verdict = NULL,
                        fundamental_scorecard = CAST(:scorecard AS jsonb),
                        llm_flags = CAST(:flags AS jsonb),
                        fundamental_snapshot_id = COALESCE(:snapshot_id, fundamental_snapshot_id)
                    WHERE id = :result_id"""),
            {
                "result_id": survivor.result_id,
                "fundamental_status": "completed" if snapshot else "failed",
                "scorecard": json.dumps(scorecard),
                "flags": json.dumps(flags, separators=(",", ":")),
                "snapshot_id": snapshot.snapshot_id if snapshot else None,
            },
        )
        await db.commit()


async def _set_fundamental_running(survivor: Survivor) -> None:
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE screening_results
                SET fundamental_status = 'running'
                WHERE id = :result_id
                """
            ),
            {"result_id": survivor.result_id},
        )
        await db.commit()


async def _set_ai_running(survivor: Survivor) -> None:
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE screening_results
                SET ai_status = 'running', llm_status = 'running',
                    llm_verdict = NULL, llm_checked_at = NULL
                WHERE id = :result_id
                """
            ),
            {"result_id": survivor.result_id},
        )
        await db.commit()


async def _update_ai_result(
    survivor: Survivor,
    *,
    ai_status: str,
    opinion: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    error: Exception | None = None,
    ai_skip_reason: str | None = None,
) -> None:
    successful = ai_status in {"succeeded", "cached"}
    legacy_status = (
        "succeeded"
        if successful
        else "skipped"
        if ai_status in {"paused", "not_requested", "budget_exhausted", "skipped"}
        else "failed"
    )
    ai_fields = {
        "ai_opinion": opinion,
        "strengths": (opinion or {}).get("strengths", []),
        "highlights": (opinion or {}).get("strengths", []),
        "risks": (opinion or {}).get("risks", []),
        "review_focus": (opinion or {}).get("review_focus", []),
        "ai_skip_reason": ai_skip_reason,
        "model": model,
        "ai_error": (
            {"type": type(error).__name__, "message": str(error)[:500]}
            if error
            else None
        ),
    }
    if opinion is not None:
        ai_fields["summary"] = opinion.get("summary")
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE screening_results
                SET ai_status = :ai_status,
                    llm_status = :llm_status,
                    llm_verdict = :llm_verdict,
                    llm_flags = llm_flags || CAST(:ai_fields AS jsonb),
                    llm_checked_at = now()
                WHERE id = :result_id
                """
            ),
            {
                "result_id": survivor.result_id,
                "ai_status": ai_status,
                "llm_status": legacy_status,
                "llm_verdict": (opinion or {}).get("verdict") if successful else None,
                "ai_fields": json.dumps(ai_fields, separators=(",", ":")),
            },
        )
        await db.commit()


async def _auto_pause(control_key: str, reason: str) -> None:
    async with async_session() as db:
        await db.execute(text("UPDATE system_controls SET enabled = true, reason = :reason, changed_by = 'p7_auto_pause', changed_at = now() WHERE control_key = :key"), {"key": control_key, "reason": reason[:500]})
        await db.execute(text("INSERT INTO system_events (component, severity, event_type, payload) VALUES ('fundamental_pass', 'error', :event, CAST(:payload AS jsonb))"), {"event": f"{control_key}_auto_paused", "payload": json.dumps({"reason": reason[:500]})})
        await db.commit()


async def _processing_paused() -> bool:
    async with async_session() as db:
        return await is_fundamental_control_paused(db, "processing")


async def _ai_paused() -> bool:
    async with async_session() as db:
        return await is_fundamental_control_paused(db, "ai")


async def _process_survivor(
    analysis_run_id: UUID,
    survivor: Survivor,
    fundamentals_client: UpstoxFundamentalsClient,
    llm_client: OpenRouterFundamentalClient,
    *,
    force_refresh: bool = False,
) -> str:
    if await _processing_paused():
        await _set_item(analysis_run_id, survivor, status="cancelled")
        return "cancelled"
    await _set_run(analysis_run_id, survivor=survivor)
    await _set_item(analysis_run_id, survivor, status="fetching")
    await _set_fundamental_running(survivor)
    snapshot: Snapshot | None = None
    scorecard: dict[str, Any] | None = None
    try:
        snapshot = await _get_snapshot(survivor, fundamentals_client, force_refresh=force_refresh)
        scorecard = score_minervini_inspired(snapshot.facts)
        await _set_item(analysis_run_id, survivor, status="scoring", snapshot_id=snapshot.snapshot_id, provider_requests=0 if snapshot.cache_hit else 8)
        await _update_fundamental_result(
            survivor,
            snapshot=snapshot,
            scorecard=scorecard,
        )
        ai_paused = await _ai_paused()
        if ai_paused or await _processing_paused():
            status = "paused"
            skip_reason = "ai_paused" if ai_paused else "processing_paused"
            await _update_ai_result(
                survivor,
                ai_status=status,
                ai_skip_reason=skip_reason,
            )
            await _set_item(analysis_run_id, survivor, status="rules_only", snapshot_id=snapshot.snapshot_id)
            return "rules_only"
        prepared = llm_client.prepare(snapshot.facts)
        if not prepared.has_usable_facts:
            await _update_ai_result(
                survivor,
                ai_status="skipped",
                ai_skip_reason="no_usable_facts",
            )
            await _set_item(
                analysis_run_id,
                survivor,
                status="rules_only",
                snapshot_id=snapshot.snapshot_id,
            )
            return "rules_only"
        analysis_key = canonical_json_hash(
            {
                "input_hash": prepared.input_hash,
                "model": llm_client.model,
                "reasoning": llm_client.reasoning_effort,
                "prompt": llm_client.prompt_version,
                "response_schema": "fundamental_second_opinion_v1",
            }
        )
        cached = await _cached_annotation(analysis_key)
        if cached:
            opinion = dict(cached.get("payload") or {})
            await _update_ai_result(
                survivor,
                ai_status="cached",
                opinion=opinion,
                model={
                    "provider": "openrouter",
                    "name": cached.get("model"),
                    "reasoning_effort": cached.get("reasoning_effort"),
                    "prompt_version": cached.get("prompt_version"),
                    "request_id": cached.get("request_id"),
                    "input_hash": cached.get("input_hash"),
                    "cache_hit": True,
                },
            )
            await _set_item(analysis_run_id, survivor, status="succeeded", snapshot_id=snapshot.snapshot_id, analysis_key=analysis_key)
            return "cached"
        reserve = (
            len(json.dumps(prepared.request_payload, separators=(",", ":"))) + 1
        ) // 2 + settings.openrouter_max_tokens
        if await _run_tokens(analysis_run_id) + reserve > settings.fundamental_run_token_budget:
            await _update_ai_result(
                survivor,
                ai_status="budget_exhausted",
                ai_skip_reason="budget_exhausted",
            )
            await _set_item(analysis_run_id, survivor, status="budget_exhausted", snapshot_id=snapshot.snapshot_id, analysis_key=analysis_key)
            return "budget_exhausted"
        await _set_ai_running(survivor)
        await _set_item(analysis_run_id, survivor, status="ai_running", snapshot_id=snapshot.snapshot_id, analysis_key=analysis_key)
        for call_number in range(1, 3):
            attempt_id, _ = await _start_ai_attempt(
                analysis_run_id,
                survivor,
                llm_client,
                prepared,
            )
            try:
                result = await llm_client.send_once(prepared)
            except FundamentalLLMError as exc:
                await _finish_ai_attempt(
                    attempt_id,
                    status=exc.attempt_status,
                    error=exc,
                )
                await _set_item(
                    analysis_run_id,
                    survivor,
                    status="ai_running",
                    snapshot_id=snapshot.snapshot_id,
                    analysis_key=analysis_key,
                    usage=_usage(exc.usage),
                    cost=exc.cost,
                    provider_requests=1,
                    error=exc,
                )
                if exc.retryable and call_number == 1:
                    await asyncio.sleep(0.5)
                    continue
                raise
            await _finish_ai_attempt(
                attempt_id,
                status="succeeded",
                result=result,
            )
            usage = _usage(result.usage)
            await _set_item(
                analysis_run_id,
                survivor,
                status="ai_running",
                snapshot_id=snapshot.snapshot_id,
                analysis_key=analysis_key,
                usage=usage,
                cost=result.cost,
                provider_requests=1,
            )
            await _store_annotation(
                analysis_key,
                llm_client,
                result,
                attempt_id,
            )
            opinion = result.opinion.model_dump(mode="json")
            await _update_ai_result(
                survivor,
                ai_status="succeeded",
                opinion=opinion,
                model={
                    "provider": "openrouter",
                    "name": llm_client.model,
                    "reasoning_effort": llm_client.reasoning_effort,
                    "prompt_version": llm_client.prompt_version,
                    "request_id": result.request_id,
                    "input_hash": result.input_hash,
                    "cache_hit": False,
                },
            )
            await _set_item(
                analysis_run_id,
                survivor,
                status="succeeded",
                snapshot_id=snapshot.snapshot_id,
                analysis_key=analysis_key,
            )
            return "succeeded"
        raise FundamentalLLMError("OpenRouter second opinion failed")
    except FundamentalsAuthError as exc:
        await _auto_pause("fundamentals_processing_paused", str(exc))
        await _update_fundamental_result(
            survivor,
            snapshot=None,
            scorecard=scorecard or _empty_scorecard(),
            error=exc,
        )
        await _update_ai_result(
            survivor,
            ai_status="skipped",
            ai_skip_reason="fundamental_unavailable",
        )
        await _set_item(analysis_run_id, survivor, status="failed", snapshot_id=snapshot.snapshot_id if snapshot else None, error=exc)
        return "failed"
    except FundamentalLLMError as exc:
        if exc.http_status in {401, 402} or "key was rejected" in str(exc):
            await _auto_pause("fundamentals_ai_paused", str(exc))
        await _update_ai_result(
            survivor,
            ai_status="failed",
            error=exc,
        )
        await _set_item(
            analysis_run_id,
            survivor,
            status="rules_only",
            snapshot_id=snapshot.snapshot_id if snapshot else None,
            error=exc,
        )
        return "rules_only"
    except (FundamentalsError, ValueError, TypeError) as exc:
        scorecard = scorecard or _empty_scorecard()
        await _update_fundamental_result(
            survivor,
            snapshot=None,
            scorecard=scorecard,
            error=exc,
        )
        await _update_ai_result(
            survivor,
            ai_status="skipped",
            ai_skip_reason="fundamental_unavailable",
        )
        await _set_item(analysis_run_id, survivor, status="failed", snapshot_id=snapshot.snapshot_id if snapshot else None, error=exc)
        return "failed"


def _empty_scorecard() -> dict[str, Any]:
    """Assessment-safe fallback when Upstox cannot provide a snapshot."""
    return {
        "rubric_version": RUBRIC_VERSION,
        "score": None,
        "grade": "insufficient",
        "coverage_pct": 0.0,
        "earned_points": 0.0,
        "available_points": 0.0,
        "max_points": 100.0,
        "core_sufficient": False,
        "insufficient_reason": "No usable fundamentals snapshot was returned.",
        "components": [],
        "criteria": [],
        "red_flags": [],
        "provider_limitations": ["quarterly_eps_yoy", "quarterly_sales_yoy", "debt_to_equity", "promoter_pledge"],
        "verdict": None,
    }


async def run_fundamental_pass(
    ctx: dict[str, Any],
    scan_run_id: str,
    mode: str = "retry_incomplete",
) -> dict[str, Any]:
    """Run P7 in strict rank order; no concurrent provider or model calls."""
    job_id = str(ctx.get("job_id") or f"fundamental-pass:{scan_run_id}")
    if not settings.p7_fundamental_pass_enabled:
        return {"status": "disabled", "scan_run_id": scan_run_id}
    analysis_run_id = await _ensure_analysis_run(scan_run_id, job_id)
    async with async_session() as lock_db:
        await lock_db.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": GLOBAL_P7_LOCK})
        try:
            if await _processing_paused():
                await _finish_unprocessed_results(
                    scan_run_id,
                    llm_status="skipped",
                    ai_status="paused",
                    reason="Fundamental processing is paused",
                )
                await _set_run(analysis_run_id, status="cancelled", completed=True, error="Fundamental processing is paused")
                return {"status": "cancelled", "scan_run_id": scan_run_id}
            survivors = await _load_survivors(scan_run_id)
            await _seed_items(analysis_run_id, survivors)
            await _set_run(analysis_run_id, status="running")
            llm_client = OpenRouterFundamentalClient(
                api_key=settings.openrouter_api_key,
                api_url=settings.openrouter_api_url,
                model=settings.openrouter_model,
                reasoning_effort=settings.openrouter_reasoning_effort,
                prompt_version=settings.openrouter_prompt_version,
                app_title=settings.openrouter_app_title,
                http_referer=settings.openrouter_http_referer,
                timeout_seconds=settings.openrouter_http_timeout_seconds,
                max_attempts=2,
                max_tokens=settings.openrouter_max_tokens,
                prompt_max_chars=settings.fundamental_prompt_max_chars,
                temperature=settings.openrouter_temperature,
            )
            outcomes: list[str] = []
            async with UpstoxFundamentalsClient(analytics_token=settings.upstox_analytics_token, base_url=settings.upstox_fundamentals_base_url, timeout_seconds=settings.fundamentals_http_timeout_seconds, max_attempts=settings.fundamentals_http_max_attempts) as client:
                for survivor in survivors:
                    outcome = await _process_survivor(
                        analysis_run_id,
                        survivor,
                        client,
                        llm_client,
                        force_refresh=mode == "refresh_stale",
                    )
                    outcomes.append(outcome)
                    if outcome == "cancelled" or await _processing_paused():
                        break
            final_status = "cancelled" if await _processing_paused() else ("partial" if any(item not in {"succeeded", "cached", "rules_only"} for item in outcomes) else "succeeded")
            if final_status == "cancelled":
                await _finish_unprocessed_results(
                    scan_run_id,
                    llm_status="skipped",
                    ai_status="paused",
                    reason="Fundamental processing was paused before completion",
                )
            await _set_run(analysis_run_id, status=final_status, completed=True)
            return {"status": final_status, "scan_run_id": scan_run_id, "analysis_run_id": str(analysis_run_id), "outcomes": {item: outcomes.count(item) for item in set(outcomes)}}
        except Exception as exc:
            logger.exception("P7 fundamental pass crashed for scan %s", scan_run_id)
            await _finish_unprocessed_results(
                scan_run_id,
                llm_status="failed",
                ai_status="failed",
                reason=str(exc)[:500],
            )
            await _set_run(analysis_run_id, status="failed", completed=True, error=str(exc)[:500])
            return {"status": "failed", "scan_run_id": scan_run_id, "error": str(exc)[:500]}
        finally:
            await lock_db.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": GLOBAL_P7_LOCK})
