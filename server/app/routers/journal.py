from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.database import db_dep
from app.schemas.journal import (
    ActualChargesUpdate,
    AiCoachRunCreate,
    AiCoachRunView,
    ChartArtifactClaimView,
    JournalDetailView,
    JournalListItem,
    JournalListResponse,
    JournalReviewUpdate,
    PeriodSummaryRequest,
)
from app.services.journal_service import (
    ArtifactNotFoundError,
    JournalConflictError,
    JournalNotFoundError,
    claim_chart_artifact,
    enqueue_ai_coach_run,
    get_ai_run,
    get_chart_artifact_png,
    get_journal_entry,
    get_period_summary,
    list_ai_runs,
    list_journal_entries,
    reconcile_actual_charges,
    update_journal_review,
    upload_chart_artifact,
)

router = APIRouter(prefix="/journal", tags=["journal"])


def _raise_journal_http_error(exc: Exception) -> None:
    if isinstance(exc, JournalNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (JournalConflictError, ArtifactNotFoundError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("/entries", response_model=JournalListResponse)
async def list_entries(
    db: db_dep,
    status: Annotated[str | None, Query()] = None,
    execution_mode: Annotated[str | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    setup_tag: Annotated[str | None, Query()] = None,
    regime: Annotated[str | None, Query()] = None,
    exit_outcome: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JournalListResponse:
    items, total = await list_journal_entries(
        db,
        status=status,
        execution_mode=execution_mode,
        symbol=symbol,
        setup_tag=setup_tag,
        regime=regime,
        exit_outcome=exit_outcome,
        offset=offset,
        limit=limit,
    )
    return JournalListResponse(
        items=[JournalListItem.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/entries/{journal_id}", response_model=JournalDetailView)
async def get_entry(db: db_dep, journal_id: UUID) -> JournalDetailView:
    try:
        entry = await get_journal_entry(db, journal_id)
        return JournalDetailView.model_validate(entry)
    except Exception as exc:
        _raise_journal_http_error(exc)


@router.patch("/entries/{journal_id}/review", response_model=JournalDetailView)
async def patch_review(
    db: db_dep,
    journal_id: UUID,
    payload: JournalReviewUpdate,
) -> JournalDetailView:
    try:
        entry = await update_journal_review(
            db,
            journal_id,
            notes=payload.notes,
            execution_rating=payload.execution_rating,
            setup_tags=payload.setup_tags,
            mistake_tags=payload.mistake_tags,
            emotion_tags=payload.emotion_tags,
            lessons=payload.lessons,
        )
        await db.commit()
        return JournalDetailView.model_validate(entry)
    except Exception as exc:
        await db.rollback()
        _raise_journal_http_error(exc)


@router.put("/entries/{journal_id}/actual-charges", response_model=JournalDetailView)
async def put_actual_charges(
    db: db_dep,
    journal_id: UUID,
    payload: ActualChargesUpdate,
) -> JournalDetailView:
    try:
        entry = await reconcile_actual_charges(
            db,
            journal_id,
            payload.model_dump(mode="json"),
        )
        await db.commit()
        return JournalDetailView.model_validate(entry)
    except Exception as exc:
        await db.rollback()
        _raise_journal_http_error(exc)


@router.post("/summary")
async def post_summary(db: db_dep, payload: PeriodSummaryRequest) -> dict:
    return await get_period_summary(
        db,
        bucket=payload.bucket,
        filters=payload.model_dump(exclude={"bucket"}),
    )


@router.post("/artifacts/claim", response_model=ChartArtifactClaimView | None)
async def post_claim_artifact(
    db: db_dep,
    claimer_id: Annotated[str, Query(min_length=1, max_length=128)],
) -> ChartArtifactClaimView | None:
    artifact = await claim_chart_artifact(db, claimer_id=claimer_id)
    await db.commit()
    if artifact is None:
        return None
    return ChartArtifactClaimView.model_validate(artifact)


@router.put("/artifacts/{artifact_id}/upload")
async def put_upload_artifact(
    db: db_dep,
    artifact_id: UUID,
    claimer_id: Annotated[str, Query(min_length=1, max_length=128)],
    request: Request,
) -> dict:
    try:
        png_bytes = await request.body()
        result = await upload_chart_artifact(
            db,
            artifact_id=artifact_id,
            claimer_id=claimer_id,
            png_bytes=png_bytes,
        )
        await db.commit()
        return result
    except Exception as exc:
        await db.rollback()
        _raise_journal_http_error(exc)


@router.get("/entries/{journal_id}/chart.png")
async def get_chart_png(db: db_dep, journal_id: UUID) -> Response:
    png = await get_chart_artifact_png(db, journal_id)
    if png is None:
        raise HTTPException(status_code=404, detail="Chart artifact not captured.")
    return Response(content=png, media_type="image/png")


@router.post("/ai/runs", response_model=AiCoachRunView, status_code=status.HTTP_202_ACCEPTED)
async def create_ai_run(
    request: Request,
    db: db_dep,
    payload: AiCoachRunCreate,
) -> AiCoachRunView:
    try:
        run_id = await enqueue_ai_coach_run(db, payload.filters.model_dump(exclude_none=True))
        await db.commit()
        await request.app.state.redis.enqueue_job(
            "run_journal_ai_coach",
            str(run_id),
        )
        run = await get_ai_run(db, run_id)
        return AiCoachRunView.model_validate(run)
    except Exception as exc:
        await db.rollback()
        _raise_journal_http_error(exc)


@router.get("/ai/runs/{run_id}", response_model=AiCoachRunView)
async def get_ai_run_endpoint(db: db_dep, run_id: UUID) -> AiCoachRunView:
    try:
        run = await get_ai_run(db, run_id)
        return AiCoachRunView.model_validate(run)
    except Exception as exc:
        _raise_journal_http_error(exc)


@router.get("/ai/runs", response_model=list[AiCoachRunView])
async def list_ai_run_endpoint(
    db: db_dep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[AiCoachRunView]:
    runs = await list_ai_runs(db, limit=limit)
    return [AiCoachRunView.model_validate(run) for run in runs]
