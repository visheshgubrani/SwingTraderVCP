from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any
from app.database import get_db
import uuid

router = APIRouter(prefix="/screening", tags=["screening"])

@router.post("/scan")
async def trigger_scan(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Creates a new technical scan run record and enqueues the screening job on Redis arq.
    """
    # 1. Create a scan run entry in postgres
    scan_run_id = uuid.uuid4()
    
    insert_run_query = text("""
        INSERT INTO scan_runs (id, universe_code, status, triggered_by, technical_config)
        VALUES (:id, 'NIFTY500', 'queued', 'manual', '{}'::jsonb)
    """)
    
    await db.execute(insert_run_query, {"id": scan_run_id})
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
async def list_scan_runs(db: AsyncSession = Depends(get_db)):
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
            "created_at": run.created_at.isoformat(),
            "passing_count": run.passing_count
        }
        for run in runs
    ]

@router.get("/runs/{run_id}/results")
async def get_scan_results(run_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retrieves the list of symbols that passed the technical screening run.
    Dynamically sorts results at read-time by closeness to 52-week high.
    """
    query = text("""
        SELECT 
            s.id, 
            s.close_price, 
            s.sma_50, 
            s.sma_200, 
            s.avg_volume_20, 
            s.pct_from_52w_high, 
            s.technical_metrics,
            s.llm_status,
            s.llm_verdict,
            s.reviewer_status,
            i.symbol, 
            i.name, 
            i.fyers_symbol
        FROM screening_results s
        JOIN instruments i ON s.instrument_id = i.id
        WHERE s.scan_run_id = :run_id
        ORDER BY s.pct_from_52w_high ASC
    """)
    
    res = await db.execute(query, {"run_id": run_id})
    rows = res.all()
    
    results = []
    for rank, row in enumerate(rows, start=1):
        tech_metrics = row.technical_metrics or {}
        
        results.append({
            "rank": rank,
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
            "criteria_matches": tech_metrics.get("criteria_matches", {}),
            "llm_status": row.llm_status,
            "llm_verdict": row.llm_verdict,
            "reviewer_status": row.reviewer_status
        })
        
    return results
