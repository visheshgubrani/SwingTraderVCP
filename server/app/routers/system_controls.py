from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from app.database import db_dep
from app.schemas.trading import (
    KillSwitchUpdate,
    KillSwitchView,
    ReconciliationItemView,
    ReconciliationRunView,
    ReconciliationTriggerResponse,
)
from app.services.kill_switch import (
    KillSwitchUnavailableError,
    get_kill_switch,
    publish_kill_switch,
    update_kill_switch,
)

router = APIRouter(prefix="/system", tags=["system controls"])


@router.get("/kill-switch", response_model=KillSwitchView)
async def read_kill_switch(db: db_dep) -> KillSwitchView:
    try:
        return await get_kill_switch(db)
    except KillSwitchUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/kill-switch", response_model=KillSwitchView)
async def set_kill_switch(
    payload: KillSwitchUpdate,
    request: Request,
    db: db_dep,
) -> KillSwitchView:
    try:
        state = await update_kill_switch(
            db,
            enabled=payload.enabled,
            reason=payload.reason,
        )
        await db.commit()
    except KillSwitchUnavailableError as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

    try:
        state.redis_published = await publish_kill_switch(
            request.app.state.redis,
            state,
        )
    except Exception:
        # Postgres remains authoritative. P5 workers also re-read controls;
        # the response makes the missed instant fan-out visible.
        state.redis_published = False
    return state


@router.get("/reconciliation/runs", response_model=list[ReconciliationRunView])
async def list_reconciliation_runs(
    db: db_dep,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ReconciliationRunView]:
    result = await db.execute(
        text(
            """
            SELECT
                id,
                status,
                started_at,
                completed_at,
                discrepancies_found,
                summary,
                error_message
            FROM reconciliation_runs
            ORDER BY started_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [ReconciliationRunView.model_validate(dict(row)) for row in result.mappings().all()]


@router.get(
    "/reconciliation/runs/{run_id}/items",
    response_model=list[ReconciliationItemView],
)
async def list_reconciliation_items(
    run_id: UUID,
    db: db_dep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ReconciliationItemView]:
    result = await db.execute(
        text(
            """
            SELECT
                id,
                reconciliation_run_id,
                domain,
                local_record_id,
                broker_record_id,
                issue_type,
                severity,
                local_snapshot,
                broker_snapshot,
                resolution_status,
                resolved_at,
                created_at
            FROM reconciliation_items
            WHERE reconciliation_run_id = :run_id
            ORDER BY severity DESC, created_at ASC
            LIMIT :limit
            """
        ),
        {"run_id": run_id, "limit": limit},
    )
    return [
        ReconciliationItemView.model_validate(dict(row))
        for row in result.mappings().all()
    ]


@router.post("/reconciliation/run", response_model=ReconciliationTriggerResponse)
async def trigger_reconciliation(request: Request) -> ReconciliationTriggerResponse:
    redis = request.app.state.redis
    try:
        job = await redis.enqueue_job("run_reconciliation", "manual")
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to enqueue reconciliation job.",
        ) from exc

    if job is None:
        return ReconciliationTriggerResponse(
            status="already_running",
            job_id=None,
            message="A reconciliation job is already queued or running.",
        )

    return ReconciliationTriggerResponse(
        status="queued",
        job_id=job.job_id,
        message="Reconciliation job queued.",
    )
