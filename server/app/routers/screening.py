import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.screening import (
    FundamentalDetailResponse,
    FundamentalPassProgressResponse,
    FundamentalPassRequest,
    ScanResultResponse,
    ScanRunResponse,
    ScanTriggerResponse,
)
from app.services.screening_config import TechnicalScreeningConfig
from app.services.fundamental_pass import p7_run_config

router = APIRouter(prefix="/screening", tags=["screening"])


def _fundamental_assessment(scorecard: object) -> dict | None:
    """Return only the public v3 assessment fields from persisted JSONB."""
    if not isinstance(scorecard, dict) or not isinstance(scorecard.get("grade"), str):
        return None
    return scorecard


@router.get(
    "/results/{result_id}/fundamentals",
    response_model=FundamentalDetailResponse,
)
async def get_fundamental_detail(
    result_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FundamentalDetailResponse:
    """Return normalized fundamentals and safe AI annotation metadata."""

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
            "annotation": {
                "status": row.llm_status,
                "verdict": row.llm_verdict,
                "checked_at": row.llm_checked_at,
                "summary": flags.get("summary"),
                "criteria": flags.get("criteria") or [],
                "red_flags": flags.get("red_flags") or [],
                "missing_data": flags.get("missing_data") or [],
                "error": flags.get("error"),
                "model": flags.get("model"),
                "rules_verdict": getattr(row, "fundamental_verdict", None) or flags.get("rules", {}).get("verdict"),
                "scorecard": getattr(row, "fundamental_scorecard", None) or flags.get("rules", {}),
                "assessment": _fundamental_assessment(getattr(row, "fundamental_scorecard", None) or flags.get("assessment") or flags.get("rules", {})),
                "provider_limitations": flags.get("provider_limitations", []),
                "ai_status": getattr(row, "ai_status", None),
                "strengths": flags.get("strengths") or flags.get("highlights") or [],
                "risks": flags.get("risks") or [],
                "review_focus": flags.get("review_focus") or [],
                "ai_skip_reason": flags.get("ai_skip_reason"),
            },
            "snapshot": snapshot,
        }
    )


@router.post("/scan", response_model=ScanTriggerResponse)
async def trigger_scan(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    config: TechnicalScreeningConfig | None = None,
) -> ScanTriggerResponse:
    """Create a versioned technical-score run and enqueue it on Redis arq."""
    # 1. Create a scan run entry in postgres
    scan_run_id = uuid.uuid4()

    effective_config = config or TechnicalScreeningConfig()
    insert_run_query = text(
        """
        INSERT INTO scan_runs (
            id,
            universe_code,
            status,
            triggered_by,
            technical_config
        )
        VALUES (
            :id,
            'NIFTY500',
            'queued',
            'manual',
            CAST(:technical_config AS jsonb)
        )
        """
    )

    await db.execute(
        insert_run_query,
        {
            "id": scan_run_id,
            "technical_config": json.dumps(effective_config.model_dump()),
        },
    )
    await db.commit()

    # 2. Enqueue the background job using arq Redis connection pool
    redis_pool = getattr(request.app.state, "redis", None)
    if not redis_pool:
        raise HTTPException(
            status_code=500,
            detail="Redis background queue connection not initialized on the server.",
        )

    try:
        await redis_pool.enqueue_job("run_technical_scan", str(scan_run_id))
    except Exception as enqueue_error:
        # Update scan run to failed if enqueuing fails
        fail_query = text("""
            UPDATE scan_runs
            SET status = 'failed', completed_at = now(), error_message = :error
            WHERE id = :scan_run_id
        """)
        await db.execute(
            fail_query,
            {
                "scan_run_id": scan_run_id,
                "error": f"Enqueuing failed: {enqueue_error}",
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to enqueue scan job: {enqueue_error}",
        ) from enqueue_error

    return ScanTriggerResponse(
        status="queued",
        scan_run_id=scan_run_id,
        message="Technical scoring job enqueued successfully.",
    )


@router.get("/runs", response_model=list[ScanRunResponse])
async def list_scan_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ScanRunResponse]:
    """Return recent screening execution history."""
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
            r.created_at,
            COUNT(s.id) as passing_count
        FROM scan_runs r
        LEFT JOIN screening_results s ON r.id = s.scan_run_id
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
            llm_status = 'queued',
            ai_status = 'queued'
        WHERE
            scan_run_id = :run_id
            AND technical_passed = true
            AND COALESCE((technical_metrics ->> 'fundamental_selected')::boolean, false) = true
            AND (
                :mode = 'refresh_stale'
                OR llm_status IN ('failed', 'skipped', 'not_requested')
            )
        """
    )
    await db.execute(reset_query, {"run_id": run_id, "mode": (payload.mode if payload else "retry_incomplete")})
    await db.execute(
        text(
            """
            INSERT INTO fundamental_analysis_runs (scan_run_id, status, mode, queue_job_id, config)
            VALUES (:run_id, 'queued', :mode, :job_id, CAST(:config AS jsonb))
            """
        ),
        {
            "run_id": run_id,
            "mode": payload.mode if payload else "retry_incomplete",
            "job_id": f"fundamental-pass:{run_id}",
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
        await redis_pool.enqueue_job(
            "run_fundamental_pass",
            str(run_id),
            payload.mode if payload else "retry_incomplete",
            _job_id=f"fundamental-pass:{run_id}",
        )
    except Exception as enqueue_error:
        await db.execute(
            text(
                """UPDATE fundamental_analysis_runs SET status = 'failed', error_message = :error,
                   completed_at = now() WHERE scan_run_id = :run_id AND status = 'queued'"""
            ),
            {"run_id": run_id, "error": str(enqueue_error)[:500]},
        )
        await db.commit()
        raise HTTPException(
            status_code=500,
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
            SELECT r.*, COALESCE(items.counts, '{}'::jsonb) AS counts
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
            i.fyers_symbol
        FROM screening_results s
        JOIN instruments i ON s.instrument_id = i.id
        LEFT JOIN fundamental_snapshots f ON f.id = s.fundamental_snapshot_id
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
            "criteria_matches": tech_metrics.get("criteria_matches", {}),
            "fundamental_selected": bool(
                tech_metrics.get(
                    "fundamental_selected",
                    row.llm_status != "not_requested",
                )
            ),
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
        }))

    return results
