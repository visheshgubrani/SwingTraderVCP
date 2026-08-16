"""On-demand VCP vision validator API.

Advisory chart-image analysis for personal screening results only. The AI
verdict never changes technical rank, ``vcp_detected``, ``reviewer_status``,
watchlists, trade drafts, or execution state.
"""

import datetime as dt
import hashlib
import json
import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.vcp_vision import (
    VcpVisionAnalysisResponse,
    VcpVisionChartUploadResponse,
    VcpVisionCreateResponse,
    VcpVisionReviewRequest,
    VcpVisionStatusResponse,
)
from app.services.vcp_vision import (
    VisionUploadError,
    freeze_result_ohlcv,
    frozen_ohlcv_from_payload,
    frozen_ohlcv_payload,
    validate_chart_png,
    vcp_vision_job_id,
)

router = APIRouter(prefix="/screening", tags=["screening-vcp-vision"])
logger = logging.getLogger(__name__)

ChartKind = Literal["context", "detail"]

_RESULT_GUARD = """
    SELECT s.id AS result_id, s.instrument_id, i.symbol, r.as_of_date
    FROM screening_results s
    JOIN instruments i ON i.id = s.instrument_id
    JOIN scan_runs r ON r.id = s.scan_run_id
    WHERE s.id = :result_id
      AND r.visibility = 'personal'
      AND r.triggered_by <> 'manual_shadow'
"""

_ANALYSIS_STATUS_GUARD = """
    SELECT v.status
    FROM vcp_visual_analyses v
    JOIN screening_results s ON s.id = v.screening_result_id
    JOIN scan_runs r ON r.id = s.scan_run_id
    WHERE v.id = :analysis_id
      AND r.visibility = 'personal'
      AND r.triggered_by <> 'manual_shadow'
"""


def _vision_http_exception(exc: VisionUploadError, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(exc))


def _require_vision_enabled() -> None:
    if not settings.vcp_vision_enabled:
        raise HTTPException(
            status_code=503,
            detail="VCP vision validation is disabled by server configuration.",
        )


async def _mark_enqueue_failed(
    db: AsyncSession,
    analysis_id: uuid.UUID,
    error: Exception,
) -> None:
    await db.execute(
        text(
            """
            UPDATE vcp_visual_analyses
            SET status = 'failed',
                error_code = 'EnqueueError',
                error_message = :error
            WHERE id = :analysis_id AND status = 'queued'
            """
        ),
        {"analysis_id": analysis_id, "error": str(error)[:500]},
    )
    await db.commit()


async def _enqueue_analysis(
    request: Request,
    db: AsyncSession,
    analysis_id: uuid.UUID,
) -> None:
    redis_pool = getattr(request.app.state, "redis", None)
    try:
        if redis_pool is None:
            raise RuntimeError("Redis background queue is unavailable.")
        queued_job = await redis_pool.enqueue_job(
            "run_vcp_vision_analysis",
            str(analysis_id),
            _job_id=vcp_vision_job_id(analysis_id),
        )
        if queued_job is None:
            raise RuntimeError("Redis rejected the vision job.")
    except Exception as enqueue_error:
        logger.exception("VCP vision enqueue failed for %s", analysis_id)
        await _mark_enqueue_failed(db, analysis_id, enqueue_error)
        raise HTTPException(
            status_code=503,
            detail="Failed to enqueue the vision analysis job.",
        ) from enqueue_error


# ---------------------------------------------------------------------------
# Create / reuse
# ---------------------------------------------------------------------------

@router.post(
    "/results/{result_id}/vcp-vision/analyses",
    response_model=VcpVisionCreateResponse,
)
async def create_vcp_vision_analysis(
    result_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> VcpVisionCreateResponse:
    """Create (or reuse) the advisory vision analysis for one screening result.

    The frozen EOD candle window is snapshotted here so every later analysis
    reads the same reproducible input the human saw when capturing charts.
    """
    _require_vision_enabled()
    result = await db.execute(text(_RESULT_GUARD), {"result_id": result_id})
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Personal screening result not found")
    if row["as_of_date"] is None:
        raise HTTPException(
            status_code=422,
            detail="Scan run has no as_of_date; cannot anchor the vision window.",
        )

    as_of_date = row["as_of_date"]
    if isinstance(as_of_date, dt.datetime):
        as_of_date = as_of_date.date()
    symbol = row["symbol"] or ""
    try:
        frozen = await freeze_result_ohlcv(
            db,
            instrument_id=row["instrument_id"],
            as_of_date=as_of_date,
            context_sessions=settings.vcp_vision_context_sessions,
            detail_sessions=settings.vcp_vision_detail_sessions,
            symbol=symbol,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    model = settings.vcp_vision_model
    reasoning_effort = settings.vcp_vision_reasoning_effort
    max_tokens = settings.vcp_vision_max_tokens
    reuse_key = ":".join(
        (
            str(result_id),
            frozen.source_hash,
            settings.vcp_vision_renderer_version,
            model,
            reasoning_effort,
            str(max_tokens),
            settings.vcp_vision_prompt_version,
            settings.vcp_vision_schema_version,
        )
    )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:reuse_key, 0))"),
        {"reuse_key": reuse_key},
    )
    reuse = await db.execute(
        text(
            """
            SELECT v.id, v.status
            FROM vcp_visual_analyses v
            WHERE v.screening_result_id = :result_id
              AND v.source_hash = :source_hash
              AND v.renderer_version = :renderer_version
              AND v.model = :model
              AND v.reasoning_effort = :reasoning_effort
              AND v.max_tokens = :max_tokens
              AND v.prompt_version = :prompt_version
              AND v.schema_version = :schema_version
              AND v.status IN ('awaiting_capture', 'queued', 'running', 'succeeded')
            ORDER BY v.created_at DESC
            LIMIT 1
            """
        ),
        {
            "result_id": result_id,
            "source_hash": frozen.source_hash,
            "renderer_version": settings.vcp_vision_renderer_version,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "max_tokens": max_tokens,
            "prompt_version": settings.vcp_vision_prompt_version,
            "schema_version": settings.vcp_vision_schema_version,
        },
    )
    existing = reuse.mappings().one_or_none()
    if existing is not None:
        # Release the transaction-scoped advisory lock explicitly before
        # returning a reused analysis.
        await db.rollback()
        return VcpVisionCreateResponse(
            analysis_id=existing["id"],
            status=existing["status"],
            reused=True,
            message=(
                "Reused the existing vision analysis; it is unchanged because "
                "the frozen candles and renderer are identical."
            ),
        )

    chart_source = {
        "as_of_date": as_of_date.isoformat(),
        "symbol": symbol,
        "context_sessions": settings.vcp_vision_context_sessions,
        "detail_sessions": settings.vcp_vision_detail_sessions,
    }
    inserted = await db.execute(
        text(
            """
            INSERT INTO vcp_visual_analyses (
                screening_result_id, status, chart_source, frozen_ohlcv,
                source_hash, renderer_version, model, reasoning_effort,
                max_tokens, prompt_version, schema_version
            )
            VALUES (
                :result_id, 'awaiting_capture', CAST(:chart_source AS jsonb),
                CAST(:frozen_ohlcv AS jsonb), :source_hash, :renderer_version,
                :model, :reasoning_effort, :max_tokens, :prompt_version,
                :schema_version
            )
            RETURNING id
            """
        ),
        {
            "result_id": result_id,
            "chart_source": json.dumps(chart_source),
            "frozen_ohlcv": json.dumps(
                frozen_ohlcv_payload(frozen), separators=(",", ":")
            ),
            "source_hash": frozen.source_hash,
            "renderer_version": settings.vcp_vision_renderer_version,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "max_tokens": max_tokens,
            "prompt_version": settings.vcp_vision_prompt_version,
            "schema_version": settings.vcp_vision_schema_version,
        },
    )
    analysis_id = inserted.scalar_one()
    await db.commit()
    return VcpVisionCreateResponse(
        analysis_id=analysis_id,
        status="awaiting_capture",
        reused=False,
        message=(
            "Vision analysis created. Capture and upload the standardized "
            "context and detail chart images to start validation."
        ),
    )


# ---------------------------------------------------------------------------
# Chart capture upload (raw PNG body)
# ---------------------------------------------------------------------------

@router.put(
    "/vcp-vision/analyses/{analysis_id}/charts/{chart_kind}",
    response_model=VcpVisionChartUploadResponse,
)
async def upload_analysis_chart(
    analysis_id: uuid.UUID,
    chart_kind: Annotated[ChartKind, Path()],
    payload: Annotated[bytes, Body(media_type="application/octet-stream")],
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> VcpVisionChartUploadResponse:
    """Store one standardized chart PNG; enqueue analysis once both are present."""
    _require_vision_enabled()
    try:
        validate_chart_png(
            payload,
            max_bytes=settings.vcp_vision_max_image_bytes,
        )
    except VisionUploadError as exc:
        status_code = 413 if "limit" in str(exc) else 400
        raise _vision_http_exception(exc, status_code=status_code) from exc
    image_hash = hashlib.sha256(payload).hexdigest()
    column = f"{chart_kind}_image"
    hash_column = f"{chart_kind}_image_hash"

    stored = await db.execute(
        text(
            f"""
            UPDATE vcp_visual_analyses
            SET {column} = :image, {hash_column} = :image_hash
            WHERE id = :analysis_id AND status = 'awaiting_capture'
              AND EXISTS (
                  SELECT 1
                  FROM screening_results s
                  JOIN scan_runs r ON r.id = s.scan_run_id
                  WHERE s.id = vcp_visual_analyses.screening_result_id
                    AND r.visibility = 'personal'
                    AND r.triggered_by <> 'manual_shadow'
              )
            RETURNING id
            """
        ),
        {
            "analysis_id": analysis_id,
            "image": payload,
            "image_hash": image_hash,
        },
    )
    if stored.scalar_one_or_none() is None:
        existing = await db.execute(
            text(_ANALYSIS_STATUS_GUARD), {"analysis_id": analysis_id}
        )
        status = existing.scalar_one_or_none()
        if status is None:
            raise HTTPException(status_code=404, detail="Vision analysis not found")
        raise HTTPException(
            status_code=409,
            detail=f"Chart images are immutable once analysis is {status}.",
        )
    transition = await db.execute(
        text(
            """
            UPDATE vcp_visual_analyses
            SET status = 'queued', error_code = NULL, error_message = NULL
            WHERE id = :analysis_id
              AND status = 'awaiting_capture'
              AND context_image IS NOT NULL
              AND detail_image IS NOT NULL
            RETURNING id
            """
        ),
        {"analysis_id": analysis_id},
    )
    enqueued = transition.scalar_one_or_none() is not None
    await db.commit()

    if not enqueued:
        return VcpVisionChartUploadResponse(
            analysis_id=analysis_id,
            chart=chart_kind,
            status="awaiting_capture",
            message="Chart stored. Upload the other chart view to start validation.",
        )

    await _enqueue_analysis(request, db, analysis_id)

    return VcpVisionChartUploadResponse(
        analysis_id=analysis_id,
        chart=chart_kind,
        status="queued",
        message="Vision validation job enqueued.",
    )


# ---------------------------------------------------------------------------
# Read surfaces
# ---------------------------------------------------------------------------

async def _load_analysis(
    db: AsyncSession,
    analysis_id: uuid.UUID,
    *,
    include_attempts: bool,
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            SELECT v.id, v.screening_result_id, v.status, v.chart_source,
                   v.frozen_ohlcv, v.source_hash, v.renderer_version, v.model,
                   v.reasoning_effort, v.max_tokens,
                   v.prompt_version, v.schema_version,
                   v.result, v.ai_verdict, v.error_code, v.error_message,
                   v.usage, v.cost, v.human_verdict, v.human_note,
                   v.human_reviewed_at, v.created_at, v.updated_at,
                   i.id AS instrument_id
            FROM vcp_visual_analyses v
            JOIN screening_results s ON s.id = v.screening_result_id
            JOIN scan_runs r ON r.id = s.scan_run_id
            JOIN instruments i ON i.id = s.instrument_id
            WHERE v.id = :analysis_id
              AND r.visibility = 'personal'
              AND r.triggered_by <> 'manual_shadow'
            """
        ),
        {"analysis_id": analysis_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Vision analysis not found")

    frozen_packet, stale = await _rebuild_frozen(db, row)
    payload: dict[str, Any] = {
        "id": row["id"],
        "screening_result_id": row["screening_result_id"],
        "status": row["status"],
        "chart_source": dict(row["chart_source"] or {}),
        "renderer_version": row["renderer_version"],
        "model": row["model"],
        "reasoning_effort": row["reasoning_effort"],
        "max_tokens": row["max_tokens"],
        "prompt_version": row["prompt_version"],
        "schema_version": row["schema_version"],
        "result": dict(row["result"] or {}) or None,
        "ai_verdict": row["ai_verdict"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "usage": dict(row["usage"] or {}),
        "cost": float(row["cost"] or 0),
        "human_review": (
            {
                "verdict": row["human_verdict"],
                "note": row["human_note"],
                "reviewed_at": row["human_reviewed_at"],
            }
            if row["human_reviewed_at"] is not None or row["human_verdict"] is not None
            else None
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "attempts": [],
        "frozen": frozen_packet,
        "candles_stale": stale,
    }
    if include_attempts:
        attempts = await db.execute(
            text(
                """
                SELECT id, attempt_number, status, model, reasoning_effort,
                       prompt_version, input_hash, request_id, http_status,
                       usage, cost, error_code, error_message,
                       started_at, completed_at
                FROM vcp_visual_attempts
                WHERE analysis_id = :analysis_id
                ORDER BY attempt_number ASC
                """
            ),
            {"analysis_id": analysis_id},
        )
        payload["attempts"] = [dict(attempt) for attempt in attempts.mappings()]
    return payload


async def _rebuild_frozen(
    db: AsyncSession,
    row: Any,
) -> tuple[dict[str, Any] | None, bool]:
    chart_source = dict(row["chart_source"] or {})
    as_of = chart_source.get("as_of_date")
    if not as_of:
        return None, True
    context_sessions = int(
        chart_source.get("context_sessions")
        or settings.vcp_vision_context_sessions
    )
    detail_sessions = int(
        chart_source.get("detail_sessions")
        or settings.vcp_vision_detail_sessions
    )
    try:
        if row["frozen_ohlcv"]:
            frozen = frozen_ohlcv_from_payload(
                row["frozen_ohlcv"],
                symbol=chart_source.get("symbol") or "",
                as_of_date=dt.date.fromisoformat(as_of),
                context_sessions=context_sessions,
                detail_sessions=detail_sessions,
            )
        else:
            # Compatibility for analyses created before migration 014.
            frozen = await freeze_result_ohlcv(
                db,
                instrument_id=row["instrument_id"],
                as_of_date=dt.date.fromisoformat(as_of),
                context_sessions=context_sessions,
                detail_sessions=detail_sessions,
                symbol=chart_source.get("symbol") or "",
            )
    except Exception:
        logger.exception(
            "VCP vision frozen rebuild failed for analysis %s", row["id"]
        )
        return None, True
    packet = {
        "symbol": frozen.symbol,
        "as_of_date": frozen.as_of_date.isoformat(),
        "context_sessions": frozen.context_sessions,
        "detail_sessions": frozen.detail_sessions,
        "source_hash": frozen.source_hash,
        "candles": [
            {
                "date": candle.date.isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in frozen.candles
        ],
    }
    return packet, frozen.source_hash != row["source_hash"]


@router.get(
    "/vcp-vision/analyses/{analysis_id}",
    response_model=VcpVisionAnalysisResponse,
)
async def get_vcp_vision_analysis(
    analysis_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return one vision analysis with its attempts and frozen candle packet."""
    return await _load_analysis(db, analysis_id, include_attempts=True)


@router.get(
    "/vcp-vision/analyses/{analysis_id}/charts/{chart_kind}",
    response_class=Response,
)
async def get_analysis_chart(
    analysis_id: uuid.UUID,
    chart_kind: Annotated[ChartKind, Path()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Serve the stored standardized chart PNG for a vision analysis."""
    column = f"{chart_kind}_image"
    result = await db.execute(
        text(
            f"""
            SELECT v.{column} AS image
            FROM vcp_visual_analyses v
            JOIN screening_results s ON s.id = v.screening_result_id
            JOIN scan_runs r ON r.id = s.scan_run_id
            WHERE v.id = :analysis_id
              AND r.visibility = 'personal'
              AND r.triggered_by <> 'manual_shadow'
            """
        ),
        {"analysis_id": analysis_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Vision analysis not found")
    image = row["image"]
    if not image:
        raise HTTPException(
            status_code=404,
            detail=f"Analysis has no {chart_kind} chart image stored.",
        )
    return Response(content=bytes(image), media_type="image/png")


@router.get(
    "/results/{result_id}/vcp-vision/latest",
    response_model=VcpVisionAnalysisResponse,
)
async def get_latest_vcp_vision_analysis(
    result_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return the newest vision analysis for a personal screening result."""
    result = await db.execute(text(_RESULT_GUARD), {"result_id": result_id})
    if result.mappings().one_or_none() is None:
        raise HTTPException(status_code=404, detail="Personal screening result not found")
    latest = await db.execute(
        text(
            """
            SELECT id FROM vcp_visual_analyses
            WHERE screening_result_id = :result_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"result_id": result_id},
    )
    analysis_id = latest.scalar_one_or_none()
    if analysis_id is None:
        raise HTTPException(
            status_code=404,
            detail="No vision analysis exists for this screening result.",
        )
    return await _load_analysis(db, analysis_id, include_attempts=False)


@router.get("/vcp-vision/status", response_model=VcpVisionStatusResponse)
async def get_vcp_vision_status(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> VcpVisionStatusResponse:
    """Return whether the vision validator is enabled and analysis counts."""
    result = await db.execute(
        text(
            """
            SELECT v.status AS status, count(*)::int AS count
            FROM vcp_visual_analyses v
            JOIN screening_results s ON s.id = v.screening_result_id
            JOIN scan_runs r ON r.id = s.scan_run_id
            WHERE r.visibility = 'personal'
              AND r.triggered_by <> 'manual_shadow'
            GROUP BY v.status
            """
        )
    )
    counts = {row.status: row.count for row in result.all()}
    return VcpVisionStatusResponse(
        enabled=settings.vcp_vision_enabled,
        model=settings.vcp_vision_model,
        counts=counts,
    )


# ---------------------------------------------------------------------------
# Human review + explicit retry
# ---------------------------------------------------------------------------

@router.patch(
    "/vcp-vision/analyses/{analysis_id}/review",
    response_model=VcpVisionAnalysisResponse,
)
async def review_vcp_vision_analysis(
    analysis_id: uuid.UUID,
    payload: VcpVisionReviewRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Record the human's evaluation metadata for the advisory verdict."""
    updated = await db.execute(
        text(
            """
            UPDATE vcp_visual_analyses
            SET human_verdict = :verdict,
                human_note = NULLIF(:note, ''),
                human_reviewed_at = now()
            WHERE id = :analysis_id
              AND status = 'succeeded'
              AND EXISTS (
                  SELECT 1
                  FROM screening_results s
                  JOIN scan_runs r ON r.id = s.scan_run_id
                  WHERE s.id = vcp_visual_analyses.screening_result_id
                    AND r.visibility = 'personal'
                    AND r.triggered_by <> 'manual_shadow'
              )
            RETURNING id
            """
        ),
        {
            "analysis_id": analysis_id,
            "verdict": payload.verdict,
            "note": payload.note,
        },
    )
    if updated.scalar_one_or_none() is None:
        existing = await db.execute(
            text(_ANALYSIS_STATUS_GUARD), {"analysis_id": analysis_id}
        )
        status = existing.scalar_one_or_none()
        if status is None:
            raise HTTPException(status_code=404, detail="Vision analysis not found")
        raise HTTPException(
            status_code=409,
            detail=f"Human review requires a succeeded analysis; current status is {status}.",
        )
    await db.commit()
    return await _load_analysis(db, analysis_id, include_attempts=True)


@router.post(
    "/vcp-vision/analyses/{analysis_id}/retry",
    response_model=VcpVisionChartUploadResponse,
)
async def retry_vcp_vision_analysis(
    analysis_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> VcpVisionChartUploadResponse:
    """Explicitly re-run a failed vision analysis (same frozen input)."""
    _require_vision_enabled()
    try:
        transition = await db.execute(
            text(
                """
            UPDATE vcp_visual_analyses AS target
            SET status = 'queued', error_code = NULL, error_message = NULL,
                max_tokens = GREATEST(target.max_tokens, :max_tokens)
            WHERE target.id = :analysis_id
              AND target.status = 'failed'
              AND target.context_image IS NOT NULL
              AND target.detail_image IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM screening_results s
                  JOIN scan_runs r ON r.id = s.scan_run_id
                  WHERE s.id = target.screening_result_id
                    AND r.visibility = 'personal'
                    AND r.triggered_by <> 'manual_shadow'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM vcp_visual_analyses active
                  WHERE active.id <> target.id
                    AND active.screening_result_id = target.screening_result_id
                    AND active.source_hash = target.source_hash
                    AND active.renderer_version = target.renderer_version
                    AND active.model = target.model
                    AND active.reasoning_effort = target.reasoning_effort
                    AND active.max_tokens = GREATEST(
                        target.max_tokens, :max_tokens
                    )
                    AND active.prompt_version = target.prompt_version
                    AND active.schema_version = target.schema_version
                    AND active.status IN (
                        'awaiting_capture', 'queued', 'running', 'succeeded'
                    )
              )
            RETURNING id
                """
            ),
            {
                "analysis_id": analysis_id,
                "max_tokens": settings.vcp_vision_max_tokens,
            },
        )
    except IntegrityError as exc:
        # A concurrent create/retry can win the partial unique reuse index.
        # Surface a deterministic conflict instead of leaking a database 500.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An equivalent VCP vision analysis is already active.",
        ) from exc
    if transition.scalar_one_or_none() is None:
        existing = await db.execute(
            text(_ANALYSIS_STATUS_GUARD), {"analysis_id": analysis_id}
        )
        status = existing.scalar_one_or_none()
        if status is None:
            raise HTTPException(status_code=404, detail="Vision analysis not found")
        raise HTTPException(
            status_code=409,
            detail=(
                "Vision analysis cannot be retried in its current state: "
                f"{status} (both chart images must be present and no equivalent "
                "active analysis may exist)."
            ),
        )
    await db.commit()

    await _enqueue_analysis(request, db, analysis_id)

    return VcpVisionChartUploadResponse(
        analysis_id=analysis_id,
        chart="context",
        status="queued",
        message="Vision validation retry enqueued.",
    )
