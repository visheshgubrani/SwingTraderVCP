import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.domain.p9_sector_taxonomy import sector_for_industry
from app.schemas.screening import (
    FundamentalDetailResponse,
    FundamentalPassProgressResponse,
    FundamentalPassRequest,
    FundamentalTraceResponse,
    ScanResultResponse,
    ScanRunResponse,
    ScannerDiagnosticsResponse,
    ScanTriggerResponse,
)
from app.services.fundamental_controls import is_fundamental_control_paused
from app.services.fundamental_data import (
    FundamentalsDataContractError,
    upstox_endpoint_manifest,
    validate_upstox_bundle,
)
from app.services.fundamental_llm import sanitize_provider_payload
from app.services.fundamental_pass import (
    ensure_fundamental_survivors_selected,
    p7_run_config,
)
from app.services.fundamental_rules import unresolved_scorecard_evidence
from app.services.personal_scan import ensure_personal_scan
from app.services.screening_config import TechnicalScreeningConfig
from app.services.screening_diagnostics import build_scanner_diagnostics

router = APIRouter(prefix="/screening", tags=["screening"])
logger = logging.getLogger(__name__)


def _fundamental_assessment(scorecard: object) -> dict | None:
    """Return only the public v3 assessment fields from persisted JSONB."""
    if not isinstance(scorecard, dict) or not isinstance(scorecard.get("grade"), str):
        return None
    return scorecard


def _sector_code_from_metrics(tech_metrics: dict[str, Any]) -> str | None:
    stored = tech_metrics.get("sector_code")
    if isinstance(stored, str) and stored.strip():
        return stored
    mapped = sector_for_industry(tech_metrics.get("industry"))
    return mapped.code if mapped is not None else None


@router.get(
    "/results/{result_id}/fundamentals",
    response_model=FundamentalDetailResponse,
)
async def get_fundamental_detail(
    result_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FundamentalDetailResponse:
    """Return authoritative fundamentals and the separate AI second opinion."""

    result = await db.execute(
        text(
            """
            SELECT
                s.id,
                s.scan_run_id,
                s.llm_status,
                s.llm_verdict,
                s.llm_flags,
                s.llm_checked_at,
                s.fundamental_status,
                s.fundamental_verdict,
                s.fundamental_scorecard,
                s.ai_status,
                i.symbol,
                i.name,
                i.fyers_symbol,
                f.id AS snapshot_id,
                f.provider,
                f.statement_type,
                f.fetched_at,
                f.latest_annual_period,
                f.latest_quarterly_period,
                f.normalized_facts
            FROM screening_results s
            JOIN instruments i ON i.id = s.instrument_id
            LEFT JOIN fundamental_snapshots f ON f.id = s.fundamental_snapshot_id
            WHERE s.id = :result_id
            """
        ),
        {"result_id": result_id},
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Screening result not found")

    flags = dict(row.llm_flags or {})
    persisted_opinion = (
        flags.get("ai_opinion")
        if isinstance(flags.get("ai_opinion"), dict)
        else None
    )
    snapshot = None
    if row.snapshot_id is not None:
        snapshot = {
            "id": row.snapshot_id,
            "provider": row.provider,
            "statement_type": row.statement_type,
            "fetched_at": row.fetched_at,
            "latest_annual_period": row.latest_annual_period,
            "latest_quarterly_period": row.latest_quarterly_period,
            "normalized_facts": dict(row.normalized_facts or {}),
        }

    return FundamentalDetailResponse.model_validate(
        {
            "result_id": row.id,
            "scan_run_id": row.scan_run_id,
            "instrument": {
                "symbol": row.symbol,
                "name": row.name,
                "fyers_symbol": row.fyers_symbol,
            },
            "fundamental": {
                "status": row.fundamental_status,
                "assessment": _fundamental_assessment(
                    row.fundamental_scorecard
                    or flags.get("assessment")
                    or flags.get("rules", {})
                ),
                "scorecard": row.fundamental_scorecard
                or flags.get("rules", {}),
                "missing_data": flags.get("missing_data") or [],
                "provider_limitations": flags.get("provider_limitations") or [],
                "error": None,
            },
            "ai_opinion": {
                "status": row.ai_status,
                "verdict": row.llm_verdict,
                "checked_at": row.llm_checked_at,
                "summary": (
                    persisted_opinion.get("summary")
                    if persisted_opinion
                    else flags.get("summary")
                    if flags.get("schema_version") != "fundamental_result_v4"
                    else None
                ),
                "verdict_reference_ids": (
                    persisted_opinion or {}
                ).get("verdict_reference_ids", []),
                "strengths": flags.get("strengths") or [],
                "risks": flags.get("risks") or [],
                "review_focus": flags.get("review_focus") or [],
                "skip_reason": flags.get("ai_skip_reason"),
                "error": None,
                "model": flags.get("model"),
            },
            "snapshot": snapshot,
            "risk_checks": flags.get("risk_checks") or {},
            "source_snapshots": flags.get("source_snapshots") or [],
        }
    )


@router.get(
    "/results/{result_id}/fundamentals/trace",
    response_model=FundamentalTraceResponse,
)
async def get_fundamental_trace(
    result_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FundamentalTraceResponse:
    """Return the lazy, sanitized P7 source-to-model audit trail."""

    result = await db.execute(
        text(
            """
            SELECT s.id, s.fundamental_scorecard, s.llm_flags,
                   s.fundamental_status, s.ai_status, i.isin,
                   f.id AS snapshot_id, f.provider, f.statement_type,
                   f.fetched_at, f.content_hash, f.raw_payload,
                   f.normalized_facts,
                   item.id AS analysis_item_id, item.analysis_key
            FROM screening_results s
            JOIN instruments i ON i.id = s.instrument_id
            LEFT JOIN fundamental_snapshots f ON f.id = s.fundamental_snapshot_id
            LEFT JOIN LATERAL (
                SELECT id, analysis_key
                FROM fundamental_analysis_items
                WHERE screening_result_id = s.id
                ORDER BY created_at DESC
                LIMIT 1
            ) item ON true
            WHERE s.id = :result_id
            """
        ),
        {"result_id": result_id},
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Screening result not found")

    attempts: list[dict] = []
    if row.analysis_item_id is not None:
        attempt_result = await db.execute(
            text(
                """
                SELECT attempt.*
                FROM fundamental_ai_attempts attempt
                WHERE attempt.analysis_item_id = :analysis_item_id
                   OR attempt.id = (
                        SELECT annotation.source_attempt_id
                        FROM fundamental_annotations annotation
                        WHERE annotation.analysis_key = :analysis_key
                   )
                ORDER BY attempt.started_at, attempt.attempt_number
                """
            ),
            {
                "analysis_item_id": row.analysis_item_id,
                "analysis_key": row.analysis_key,
            },
        )
        for attempt in attempt_result.mappings():
            item = dict(attempt)
            item["request_payload"] = sanitize_provider_payload(
                dict(item.get("request_payload") or {})
            )
            response_payload = item.get("response_payload")
            item["response_payload"] = (
                sanitize_provider_payload(dict(response_payload))
                if isinstance(response_payload, dict)
                else None
            )
            item["usage"] = sanitize_provider_payload(dict(item.get("usage") or {}))
            item["cost"] = float(item.get("cost") or 0)
            attempts.append(item)

    facts = dict(row.normalized_facts or {})
    scorecard = dict(row.fundamental_scorecard or {})
    flags = dict(row.llm_flags or {})
    unresolved = unresolved_scorecard_evidence(facts, scorecard)
    latest_request = attempts[-1]["request_payload"] if attempts else None
    raw_payload = dict(row.raw_payload or {}) if row.snapshot_id else None
    source_contract_valid: bool | None = None
    source_contract_error: str | None = None
    if raw_payload is not None:
        try:
            validate_upstox_bundle(raw_payload)
            source_contract_valid = True
        except FundamentalsDataContractError as exc:
            source_contract_valid = False
            source_contract_error = str(exc)[:1000]
    return FundamentalTraceResponse.model_validate(
        {
            "result_id": row.id,
            "source": {
                "snapshot_id": row.snapshot_id,
                "provider": row.provider,
                "statement_type": row.statement_type,
                "fetched_at": row.fetched_at,
                "content_hash": row.content_hash,
                "endpoint_manifest": (
                    upstox_endpoint_manifest(
                        row.isin,
                        statement_type=row.statement_type or "consolidated",
                    )
                    if row.isin
                    else []
                ),
                "raw_payload": sanitize_provider_payload(raw_payload),
                "contract_valid": source_contract_valid,
                "contract_error": source_contract_error,
            },
            "normalized": {
                "schema_version": facts.get("schema_version"),
                "facts": facts,
            },
            "python_fit": {
                "rubric_version": scorecard.get("rubric_version"),
                "scorecard": scorecard,
                "contract_valid": not unresolved,
                "unresolved_reference_ids": unresolved,
            },
            "ai_request": latest_request,
            "ai_attempts": attempts,
            "legacy_response_captured": bool(attempts),
            "pipeline_errors": sanitize_provider_payload(
                {
                    "fundamental": flags.get("fundamental_error")
                    or (flags.get("error") if row.fundamental_status == "failed" else None),
                    "ai": flags.get("ai_error")
                    or (flags.get("error") if row.ai_status == "failed" else None),
                }
            ),
        }
    )


@router.post("/scan", response_model=ScanTriggerResponse)
async def trigger_scan(
    request: Request,
    config: TechnicalScreeningConfig | None = None,
) -> ScanTriggerResponse:
    """Create or reuse today's versioned personal technical scan."""
    redis_pool = getattr(request.app.state, "redis", None)
    if not redis_pool:
        raise HTTPException(
            status_code=500,
            detail="Redis background queue connection not initialized on the server.",
        )

    try:
        run = await ensure_personal_scan(
            redis_pool,
            config=config,
            triggered_by="manual",
        )
    except Exception as enqueue_error:
        logger.exception("Could not ensure the personal EOD scan")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create or attach to the EOD scan: {enqueue_error}",
        ) from enqueue_error

    return ScanTriggerResponse(
        status=run.status,
        scan_run_id=run.scan_run_id,
        reused=run.reused,
        as_of_date=run.as_of_date,
        message=(
            "Opened the existing EOD scan."
            if run.reused
            else "Technical scoring job enqueued successfully."
        ),
    )


@router.get("/runs", response_model=list[ScanRunResponse])
async def list_scan_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ScanRunResponse]:
    """Return recent personal screening execution history.

    Historical technical-only version-comparison shadow runs are always
    hidden from the UI; their persisted rows are retained for audit.
    """
    query = text("""
        SELECT 
            r.id, 
            r.universe_code, 
            r.status, 
            r.triggered_by, 
            r.started_at, 
            r.completed_at, 
            r.error_message, 
            r.technical_config,
            r.as_of_date,
            r.created_at,
            COUNT(s.id) as passing_count
        FROM scan_runs r
        LEFT JOIN screening_results s ON r.id = s.scan_run_id
        WHERE r.visibility = 'personal'
          AND r.triggered_by <> 'manual_shadow'
        GROUP BY r.id
        ORDER BY r.created_at DESC
        LIMIT 50
    """)
    
    res = await db.execute(query)
    runs = res.all()
    
    return [
        ScanRunResponse.model_validate(
            {
                "id": run.id,
                "universe_code": run.universe_code,
                "status": run.status,
                "triggered_by": run.triggered_by,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "error_message": run.error_message,
                "technical_config": run.technical_config,
                "as_of_date": run.as_of_date,
                "created_at": run.created_at,
                "passing_count": run.passing_count,
            }
        )
        for run in runs
    ]


@router.post(
    "/runs/{run_id}/fundamental-pass",
    response_model=ScanTriggerResponse,
)
async def trigger_fundamental_pass(
    run_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: FundamentalPassRequest | None = None,
) -> ScanTriggerResponse:
    """Queue only the selected top-20 incomplete/stale P7 results, once per scan."""
    run_query = text("SELECT id FROM scan_runs WHERE id = :run_id")
    res = await db.execute(run_query, {"run_id": run_id})
    if res.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Scan run not found")

    if await is_fundamental_control_paused(db, "processing"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Fundamental processing is paused. Resume processing before "
                "queueing another pass."
            ),
        )

    active = await db.execute(
        text("""SELECT id FROM fundamental_analysis_runs WHERE scan_run_id = :run_id
                 AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1"""),
        {"run_id": run_id},
    )
    if active.scalar_one_or_none() is not None:
        return ScanTriggerResponse(status="queued", scan_run_id=run_id, message="Fundamental pass is already queued or running.")

    reset_query = text(
        """
        UPDATE screening_results
        SET
            fundamental_status = 'queued',
            ai_status = 'queued',
            llm_status = 'queued'
        WHERE
            scan_run_id = :run_id
            AND technical_passed = true
            AND COALESCE((technical_metrics ->> 'fundamental_selected')::boolean, false) = true
            AND (
                :mode = 'refresh_stale'
                OR fundamental_status IN ('failed', 'skipped', 'not_requested', 'queued')
                OR ai_status IN ('failed', 'skipped', 'not_requested', 'paused', 'budget_exhausted', 'queued')
                OR llm_status IN ('failed', 'skipped', 'not_requested', 'queued')
            )
        """
    )
    mode = payload.mode if payload else "retry_incomplete"
    analysis_run_id = uuid.uuid4()
    queue_job_id = f"fundamental-pass:{run_id}:{analysis_run_id}"

    reset_res = await db.execute(reset_query, {"run_id": run_id, "mode": mode})
    if getattr(reset_res, "rowcount", None) == 0:
        await ensure_fundamental_survivors_selected(run_id, db=db)
        await db.execute(reset_query, {"run_id": run_id, "mode": mode})
    await db.execute(
        text(
            """
            INSERT INTO fundamental_analysis_runs (
                id,
                scan_run_id,
                status,
                mode,
                queue_job_id,
                config
            )
            VALUES (
                :analysis_run_id,
                :run_id,
                'queued',
                :mode,
                :queue_job_id,
                CAST(:config AS jsonb)
            )
            """
        ),
        {
            "analysis_run_id": analysis_run_id,
            "run_id": run_id,
            "mode": mode,
            "queue_job_id": queue_job_id,
            "config": json.dumps(p7_run_config()),
        },
    )
    await db.commit()

    redis_pool = getattr(request.app.state, "redis", None)
    if not redis_pool:
        raise HTTPException(
            status_code=500,
            detail="Redis background queue connection not initialized on the server.",
        )

    try:
        queued_job = await redis_pool.enqueue_job(
            "run_fundamental_pass",
            str(run_id),
            mode,
            _job_id=queue_job_id,
            _job_timeout=1800,
        )
        if queued_job is None:
            raise RuntimeError("Redis rejected the duplicate fundamentals queue job ID.")
    except Exception as enqueue_error:
        await db.execute(
            text(
                """UPDATE fundamental_analysis_runs SET status = 'failed', error_message = :error,
                   completed_at = now() WHERE id = :analysis_run_id AND status = 'queued'"""
            ),
            {
                "analysis_run_id": analysis_run_id,
                "error": str(enqueue_error)[:500],
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"Failed to enqueue fundamental pass job: {enqueue_error}",
        ) from enqueue_error

    return ScanTriggerResponse(
        status="queued",
        scan_run_id=run_id,
        message="Fundamental rules and AI explanation pass enqueued successfully.",
    )


@router.get(
    "/runs/{run_id}/fundamental-pass",
    response_model=FundamentalPassProgressResponse | None,
)
async def get_fundamental_pass_progress(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FundamentalPassProgressResponse | None:
    result = await db.execute(
        text(
            """
            SELECT
                r.id AS analysis_run_id,
                r.scan_run_id,
                r.status,
                r.current_rank,
                r.current_symbol,
                r.provider_requests,
                r.input_tokens,
                r.reasoning_tokens,
                r.output_tokens,
                r.cached_tokens,
                r.total_cost,
                r.error_message,
                r.heartbeat_at,
                COALESCE(items.counts, '{}'::jsonb) AS counts
            FROM fundamental_analysis_runs r
            LEFT JOIN LATERAL (
                SELECT jsonb_object_agg(status, count) AS counts
                FROM (SELECT status, count(*)::int AS count FROM fundamental_analysis_items
                      WHERE analysis_run_id = r.id GROUP BY status) item_counts
            ) items ON true
            WHERE r.scan_run_id = :run_id
            ORDER BY r.created_at DESC LIMIT 1
            """
        ),
        {"run_id": run_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return FundamentalPassProgressResponse.model_validate(
        {**dict(row), "token_budget": settings.fundamental_run_token_budget}
    )


@router.get("/runs/{run_id}/results", response_model=list[ScanResultResponse])
async def get_scan_results(
    run_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ScanResultResponse]:
    """Return the persisted score-ranked setups for manual VCP review."""
    personal_run = await db.execute(
        text(
            """
            SELECT 1
            FROM scan_runs
            WHERE id = :run_id AND visibility = 'personal'
            """
        ),
        {"run_id": run_id},
    )
    if personal_run.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Personal scan run not found")

    query = text("""
        SELECT 
            s.id, 
            s.result_rank,
            s.technical_score,
            s.close_price, 
            s.sma_50, 
            s.sma_200, 
            s.avg_volume_20, 
            s.pct_from_52w_high, 
            s.technical_metrics,
            s.llm_status,
            s.llm_verdict,
            s.llm_flags,
            s.llm_checked_at,
            s.fundamental_status,
            s.fundamental_verdict,
            s.fundamental_scorecard,
            s.ai_status,
            s.fundamental_snapshot_id,
            s.reviewer_status,
            f.provider AS fundamentals_provider,
            f.statement_type AS fundamentals_statement_type,
            f.fetched_at AS fundamentals_fetched_at,
            f.latest_annual_period,
            f.latest_quarterly_period,
            i.symbol, 
            i.name, 
            i.fyers_symbol,
            v.id AS vcp_vision_id,
            v.status AS vcp_vision_status,
            v.ai_verdict AS vcp_vision_ai_verdict,
            v.human_verdict AS vcp_vision_human_verdict,
            v.created_at AS vcp_vision_created_at,
            v.error_code AS vcp_vision_error_code
        FROM screening_results s
        JOIN instruments i ON s.instrument_id = i.id
        LEFT JOIN fundamental_snapshots f ON f.id = s.fundamental_snapshot_id
        LEFT JOIN LATERAL (
            SELECT v.id, v.status, v.ai_verdict, v.human_verdict,
                   v.created_at, v.error_code
            FROM vcp_visual_analyses v
            WHERE v.screening_result_id = s.id
            ORDER BY v.created_at DESC
            LIMIT 1
        ) v ON true
        WHERE s.scan_run_id = :run_id
        ORDER BY s.result_rank ASC NULLS LAST, s.pct_from_52w_high ASC
    """)
    
    res = await db.execute(query, {"run_id": run_id})
    rows = res.all()
    
    results: list[ScanResultResponse] = []
    for fallback_rank, row in enumerate(rows, start=1):
        tech_metrics = row.technical_metrics or {}
        score_detail = tech_metrics.get("score") or {}
        technical_score = (
            float(row.technical_score) if row.technical_score is not None else None
        )
        score_grade = (
            score_detail.get("grade") if technical_score is not None else None
        )

        results.append(ScanResultResponse.model_validate({
            "rank": row.result_rank or fallback_rank,
            "id": row.id,
            "symbol": row.symbol,
            "name": row.name,
            "fyers_symbol": row.fyers_symbol,
            "technical_score": technical_score,
            "score_version": score_detail.get("version"),
            "score_grade": score_grade,
            "score_components": score_detail.get("components") or {},
            "eligibility": tech_metrics.get("eligibility") or {},
            "core_checks": tech_metrics.get("core_checks") or {},
            "close_price": float(row.close_price),
            "sma_50": float(row.sma_50),
            "sma_200": float(row.sma_200),
            "sma_150": float(tech_metrics.get("sma_150", 0.0)),
            "sma_200_yesterday": float(tech_metrics.get("sma_200_yesterday", 0.0)),
            "sma_200_prev_22": float(tech_metrics.get("sma_200_prev_22", 0.0)) if tech_metrics.get("sma_200_prev_22") is not None else None,
            "sma_200_prev_110": float(tech_metrics.get("sma_200_prev_110", 0.0)) if tech_metrics.get("sma_200_prev_110") is not None else None,
            "high_52w": float(tech_metrics.get("high_52w", 0.0)),
            "low_52w": float(tech_metrics.get("low_52w", 0.0)),
            "avg_volume_20": int(row.avg_volume_20 or 0),
            "pct_from_52w_high": float(row.pct_from_52w_high or 0.0),
            "rs_rating": int(tech_metrics.get("rs_rating", 0)),
            "adtv_crore": float(tech_metrics.get("adtv_crore", 0.0)),
            "atr_10": float(tech_metrics.get("atr_10", 0.0)),
            "atr_50": float(tech_metrics.get("atr_50", 0.0)),
            "atr_ratio": float(tech_metrics.get("atr_ratio", 0.0)),
            "atr_ratio_3m_low": float(tech_metrics.get("atr_ratio_3m_low", 0.0)),
            "atr_proximity_factor": (
                float(tech_metrics["atr_proximity_factor"])
                if tech_metrics.get("atr_proximity_factor") is not None
                else None
            ),
            "bb_width": float(tech_metrics.get("bb_width", 0.0)),
            "bb_width_20th_pct": float(tech_metrics.get("bb_width_20th_pct", 0.0)),
            "bb_width_percentile": (
                float(tech_metrics["bb_width_percentile"])
                if tech_metrics.get("bb_width_percentile") is not None
                else None
            ),
            "avg_volume_10": int(tech_metrics.get("avg_volume_10", 0)),
            "avg_volume_50": int(tech_metrics.get("avg_volume_50", 0)),
            "volume_dry_up_ratio": float(tech_metrics.get("volume_dry_up_ratio", 0.0)),
            "up_down_volume_ratio": (
                float(tech_metrics["up_down_volume_ratio"])
                if tech_metrics.get("up_down_volume_ratio") is not None
                else None
            ),
            "pocket_pivot_age": (
                float(tech_metrics["pocket_pivot_age"])
                if tech_metrics.get("pocket_pivot_age") is not None
                else None
            ),
            "rs_line": (
                float(tech_metrics["rs_line"])
                if tech_metrics.get("rs_line") is not None
                else None
            ),
            "rs_line_high_52w": (
                float(tech_metrics["rs_line_high_52w"])
                if tech_metrics.get("rs_line_high_52w") is not None
                else None
            ),
            "rs_line_pct_off_high": (
                float(tech_metrics["rs_line_pct_off_high"])
                if tech_metrics.get("rs_line_pct_off_high") is not None
                else None
            ),
            "rs_benchmark_symbol": tech_metrics.get("rs_benchmark_symbol"),
            "rs_benchmark_source": tech_metrics.get("rs_benchmark_source"),
            "criteria_matches": tech_metrics.get("criteria_matches", {}),
            "fundamental_selected": bool(
                tech_metrics.get(
                    "fundamental_selected",
                    row.llm_status != "not_requested",
                )
            ),
            "fundamental_selection_rank": tech_metrics.get("fundamental_selection_rank"),
            "industry": tech_metrics.get("industry"),
            "industry_key": tech_metrics.get("industry_key"),
            "fundamental_cap_exclusion_reason": tech_metrics.get("fundamental_cap_exclusion_reason"),
            "market_context_mode": tech_metrics.get("market_context_mode"),
            "sector_code": _sector_code_from_metrics(tech_metrics),
            "sector_tier": tech_metrics.get("sector_tier", "unavailable"),
            "sector_gate_tier": tech_metrics.get("sector_gate_tier", "unavailable"),
            "sector_rs_rating": tech_metrics.get("sector_rs_rating"),
            "contextual_selection_rank": tech_metrics.get("contextual_selection_rank"),
            "p9_would_fundamental_select": bool(
                tech_metrics.get("p9_would_fundamental_select", False)
            ),
            "p9_would_exclusion_reason": tech_metrics.get("p9_would_exclusion_reason"),
            "risk_checks": (row.llm_flags or {}).get("risk_checks") or {},
            "source_snapshots": (row.llm_flags or {}).get("source_snapshots") or [],
            "llm_status": row.llm_status,
            "llm_verdict": row.llm_verdict,
            "llm_flags": row.llm_flags or {},
            "llm_checked_at": (
                row.llm_checked_at.isoformat() if row.llm_checked_at else None
            ),
            "fundamental_status": getattr(row, "fundamental_status", "not_requested"),
            "fundamental_verdict": getattr(row, "fundamental_verdict", None),
            "fundamental_scorecard": getattr(row, "fundamental_scorecard", {}) or {},
            "fundamental_assessment": _fundamental_assessment(getattr(row, "fundamental_scorecard", {}) or (row.llm_flags or {}).get("assessment", {})),
            "ai_status": getattr(row, "ai_status", "not_requested"),
            "fundamental_snapshot_id": (
                row.fundamental_snapshot_id if row.fundamental_snapshot_id else None
            ),
            "fundamentals_provenance": (
                {
                    "provider": row.fundamentals_provider,
                    "statement_type": row.fundamentals_statement_type,
                    "fetched_at": row.fundamentals_fetched_at.isoformat(),
                    "latest_annual_period": row.latest_annual_period,
                    "latest_quarterly_period": row.latest_quarterly_period,
                }
                if row.fundamental_snapshot_id
                else None
            ),
            "reviewer_status": row.reviewer_status,
            "vcp_vision": (
                {
                    "id": row.vcp_vision_id,
                    "status": row.vcp_vision_status,
                    "ai_verdict": row.vcp_vision_ai_verdict,
                    "human_verdict": row.vcp_vision_human_verdict,
                    "created_at": row.vcp_vision_created_at,
                    "error_code": row.vcp_vision_error_code,
                }
                if row.vcp_vision_id
                else None
            ),
        }))

    return results


@router.get(
    "/runs/{run_id}/diagnostics",
    response_model=ScannerDiagnosticsResponse,
)
async def get_scan_diagnostics(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScannerDiagnosticsResponse:
    """Return reproducible score, concentration, and XBRL diagnostics."""
    run_result = await db.execute(
        text(
            """
            SELECT id, as_of_date, technical_config
            FROM scan_runs
            WHERE id = :run_id AND visibility = 'personal' AND status = 'succeeded'
            """
        ),
        {"run_id": run_id},
    )
    run = run_result.mappings().one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Succeeded personal scan run not found")
    score_version = dict(run["technical_config"] or {}).get("pipeline_version")

    rows_result = await db.execute(
        text(
            """
            SELECT s.result_rank, s.technical_score, s.pct_from_52w_high,
                   s.technical_metrics, i.symbol
            FROM screening_results s
            JOIN instruments i ON i.id = s.instrument_id
            WHERE s.scan_run_id = :run_id
            ORDER BY s.result_rank ASC NULLS LAST, i.symbol ASC
            """
        ),
        {"run_id": run_id},
    )
    rows = [dict(row) for row in rows_result.mappings()]

    coverage_result = await db.execute(
        text(
            """
            SELECT
                COUNT(link.snapshot_id)::int AS total,
                COUNT(*) FILTER (WHERE f.fetch_status = 'ambiguous')::int AS ambiguous,
                COUNT(link.snapshot_id) FILTER (
                    WHERE f.taxonomy_version IS NULL
                )::int AS unknown_taxonomy,
                GREATEST(
                    COUNT(DISTINCT s.id) * 2 - COUNT(link.snapshot_id), 0
                )::int AS missing
            FROM screening_results s
            LEFT JOIN screening_result_fundamental_snapshots link
              ON link.screening_result_id = s.id
             AND link.role IN ('promoter_pledge', 'leverage')
            LEFT JOIN fundamental_snapshots f ON f.id = link.snapshot_id
            WHERE s.scan_run_id = :run_id
              AND COALESCE((s.technical_metrics ->> 'fundamental_selected')::boolean, false)
            """
        ),
        {"run_id": run_id},
    )
    coverage = dict(coverage_result.mappings().one())
    return ScannerDiagnosticsResponse(
        scan_run_id=run_id,
        score_version=score_version,
        diagnostics=build_scanner_diagnostics(
            rows,
            xbrl_counts=coverage,
        ),
    )
