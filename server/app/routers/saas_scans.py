"""Public Swyingify SaaS read APIs — screening browse only, no money path."""

from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import hmac
import json
import secrets
import time
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
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
    SaasVariantCreateRequest,
    SaasVariantRunResponse,
)
from app.services.historical_fetcher import latest_completed_eod_date
from app.services.screening_config import SAAS_MINERVINI_STANDARD_CONFIG

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
VARIANT_DAILY_QUOTA = 5


def _decode_access_claims(access_token: str | None) -> dict[str, Any]:
    if (
        settings.app_environment != "production"
        and access_token == "development-bypass"
    ):
        return {
            "sub": "development-bypass-user",
            "features": [
                "scanner.strict",
                "scanner.custom",
                "scanner.history.recent",
                "scanner.history.full",
            ],
        }

    secret = settings.saas_internal_api_key.strip()
    if len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Protected SaaS APIs are not configured.",
        )
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A signed internal access assertion is required.",
        )

    try:
        payload_segment, signature = access_token.split(".", maxsplit=1)
        expected = hmac.new(
            secret.encode(),
            payload_segment.encode(),
            hashlib.sha256,
        ).digest()
        supplied = base64.urlsafe_b64decode(
            signature + "=" * (-len(signature) % 4)
        )
        if not secrets.compare_digest(supplied, expected):
            raise ValueError("signature mismatch")
        payload = base64.urlsafe_b64decode(
            payload_segment + "=" * (-len(payload_segment) % 4)
        )
        claims = json.loads(payload)
        if not isinstance(claims, dict):
            raise ValueError("claims must be an object")
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The internal access assertion is invalid.",
        ) from exc

    now = int(time.time())
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    features = claims.get("features")
    if (
        claims.get("v") != 1
        or claims.get("iss") != "swyingify-next"
        or claims.get("aud") != "swyingify-fastapi"
        or not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or issued_at > now + 30
        or expires_at < now
        or expires_at - issued_at > 120
        or not isinstance(features, list)
        or not all(isinstance(item, str) for item in features)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The internal access assertion is invalid or expired.",
        )
    return claims


def _require_access(
    access_token: str | None,
    *,
    any_feature: set[str],
    require_subject: bool = False,
) -> str | None:
    claims = _decode_access_claims(access_token)
    granted = set(claims["features"])
    if not granted.intersection(any_feature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The signed account does not have access to this capability.",
        )
    subject = claims.get("sub")
    if require_subject and (not isinstance(subject, str) or not subject.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A signed-in user is required for custom scans.",
        )
    return subject.strip() if isinstance(subject, str) else None


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
    code: Literal["standard", "strict"] = "standard",
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
              AND t.code = :code
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
            await db.execute(query, {"as_of_date": as_of_date, "code": code})
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
          AND t.code = :code
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
    return (await db.execute(query, {"code": code})).one_or_none()


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


async def _results_for_run(
    db: AsyncSession,
    *,
    run_id: UUID,
    as_of_date: datetime.date | None,
    preset: Literal["standard", "strict", "custom"],
    limit: int,
) -> list[SaasScannerResult]:
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
                LIMIT :limit
                """
            ),
            {"scan_run_id": run_id, "limit": limit},
        )
    ).all()

    instrument_ids = [row.instrument_id for row in result_rows]
    candles_by_inst = await _load_candle_series(
        db,
        instrument_ids,
        limit_per_symbol=BOARD_CANDLE_LOOKBACK,
    )
    as_of = as_of_date.isoformat() if as_of_date else ""
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
        pct_from_high = pct_raw * 100 if abs(pct_raw) <= 1.5 else pct_raw

        results.append(
            SaasScannerResult(
                id=str(row.id),
                symbol=row.symbol,
                companyName=row.name or row.symbol,
                sector=_sector_from_metadata(row.metadata),
                preset=preset,
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
    return results


async def _variant_runs_today(db: AsyncSession, user_id: str) -> int:
    return int(
        (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*)::int
                    FROM scan_runs
                    WHERE visibility = 'user'
                      AND owner_user_id = :user_id
                      AND triggered_by = 'saas_user_variant'
                      AND status IN ('queued', 'running', 'succeeded')
                      AND created_at >= (
                          (now() AT TIME ZONE 'Asia/Kolkata')::date
                          AT TIME ZONE 'Asia/Kolkata'
                      )
                    """
                ),
                {"user_id": user_id},
            )
        ).scalar_one()
    )


async def _lock_variant_quota(db: AsyncSession, user_id: str) -> None:
    """Serialize one user's quota check + run reservation for the IST day."""
    ist_day = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    ).date()
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:quota_key, 0))"),
        {"quota_key": f"swyingify-variant:{user_id}:{ist_day.isoformat()}"},
    )


def _variant_config(payload: SaasVariantCreateRequest) -> dict[str, Any]:
    contraction_limits = {
        "balanced": (1.40, 0.60),
        "tight": (1.20, 0.40),
        "very_tight": (1.10, 0.25),
    }
    volume_limits = {
        "normal": 1.00,
        "strong": 0.80,
        "extreme": 0.60,
    }
    high_full = {
        5: 2.5,
        10: 5.0,
        15: 8.0,
        25: 15.0,
    }
    atr_limit, bb_limit = contraction_limits[payload.contraction]
    raw = SAAS_MINERVINI_STANDARD_CONFIG.model_dump()
    raw.update(
        {
            "shortlist_limit": 50,
            "min_rs_rating": payload.minRsRating,
            "max_distance_52w_high_pct": payload.maxDistance52WeekHighPct,
            "high_proximity_zero_pct": payload.maxDistance52WeekHighPct,
            "high_proximity_full_pct": high_full[
                payload.maxDistance52WeekHighPct
            ],
            "min_adtv_crore": payload.minAdtvCrore,
            "stage2_core_checks_required": payload.stage2ChecksRequired,
            "max_atr_proximity_factor": atr_limit,
            "max_bb_width_percentile": bb_limit,
            "max_volume_dry_up_ratio": volume_limits[payload.volumeDryUp],
            "minimum_technical_score": payload.minimumTechnicalScore,
        }
    )
    return type(SAAS_MINERVINI_STANDARD_CONFIG).model_validate(raw).model_dump()


@router.post(
    "/scans/minervini/variants",
    response_model=SaasVariantRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_minervini_variant(
    payload: SaasVariantCreateRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_swyingify_access: Annotated[str | None, Header()] = None,
) -> SaasVariantRunResponse:
    user_id = _require_access(
        x_swyingify_access,
        any_feature={"scanner.custom"},
        require_subject=True,
    )
    assert user_id is not None
    await _lock_variant_quota(db, user_id)
    used = await _variant_runs_today(db, user_id)
    if used >= VARIANT_DAILY_QUOTA:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily custom scan limit reached ({VARIANT_DAILY_QUOTA}).",
        )

    template_id = (
        await db.execute(
            text(
                """
                SELECT id
                FROM scan_templates
                WHERE family = 'minervini'
                  AND code = 'standard'
                  AND is_active = true
                ORDER BY version DESC
                LIMIT 1
                """
            )
        )
    ).scalar_one_or_none()
    if template_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Minervini template is not configured.",
        )

    as_of_date = latest_completed_eod_date()
    config = _variant_config(payload)
    run_id = (
        await db.execute(
            text(
                """
                INSERT INTO scan_runs (
                    universe_code,
                    status,
                    triggered_by,
                    technical_config,
                    template_id,
                    visibility,
                    owner_user_id,
                    as_of_date
                )
                VALUES (
                    'NIFTY500',
                    'queued',
                    'saas_user_variant',
                    CAST(:technical_config AS jsonb),
                    :template_id,
                    'user',
                    :owner_user_id,
                    :as_of_date
                )
                RETURNING id
                """
            ),
            {
                "technical_config": json.dumps(config, separators=(",", ":")),
                "template_id": template_id,
                "owner_user_id": user_id,
                "as_of_date": as_of_date,
            },
        )
    ).scalar_one()
    await db.commit()

    redis_pool = getattr(request.app.state, "redis", None)
    if redis_pool is None:
        await db.execute(
            text(
                """
                UPDATE scan_runs
                SET status = 'failed', completed_at = now(),
                    error_message = 'Redis background queue unavailable'
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The scanner queue is unavailable.",
        )

    try:
        await redis_pool.enqueue_job("run_technical_scan", str(run_id))
    except Exception as exc:
        await db.execute(
            text(
                """
                UPDATE scan_runs
                SET status = 'failed', completed_at = now(),
                    error_message = :error
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id, "error": f"Variant enqueue failed: {exc}"},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The custom scan could not be queued.",
        ) from exc

    return SaasVariantRunResponse(
        runId=run_id,
        status="queued",
        asOfDate=as_of_date.isoformat(),
        quotaRemaining=VARIANT_DAILY_QUOTA - used - 1,
        message="Custom Nifty 500 scan queued.",
    )


@router.get(
    "/scans/minervini/variants/{run_id}",
    response_model=SaasVariantRunResponse,
)
async def get_minervini_variant(
    run_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_swyingify_access: Annotated[str | None, Header()] = None,
) -> SaasVariantRunResponse:
    user_id = _require_access(
        x_swyingify_access,
        any_feature={"scanner.custom"},
        require_subject=True,
    )
    assert user_id is not None
    run = (
        await db.execute(
            text(
                """
                SELECT id, status, as_of_date, error_message
                FROM scan_runs
                WHERE id = :run_id
                  AND visibility = 'user'
                  AND owner_user_id = :owner_user_id
                  AND triggered_by = 'saas_user_variant'
                LIMIT 1
                """
            ),
            {"run_id": run_id, "owner_user_id": user_id},
        )
    ).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Custom scan not found.")

    used = await _variant_runs_today(db, user_id)
    results = (
        await _results_for_run(
            db,
            run_id=run.id,
            as_of_date=run.as_of_date,
            preset="custom",
            limit=50,
        )
        if run.status == "succeeded"
        else []
    )
    return SaasVariantRunResponse(
        runId=run.id,
        status=run.status,
        asOfDate=run.as_of_date.isoformat() if run.as_of_date else "",
        quotaRemaining=max(0, VARIANT_DAILY_QUOTA - used),
        results=results,
        message=run.error_message,
    )


@router.get(
    "/scans/minervini/{code}/latest",
    response_model=SaasScanLatestResponse,
)
async def get_standard_latest(
    code: Literal["standard", "strict"],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SaasScanLatestResponse:
    row = await _latest_global_run(db, code=code)
    if row is None:
        return SaasScanLatestResponse(
            code=code,
            status="missing",
            message=f"No global {code.title()} scan has completed yet.",
        )
    return SaasScanLatestResponse(
        code=code,
        asOfDate=row.as_of_date.isoformat() if row.as_of_date else None,
        status=row.status,
        completedAt=row.completed_at,
        resultCount=int(row.result_count or 0),
        scanRunId=row.id,
    )


@router.get(
    "/scans/minervini/{code}/results",
    response_model=SaasScanResultsResponse,
)
async def get_standard_results(
    code: Literal["standard", "strict"],
    db: Annotated[AsyncSession, Depends(get_db)],
    as_of_date: Annotated[datetime.date | None, Query(alias="asOfDate")] = None,
    x_swyingify_access: Annotated[str | None, Header()] = None,
) -> SaasScanResultsResponse:
    if code == "strict":
        _require_access(
            x_swyingify_access,
            any_feature={"scanner.strict"},
        )

    latest = await _latest_global_run(db, code=code)
    if as_of_date is not None:
        latest_date = latest.as_of_date if latest is not None else None
        if latest_date is None or as_of_date != latest_date:
            _require_access(
                x_swyingify_access,
                any_feature={
                    "scanner.history.recent",
                    "scanner.history.full",
                },
            )

    run = await _latest_global_run(db, code=code, as_of_date=as_of_date)
    if run is None or run.status != "succeeded":
        # Latest (no asOfDate) should be poll-friendly for the public board.
        if as_of_date is None:
            latest_meta = await _latest_global_run(db, code=code)
            return SaasScanResultsResponse(
                code=code,
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
            detail=f"No successful {code.title()} scan found for the requested date.",
        )

    as_of = run.as_of_date.isoformat() if run.as_of_date else ""
    results = await _results_for_run(
        db,
        run_id=run.id,
        as_of_date=run.as_of_date,
        preset=code,
        limit=25,
    )

    return SaasScanResultsResponse(
        code=code,
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
