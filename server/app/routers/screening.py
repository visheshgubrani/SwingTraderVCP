import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.screening_config import TechnicalScreeningConfig

router = APIRouter(prefix="/screening", tags=["screening"])

@router.post("/scan")
async def trigger_scan(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    config: TechnicalScreeningConfig | None = None,
):
    """
    Creates a new technical scan run record and enqueues the screening job on Redis arq.
    """
    # 1. Create a scan run entry in postgres
    scan_run_id = uuid.uuid4()
    
    effective_config = config or TechnicalScreeningConfig()
    insert_run_query = text("""
        INSERT INTO scan_runs (id, universe_code, status, triggered_by, technical_config)
        VALUES (:id, 'NIFTY500', 'queued', 'manual', CAST(:technical_config AS jsonb))
    """)
    
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
            detail="Redis background queue connection not initialized on the server."
        )
        
    try:
        await redis_pool.enqueue_job('run_technical_scan', str(scan_run_id))
    except Exception as e:
        # Update scan run to failed if enqueuing fails
        fail_query = text("""
            UPDATE scan_runs
            SET status = 'failed', completed_at = now(), error_message = :error
            WHERE id = :scan_run_id
        """)
        await db.execute(fail_query, {"scan_run_id": scan_run_id, "error": f"Enqueuing failed: {str(e)}"})
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to enqueue scan job: {str(e)}"
        )
        
    return {
        "status": "queued",
        "scan_run_id": str(scan_run_id),
        "message": "Technical screening job enqueued successfully."
    }

@router.get("/runs")
async def list_scan_runs(db: Annotated[AsyncSession, Depends(get_db)]):
    """
    Retrieves the execution history of past screening runs.
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
        {
            "id": str(run.id),
            "universe_code": run.universe_code,
            "status": run.status,
            "triggered_by": run.triggered_by,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "error_message": run.error_message,
            "technical_config": run.technical_config,
            "created_at": run.created_at.isoformat(),
            "passing_count": run.passing_count
        }
        for run in runs
    ]

@router.get("/runs/{run_id}/results")
async def get_scan_results(
    run_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Retrieves the list of symbols that passed the technical screening run.
    Returns the persisted RS-descending shortlist order for manual VCP review.
    """
    query = text("""
        SELECT 
            s.id, 
            s.result_rank,
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
    
    results = []
    for fallback_rank, row in enumerate(rows, start=1):
        tech_metrics = row.technical_metrics or {}
        
        results.append({
            "rank": row.result_rank or fallback_rank,
            "id": str(row.id),
            "symbol": row.symbol,
            "name": row.name,
            "fyers_symbol": row.fyers_symbol,
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
            "bb_width": float(tech_metrics.get("bb_width", 0.0)),
            "bb_width_20th_pct": float(tech_metrics.get("bb_width_20th_pct", 0.0)),
            "avg_volume_10": int(tech_metrics.get("avg_volume_10", 0)),
            "avg_volume_50": int(tech_metrics.get("avg_volume_50", 0)),
            "volume_dry_up_ratio": float(tech_metrics.get("volume_dry_up_ratio", 0.0)),
            "criteria_matches": tech_metrics.get("criteria_matches", {}),
            "llm_status": row.llm_status,
            "llm_verdict": row.llm_verdict,
            "llm_flags": row.llm_flags or {},
            "llm_checked_at": (
                row.llm_checked_at.isoformat() if row.llm_checked_at else None
            ),
            "fundamental_snapshot_id": (
                str(row.fundamental_snapshot_id)
                if row.fundamental_snapshot_id
                else None
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
            "reviewer_status": row.reviewer_status
        })
        
    return results
