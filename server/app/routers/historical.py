from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.historical_fetcher import sync_progress, run_historical_sync
from app.services.data_validator import validation_progress, run_data_validation

router = APIRouter(prefix="/historical", tags=["historical"])

class SyncRequest(BaseModel):
    years: int = 1

class ValidateRequest(BaseModel):
    years: int = 2

@router.post("/sync")
async def trigger_sync(payload: SyncRequest, background_tasks: BackgroundTasks):
    """
    Triggers the 1-year (or specified years) historical candle sync in the background.
    """
    if sync_progress.is_running:
        raise HTTPException(
            status_code=400,
            detail="Sync is already running. Please wait for the current run to finish or cancel it."
        )
    
    # Start the task in background
    background_tasks.add_task(run_historical_sync, years=payload.years)
    return {"status": "started", "message": "Historical data sync initiated."}

@router.get("/status")
async def get_sync_status(db: AsyncSession = Depends(get_db)):
    """
    Returns the current sync progress, logs, and database metrics.
    """
    # Query database metrics
    res_candles = await db.execute(text("SELECT count(*) FROM market_candles"))
    candles_count = res_candles.scalar() or 0

    res_instruments = await db.execute(text("""
        SELECT count(*) 
        FROM instruments i
        JOIN universe_memberships m ON i.id = m.instrument_id
        WHERE m.universe_code = 'NIFTY500' AND m.member_to IS NULL AND i.active = true
    """))
    nifty500_count = res_instruments.scalar() or 0
    
    return {
        "is_running": sync_progress.is_running,
        "total_symbols": sync_progress.total_symbols,
        "current_index": sync_progress.current_index,
        "current_symbol": sync_progress.current_symbol,
        "errors": sync_progress.errors,
        "logs": sync_progress.logs,
        "started_at": sync_progress.started_at.isoformat() if sync_progress.started_at else None,
        "completed_at": sync_progress.completed_at.isoformat() if sync_progress.completed_at else None,
        "db_metrics": {
            "total_candles": candles_count,
            "nifty500_instruments": nifty500_count
        }
    }

@router.post("/cancel")
async def cancel_sync():
    """
    Cancels the active historical sync.
    """
    if not sync_progress.is_running:
        raise HTTPException(
            status_code=400,
            detail="No active sync run to cancel."
        )
    
    sync_progress.cancel("User cancelled the sync.")
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

