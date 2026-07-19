import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.security import get_fyers_token
from app.services.data_validator import validation_progress, run_data_validation
from app.services.historical_fetcher import (
    SYNC_CANCEL_KEY,
    SyncProgress,
    get_sync_status as get_stored_sync_status,
    save_sync_status,
)

router = APIRouter(prefix="/historical", tags=["historical"])

class SyncRequest(BaseModel):
    backfill_years: int = Field(default=1, ge=1, le=2)


class SyncTriggerResponse(BaseModel):
    status: str
    run_id: str
    message: str

class ValidateRequest(BaseModel):
    years: int = 2

@router.post("/sync")
async def trigger_sync(
    payload: SyncRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SyncTriggerResponse:
    """
    Enqueue an incremental EOD sync. The backfill window is only used for symbols
    that do not have any daily candles yet.
    """
    redis = request.app.state.redis
    current_status = await get_stored_sync_status(redis)
    if current_status["state"] in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail="An EOD sync is already queued or running.",
        )

    token_data = await get_fyers_token(db)
    now = datetime.datetime.now(datetime.timezone.utc)
    if not token_data or token_data["expires_at"] <= now + datetime.timedelta(minutes=5):
        raise HTTPException(
            status_code=401,
            detail="Fyers authentication is required before EOD data can be synced.",
        )

    run_id = str(uuid.uuid4())
    progress = SyncProgress(
        run_id=run_id,
        state="queued",
        triggered_by="manual",
    )
    progress.log("Manual incremental EOD sync queued.")
    await save_sync_status(redis, progress)

    try:
        job = await redis.enqueue_job(
            "run_historical_sync",
            "manual",
            payload.backfill_years,
            run_id,
        )
    except Exception as exc:
        progress.finish("failed", f"Failed to enqueue sync: {exc}")
        await save_sync_status(redis, progress)
        raise HTTPException(status_code=500, detail="Failed to enqueue the EOD sync.") from exc

    if job is None:
        progress.finish("failed", "Redis rejected the duplicate sync job.")
        await save_sync_status(redis, progress)
        raise HTTPException(status_code=409, detail="The EOD sync is already queued.")

    return SyncTriggerResponse(
        status="queued",
        run_id=run_id,
        message="Latest EOD data sync queued.",
    )

@router.get("/status")
async def get_sync_status(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """
    Returns the current sync progress, logs, and database metrics.
    """
    # Query database metrics
    res_candles = await db.execute(
        text("SELECT count(*) FROM market_candles WHERE timeframe = '1d'")
    )
    candles_count = res_candles.scalar() or 0

    res_instruments = await db.execute(text("""
        SELECT count(*) 
        FROM instruments i
        JOIN universe_memberships m ON i.id = m.instrument_id
        WHERE m.universe_code = 'NIFTY500' AND m.member_to IS NULL AND i.active = true
    """))
    nifty500_count = res_instruments.scalar() or 0

    latest_result = await db.execute(
        text(
            """
            WITH per_symbol AS (
                SELECT
                    i.id,
                    (MAX(c.candle_start) AT TIME ZONE 'Asia/Kolkata')::date
                        AS latest_candle_date
                FROM instruments i
                JOIN universe_memberships m ON i.id = m.instrument_id
                LEFT JOIN market_candles c
                    ON c.instrument_id = i.id AND c.timeframe = '1d'
                WHERE m.universe_code = 'NIFTY500'
                  AND m.member_to IS NULL
                  AND i.active = true
                GROUP BY i.id
            ), latest_market AS (
                SELECT MAX(latest_candle_date) AS latest_candle_date
                FROM per_symbol
            )
            SELECT
                latest_market.latest_candle_date,
                COUNT(*) FILTER (
                    WHERE per_symbol.latest_candle_date = latest_market.latest_candle_date
                ) AS symbols_at_latest_date
            FROM per_symbol
            CROSS JOIN latest_market
            GROUP BY latest_market.latest_candle_date
            """
        )
    )
    latest_row = latest_result.one_or_none()

    status = await get_stored_sync_status(request.app.state.redis)
    status["db_metrics"] = {
        "total_candles": candles_count,
        "nifty500_instruments": nifty500_count,
        "latest_candle_date": (
            latest_row.latest_candle_date.isoformat()
            if latest_row and latest_row.latest_candle_date
            else None
        ),
        "symbols_at_latest_date": (
            latest_row.symbols_at_latest_date if latest_row else 0
        ),
    }
    status["schedule"] = {
        "enabled": settings.eod_sync_enabled,
        "weekdays": "Monday-Friday",
        "time": f"{settings.eod_sync_hour:02d}:{settings.eod_sync_minute:02d}",
        "timezone": settings.scheduler_timezone,
    }
    return status

@router.post("/cancel")
async def cancel_sync(request: Request) -> dict[str, str]:
    """
    Cancels the active historical sync.
    """
    status = await get_stored_sync_status(request.app.state.redis)
    if status["state"] not in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail="No active sync run to cancel."
        )
    await request.app.state.redis.set(SYNC_CANCEL_KEY, "1", ex=60 * 60)
    return {"status": "cancelled", "message": "Sync cancellation requested."}

@router.post("/validate")
async def trigger_validation(payload: ValidateRequest, background_tasks: BackgroundTasks):
    """
    Triggers the historical candle validation in the background.
    """
    if validation_progress.is_running:
        raise HTTPException(
            status_code=400,
            detail="Validation is already running. Please wait for the current run to finish or cancel it."
        )
    
    background_tasks.add_task(run_data_validation, years=payload.years)
    return {"status": "started", "message": "Data validation initiated."}

@router.get("/validate/status")
async def get_validation_status():
    """
    Returns the current validation progress, logs, and report.
    """
    return {
        "is_running": validation_progress.is_running,
        "total_symbols": validation_progress.total_symbols,
        "current_index": validation_progress.current_index,
        "current_symbol": validation_progress.current_symbol,
        "errors": validation_progress.errors,
        "logs": validation_progress.logs,
        "started_at": validation_progress.started_at.isoformat() if validation_progress.started_at else None,
        "completed_at": validation_progress.completed_at.isoformat() if validation_progress.completed_at else None,
        "report": validation_progress.report
    }

@router.post("/validate/cancel")
async def cancel_validation():
    """
    Cancels the active validation run.
    """
    if not validation_progress.is_running:
        raise HTTPException(
            status_code=400,
            detail="No active validation run to cancel."
        )
    
    validation_progress.cancel("User cancelled the validation.")
    return {"status": "cancelled", "message": "Validation cancellation requested."}
