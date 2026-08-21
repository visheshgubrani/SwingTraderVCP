"""Fyers-authoritative 5-minute reconciliation and volume-profile refresh."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import statistics
from collections import defaultdict
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from arq.connections import ArqRedis
from fyers_apiv3 import fyersModel
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.auth_service import AuthUnavailableError, get_valid_access_token
from app.services.bar_aggregator import REDIS_CHANNEL_5M_BARS


logger = logging.getLogger("intraday_bar_reconciliation")
IST_TZ = ZoneInfo("Asia/Kolkata")


def _robust_adv20(daily_volumes: list[int]) -> int:
    values = [float(value) for value in daily_volumes[-20:] if value > 0]
    if len(values) < 15:
        raise ValueError("At least 15 daily volumes are required for robust ADV20")
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    filtered = values if mad == 0 else [
        value for value in values if abs(value - median) <= 3 * mad
    ]
    if len(filtered) < 10:
        raise ValueError("MAD filtering left insufficient ADV20 observations")
    return int(statistics.median(filtered))


def build_volume_profile(
    candles: list[list[Any]],
    *,
    today_ist: dt.date,
) -> tuple[dt.date, int, list[dict[str, str]], int] | None:
    """Build a prior-session median cumulative-volume fraction profile."""
    sessions: dict[dt.date, list[tuple[dt.datetime, int]]] = defaultdict(list)
    for candle in candles:
        if len(candle) < 6:
            continue
        timestamp = dt.datetime.fromtimestamp(int(candle[0]), tz=dt.timezone.utc).astimezone(IST_TZ)
        if timestamp.date() >= today_ist:
            continue
        sessions[timestamp.date()].append((timestamp, int(candle[5])))
    valid = {
        session: sorted(values)
        for session, values in sessions.items()
        if len(values) >= 70
    }
    selected_dates = sorted(valid)[-30:]
    if len(selected_dates) < 15:
        return None

    totals = [sum(volume for _, volume in valid[session]) for session in selected_dates]
    adv20 = _robust_adv20(totals)
    fractions: dict[str, list[float]] = defaultdict(list)
    for session in selected_dates:
        cumulative = 0
        session_total = sum(volume for _, volume in valid[session])
        if session_total <= 0:
            continue
        for timestamp, volume in valid[session]:
            cumulative += volume
            fractions[timestamp.strftime("%H:%M")].append(
                cumulative / session_total
            )
    points = [
        {
            "time": label,
            "cumulative_fraction": str(Decimal(str(statistics.median(values))).quantize(Decimal("0.000001"))),
        }
        for label, values in sorted(fractions.items())
        if len(values) >= 15
    ]
    if not points:
        return None
    return selected_dates[-1], adv20, points, len(selected_dates)


async def reconcile_intraday_bars(ctx: dict[str, Any]) -> dict[str, Any]:
    redis: ArqRedis = ctx["redis"]
    try:
        token = await get_valid_access_token(redis)
    except AuthUnavailableError as exc:
        return {"status": "authentication_required", "error": str(exc)}

    async with async_session() as session:
        symbols = (
            await session.execute(
                text(
                    """
                    SELECT DISTINCT i.id AS instrument_id, i.fyers_symbol AS symbol,
                           EXISTS (
                               SELECT 1 FROM volume_profiles vp
                               WHERE vp.symbol = i.fyers_symbol AND vp.sessions_used >= 15
                           ) AS has_profile
                    FROM instruments i
                    WHERE i.id IN (
                        SELECT tp.instrument_id
                        FROM trade_proposals tp
                        JOIN entry_legs el ON el.proposal_id = tp.id
                        WHERE tp.status = 'approved'
                          AND el.status IN ('armed', 'trigger_observed')
                        UNION
                        SELECT instrument_id FROM positions
                        WHERE state IN ('open', 'trailing_active', 'exit_pending')
                    )
                    """
                )
            )
        ).mappings().all()
    if not symbols:
        return {"status": "no_symbols", "verified": 0}

    fyers = fyersModel.FyersModel(
        is_async=True,
        client_id=settings.fyers_app_id,
        token=token,
        log_path=settings.fyers_log_path,
    )
    now_ist = dt.datetime.now(IST_TZ)
    verified = 0
    failures = 0
    for item in symbols:
        start = now_ist.date() - dt.timedelta(days=2 if item["has_profile"] else 50)
        payload = {
            "symbol": item["symbol"],
            "resolution": "5",
            "date_format": "1",
            "range_from": start.isoformat(),
            "range_to": now_ist.date().isoformat(),
            "cont_flag": "1",
        }
        try:
            response = await fyers.history(data=payload)
            if response.get("s") != "ok" or not isinstance(response.get("candles"), list):
                raise RuntimeError(str(response.get("message") or "Fyers history failed"))
            candles = response["candles"]
            cumulative_by_date: dict[dt.date, int] = defaultdict(int)
            rows: list[dict[str, Any]] = []
            for candle in candles:
                if len(candle) < 6:
                    continue
                timestamp = dt.datetime.fromtimestamp(
                    int(candle[0]), tz=dt.timezone.utc
                ).astimezone(IST_TZ)
                if timestamp + dt.timedelta(minutes=5) > now_ist:
                    continue
                volume = int(candle[5])
                cumulative_by_date[timestamp.date()] += volume
                rows.append(
                    {
                        "instrument_id": item["instrument_id"],
                        "symbol": item["symbol"],
                        "bar_time": timestamp,
                        "open": Decimal(str(candle[1])),
                        "high": Decimal(str(candle[2])),
                        "low": Decimal(str(candle[3])),
                        "close": Decimal(str(candle[4])),
                        "volume": volume,
                        "cumulative": cumulative_by_date[timestamp.date()],
                        "raw": json.dumps(candle),
                    }
                )
            async with async_session() as session:
                previously_unverified = {
                    row.bar_time
                    for row in (
                        await session.execute(
                            text(
                                """
                                SELECT bar_time FROM five_minute_bars
                                WHERE symbol = :symbol
                                  AND reconciliation_status <> 'verified'
                                """
                            ),
                            {"symbol": item["symbol"]},
                        )
                    ).all()
                }
                if rows:
                    await session.execute(
                        text(
                            """
                            INSERT INTO five_minute_bars (
                                symbol, bar_time, open, high, low, close, volume,
                                cumulative_volume, reconciliation_status,
                                reconciled_at, reconciliation_details
                            ) VALUES (
                                :symbol, :bar_time, :open, :high, :low, :close,
                                :volume, :cumulative, 'verified', now(),
                                jsonb_build_object('source', 'fyers_history')
                            )
                            ON CONFLICT (symbol, bar_time) DO UPDATE SET
                                open = EXCLUDED.open, high = EXCLUDED.high,
                                low = EXCLUDED.low, close = EXCLUDED.close,
                                volume = EXCLUDED.volume,
                                cumulative_volume = EXCLUDED.cumulative_volume,
                                reconciliation_status = 'verified',
                                reconciled_at = now(),
                                reconciliation_details = EXCLUDED.reconciliation_details
                            """
                        ),
                        rows,
                    )
                    await session.execute(
                        text(
                            """
                            INSERT INTO market_candles (
                                instrument_id, timeframe, candle_start, open_price,
                                high_price, low_price, close_price, volume, source,
                                raw_payload
                            ) VALUES (
                                :instrument_id, '5m', :bar_time, :open, :high,
                                :low, :close, :volume, 'fyers_reconciled',
                                CAST(:raw AS jsonb)
                            )
                            ON CONFLICT (instrument_id, timeframe, candle_start)
                            DO UPDATE SET open_price = EXCLUDED.open_price,
                                high_price = EXCLUDED.high_price,
                                low_price = EXCLUDED.low_price,
                                close_price = EXCLUDED.close_price,
                                volume = EXCLUDED.volume, fetched_at = now(),
                                source = EXCLUDED.source, raw_payload = EXCLUDED.raw_payload
                            """
                        ),
                        rows,
                    )
                profile = build_volume_profile(candles, today_ist=now_ist.date())
                if profile is not None:
                    as_of_date, adv20, points, sessions_used = profile
                    await session.execute(
                        text(
                            """
                            INSERT INTO volume_profiles (
                                symbol, as_of_date, adv20_robust, bucket_medians,
                                sessions_used
                            ) VALUES (
                                :symbol, :as_of_date, :adv20,
                                CAST(:points AS jsonb), :sessions_used
                            )
                            ON CONFLICT (symbol, as_of_date) DO UPDATE SET
                                adv20_robust = EXCLUDED.adv20_robust,
                                bucket_medians = EXCLUDED.bucket_medians,
                                sessions_used = EXCLUDED.sessions_used
                            """
                        ),
                        {
                            "symbol": item["symbol"],
                            "as_of_date": as_of_date,
                            "adv20": adv20,
                            "points": json.dumps(points),
                            "sessions_used": sessions_used,
                        },
                    )
                await session.commit()

            # Republish only current-session bars that just became authoritative.
            for row in rows:
                if (
                    row["bar_time"] in previously_unverified
                    and row["bar_time"].date() == now_ist.date()
                ):
                    await redis.publish(
                        REDIS_CHANNEL_5M_BARS,
                        json.dumps(
                            {
                                "symbol": row["symbol"],
                                "bar_time": row["bar_time"].isoformat(),
                                "open": float(row["open"]),
                                "high": float(row["high"]),
                                "low": float(row["low"]),
                                "close": float(row["close"]),
                                "volume": row["volume"],
                                "cumulative_volume": row["cumulative"],
                            }
                        ),
                    )
                    verified += 1
        except Exception:
            failures += 1
            logger.exception("5-minute reconciliation failed for %s", item["symbol"])
        await asyncio.sleep(0.05)
    return {"status": "completed", "verified": verified, "failures": failures}
