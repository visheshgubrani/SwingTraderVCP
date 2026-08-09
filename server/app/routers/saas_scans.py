"""Public Swyingify SaaS read APIs — screening browse only, no money path."""

from __future__ import annotations

import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.saas import (
    SaasCandlesResponse,
    SaasDailyCandle,
    SaasQualificationComponent,
    SaasQualificationFingerprint,
    SaasScanLatestResponse,
    SaasScanResultsResponse,
    SaasScannerResult,
)

router = APIRouter(prefix="/saas", tags=["saas"])

COMPONENT_META: dict[str, tuple[str, str, str]] = {
    "stage2": ("stage2", "Stage 2 trend", "Trend"),
    "relative_strength": ("relativeStrength", "Relative strength", "RS"),
    "high_proximity": ("nearHigh", "Near 52-week high", "High"),
    "atr_contraction": ("atrContraction", "ATR contraction", "ATR"),
    "bollinger_contraction": ("bollingerContraction", "Bollinger contraction", "Bands"),
    "volume_dry_up": ("volumeDryUp", "Volume dry-up", "Volume"),
    "volatility_contraction": ("atrContraction", "Volatility contraction", "Vola"),
    "rs_line_high": ("relativeStrength", "RS line near high", "RS line"),
    "up_down_volume": ("volumeDryUp", "Up/down volume", "U/D vol"),
    "pocket_pivot": ("volumeDryUp", "Pocket pivot", "Pivot"),
}

SPARK_LOOKBACK = 20
BOARD_CANDLE_LOOKBACK = 40


def _require_history_access(internal_key: str | None) -> None:
    expected = settings.saas_internal_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Historical SaaS scans are not configured.",
        )
    if not internal_key or internal_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in required for past scan dates.",
        )


def _component_status(points: float, max_points: float) -> str:
    if max_points <= 0:
        return "watch"
    ratio = points / max_points
    if ratio >= 0.85:
        return "strong"
    if ratio >= 0.55:
        return "supporting"
    return "watch"


def _fingerprint_from_metrics(metrics: dict[str, Any]) -> SaasQualificationFingerprint:
    score_detail = metrics.get("score") or {}
    raw_components = score_detail.get("components") or {}
    components: list[SaasQualificationComponent] = []
    seen_keys: set[str] = set()

    for engine_key, payload in raw_components.items():
        meta = COMPONENT_META.get(engine_key)
        if meta is None:
            continue
        ui_key, label, short = meta
        if ui_key in seen_keys:
            continue
        seen_keys.add(ui_key)
        points = float(payload.get("points") or 0)
        max_points = float(payload.get("max_points") or 0)
        components.append(
            SaasQualificationComponent(
                key=ui_key,
                label=label,
                shortLabel=short,
                score=round(points, 2),
                maxScore=round(max_points, 2),
                status=_component_status(points, max_points),  # type: ignore[arg-type]
                summary=f"{label}: {points:.1f}/{max_points:.1f}",
            )
        )

    return SaasQualificationFingerprint(
        strongCount=sum(1 for item in components if item.status == "strong"),
        totalCount=len(components),
        components=components,
    )


def _normalize_grade(raw: Any, score: float) -> str:
    if raw in ("A", "B", "C"):
        return raw
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    return "C"


async def _latest_global_run(
    db: AsyncSession,
    *,
    as_of_date: datetime.date | None = None,
) -> Any | None:
    if as_of_date is not None:
        query = text(
            """
            SELECT
                r.id,
                r.status,
                r.as_of_date,
                r.completed_at,
                (
                    SELECT COUNT(*)::int
                    FROM screening_results s
                    WHERE s.scan_run_id = r.id
                ) AS result_count
            FROM scan_runs r
            JOIN scan_templates t ON t.id = r.template_id
            WHERE r.visibility = 'global'
              AND t.family = 'minervini'
              AND t.code = 'standard'
              AND r.as_of_date = :as_of_date
            ORDER BY
                CASE r.status
                    WHEN 'succeeded' THEN 0
                    WHEN 'running' THEN 1
                    WHEN 'queued' THEN 2
                    ELSE 3
                END,
                r.created_at DESC
            LIMIT 1
            """
        )
        return (
            await db.execute(query, {"as_of_date": as_of_date})
        ).one_or_none()

    query = text(
        """
        SELECT
            r.id,
            r.status,
            r.as_of_date,
            r.completed_at,
            (
                SELECT COUNT(*)::int
                FROM screening_results s
                WHERE s.scan_run_id = r.id
            ) AS result_count
        FROM scan_runs r
        JOIN scan_templates t ON t.id = r.template_id
        WHERE r.visibility = 'global'
          AND t.family = 'minervini'
          AND t.code = 'standard'
          AND r.as_of_date IS NOT NULL
        ORDER BY
            CASE r.status
                WHEN 'succeeded' THEN 0
                WHEN 'running' THEN 1
                WHEN 'queued' THEN 2
                ELSE 3
            END,
            r.as_of_date DESC,
            r.created_at DESC
        LIMIT 1
        """
    )
    return (await db.execute(query)).one_or_none()


async def _load_candle_series(
    db: AsyncSession,
    instrument_ids: list[UUID],
    *,
    limit_per_symbol: int,
) -> dict[UUID, list[Any]]:
    if not instrument_ids:
        return {}
    query = text(
        """
        SELECT instrument_id, candle_start, open_price, high_price,
               low_price, close_price, volume
        FROM (
            SELECT
                c.instrument_id,
                c.candle_start,
                c.open_price,
                c.high_price,
                c.low_price,
                c.close_price,
                c.volume,
                ROW_NUMBER() OVER (
                    PARTITION BY c.instrument_id
                    ORDER BY c.candle_start DESC
                ) AS rn
            FROM market_candles c
            WHERE c.timeframe = '1d'
              AND c.instrument_id = ANY(:instrument_ids)
        ) ranked
        WHERE rn <= :limit_per_symbol
        ORDER BY instrument_id, candle_start ASC
        """
    ).bindparams(
        bindparam("instrument_ids", type_=ARRAY(PGUUID(as_uuid=True))),
    )
    rows = (
        await db.execute(
            query,
            {
                "instrument_ids": instrument_ids,
                "limit_per_symbol": limit_per_symbol,
            },
        )
    ).all()
    grouped: dict[UUID, list[Any]] = {iid: [] for iid in instrument_ids}
    for row in rows:
        grouped[row.instrument_id].append(row)
    return grouped


def _day_change_pct(closes: list[float]) -> float:
    if len(closes) < 2 or closes[-2] == 0:
        return 0.0
    return round(((closes[-1] - closes[-2]) / closes[-2]) * 100, 2)


def _sector_from_metadata(metadata: Any) -> str:
    if isinstance(metadata, dict):
        industry = metadata.get("industry")
        if isinstance(industry, str) and industry.strip():
            return industry.strip()
    return "—"


@router.get(
    "/scans/minervini/standard/latest",
    response_model=SaasScanLatestResponse,
)
async def get_standard_latest(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SaasScanLatestResponse:
    row = await _latest_global_run(db)
    if row is None:
        return SaasScanLatestResponse(
            status="missing",
            message="No global Standard scan has completed yet.",
        )
    return SaasScanLatestResponse(
        asOfDate=row.as_of_date.isoformat() if row.as_of_date else None,
        status=row.status,
        completedAt=row.completed_at,
        resultCount=int(row.result_count or 0),
        scanRunId=row.id,
    )


@router.get(
    "/scans/minervini/standard/results",
    response_model=SaasScanResultsResponse,
)
async def get_standard_results(
    db: Annotated[AsyncSession, Depends(get_db)],
    as_of_date: Annotated[datetime.date | None, Query(alias="asOfDate")] = None,
    x_swyingify_internal_key: Annotated[str | None, Header()] = None,
) -> SaasScanResultsResponse:
    latest = await _latest_global_run(db)
    if as_of_date is not None:
        latest_date = latest.as_of_date if latest is not None else None
        if latest_date is None or as_of_date != latest_date:
            _require_history_access(x_swyingify_internal_key)

    run = await _latest_global_run(db, as_of_date=as_of_date)
    if run is None or run.status != "succeeded":
        # Latest (no asOfDate) should be poll-friendly for the public board.
        if as_of_date is None:
            latest_meta = await _latest_global_run(db)
            return SaasScanResultsResponse(
                asOfDate=(
                    latest_meta.as_of_date.isoformat()
                    if latest_meta is not None and latest_meta.as_of_date
                    else ""
                ),
                status=latest_meta.status if latest_meta is not None else "missing",
                completedAt=(
                    latest_meta.completed_at if latest_meta is not None else None
                ),
                scanRunId=latest_meta.id if latest_meta is not None else None,
                results=[],
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No successful Standard scan found for the requested date.",
        )

    result_rows = (
        await db.execute(
            text(
                """
                SELECT
                    s.id,
                    s.instrument_id,
                    s.result_rank,
                    s.technical_score,
                    s.close_price,
                    s.pct_from_52w_high,
                    s.technical_metrics,
                    i.symbol,
                    i.name,
                    i.metadata
                FROM screening_results s
                JOIN instruments i ON i.id = s.instrument_id
                WHERE s.scan_run_id = :scan_run_id
                ORDER BY s.result_rank ASC NULLS LAST
                LIMIT 25
                """
            ),
            {"scan_run_id": run.id},
        )
    ).all()

    instrument_ids = [row.instrument_id for row in result_rows]
    candles_by_inst = await _load_candle_series(
        db,
        instrument_ids,
        limit_per_symbol=BOARD_CANDLE_LOOKBACK,
    )

    as_of = run.as_of_date.isoformat() if run.as_of_date else ""
    results: list[SaasScannerResult] = []
    for fallback_rank, row in enumerate(result_rows, start=1):
        metrics = row.technical_metrics or {}
        score_detail = metrics.get("score") or {}
        score = float(row.technical_score or score_detail.get("total") or 0)
        candle_rows = candles_by_inst.get(row.instrument_id, [])
        closes = [float(c.close_price) for c in candle_rows]
        spark = closes[-SPARK_LOOKBACK:] if closes else []
        board_candles = [
            SaasDailyCandle(
                time=c.candle_start.astimezone(
                    datetime.timezone.utc
                ).date().isoformat()
                if c.candle_start.tzinfo
                else c.candle_start.date().isoformat(),
                open=float(c.open_price),
                high=float(c.high_price),
                low=float(c.low_price),
                close=float(c.close_price),
                volume=int(c.volume or 0),
            )
            for c in candle_rows
        ]
        pct_raw = float(row.pct_from_52w_high or 0)
        # Stored as fraction in screener; expose as percent for the board.
        pct_from_high = pct_raw * 100 if abs(pct_raw) <= 1.5 else pct_raw

        results.append(
            SaasScannerResult(
                id=str(row.id),
                symbol=row.symbol,
                companyName=row.name or row.symbol,
                sector=_sector_from_metadata(row.metadata),
                rank=int(row.result_rank or fallback_rank),
                asOfDate=as_of,
                close=float(row.close_price or (closes[-1] if closes else 0)),
                technicalScore=score,
                grade=_normalize_grade(score_detail.get("grade"), score),
                rsRating=int(metrics.get("rs_rating") or 0),
                pctFrom52WeekHigh=round(pct_from_high, 2),
                adtvCrore=float(metrics.get("adtv_crore") or 0),
                dayChangePct=_day_change_pct(closes),
                sparkSeries=spark,
                atrRatio=float(metrics.get("atr_ratio") or 0),
                volumeDryUpRatio=float(metrics.get("volume_dry_up_ratio") or 0),
                fingerprint=_fingerprint_from_metrics(metrics),
                candles=board_candles,
            )
        )

    return SaasScanResultsResponse(
        asOfDate=as_of,
        status=run.status,
        completedAt=run.completed_at,
        scanRunId=run.id,
        results=results,
    )


@router.get(
    "/candles/{symbol}",
    response_model=SaasCandlesResponse,
)
async def get_symbol_candles(
    symbol: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=30, le=500)] = 252,
) -> SaasCandlesResponse:
    normalized = symbol.strip().upper()
    instrument = (
        await db.execute(
            text(
                """
                SELECT id, symbol, name
                FROM instruments
                WHERE upper(symbol) = :symbol AND active = true
                LIMIT 1
                """
            ),
            {"symbol": normalized},
        )
    ).one_or_none()
    if instrument is None:
        raise HTTPException(status_code=404, detail="Symbol not found")

    rows = (
        await db.execute(
            text(
                """
                SELECT candle_start, open_price, high_price, low_price,
                       close_price, volume
                FROM (
                    SELECT
                        candle_start, open_price, high_price, low_price,
                        close_price, volume
                    FROM market_candles
                    WHERE instrument_id = :instrument_id AND timeframe = '1d'
                    ORDER BY candle_start DESC
                    LIMIT :limit
                ) recent
                ORDER BY candle_start ASC
                """
            ),
            {"instrument_id": instrument.id, "limit": limit},
        )
    ).all()

    candles = [
        SaasDailyCandle(
            time=(
                row.candle_start.astimezone(datetime.timezone.utc).date().isoformat()
                if row.candle_start.tzinfo
                else row.candle_start.date().isoformat()
            ),
            open=float(row.open_price),
            high=float(row.high_price),
            low=float(row.low_price),
            close=float(row.close_price),
            volume=int(row.volume or 0),
        )
        for row in rows
    ]
    as_of = candles[-1].time if candles else None
    return SaasCandlesResponse(
        symbol=instrument.symbol,
        companyName=instrument.name or instrument.symbol,
        asOfDate=as_of,
        candles=candles,
    )
