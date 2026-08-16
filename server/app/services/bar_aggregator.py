"""5-Minute Bar Aggregator Service.

Aggregates real-time ticks from the single Fyers market WebSocket into completed 5-minute OHLCV bars,
persists them into `five_minute_bars`, and publishes bar completion events to Redis channel `market:bars:5m`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from sqlalchemy import text

from app.database import async_session


logger = logging.getLogger(__name__)
IST_TZ = ZoneInfo("Asia/Kolkata")
REDIS_CHANNEL_5M_BARS = "market:bars:5m"


@dataclass
class BarBucket:
    symbol: str
    bar_time: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0
    cumulative_volume: int = 0
    ticks_count: int = 0


class FiveMinuteBarAggregator:
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis
        self.active_buckets: dict[str, BarBucket] = {}
        self.symbol_daily_cumulative: dict[str, int] = {}
        self.symbol_session_dates: dict[str, dt.date] = {}

    def _get_bucket_time(self, timestamp: dt.datetime | None = None) -> dt.datetime:
        """Aligns datetime to nearest 5-minute floor bucket (e.g. 09:15:00, 09:20:00)."""
        now_ist = (timestamp or dt.datetime.now(dt.timezone.utc)).astimezone(IST_TZ)
        minute_floor = (now_ist.minute // 5) * 5
        return dt.datetime(
            now_ist.year,
            now_ist.month,
            now_ist.day,
            now_ist.hour,
            minute_floor,
            0,
            tzinfo=IST_TZ,
        )

    async def process_tick(self, tick: dict[str, Any]) -> BarBucket | None:
        """Processes a single tick and returns the completed BarBucket if a bar closed."""
        symbol = tick.get("symbol")
        ltp = tick.get("ltp")
        if not symbol or ltp is None:
            return None

        price = Decimal(str(ltp))
        tick_vol = int(tick.get("volume") or 0)
        
        # Determine timestamp
        raw_ts = tick.get("timestamp")
        if isinstance(raw_ts, (int, float)):
            ts = dt.datetime.fromtimestamp(raw_ts, tz=dt.timezone.utc)
        elif isinstance(raw_ts, str):
            try:
                ts = dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                ts = dt.datetime.now(dt.timezone.utc)
        else:
            ts = dt.datetime.now(dt.timezone.utc)

        bucket_time = self._get_bucket_time(ts)
        session_date = bucket_time.date()

        # Ignore out-of-session data. Fyers may emit cached/pre-open quotes;
        # those must not become eligible trigger bars.
        if bucket_time.time() < dt.time(9, 15) or bucket_time.time() >= dt.time(15, 30):
            return None

        previous_session = self.symbol_session_dates.get(symbol)
        if previous_session is not None and session_date < previous_session:
            logger.warning("Ignoring out-of-order tick for %s from %s", symbol, session_date)
            return None

        if previous_session != session_date:
            stale = self.active_buckets.pop(symbol, None)
            if stale is not None:
                await self._persist_and_publish_bar(stale)
            self.symbol_session_dates[symbol] = session_date
            self.symbol_daily_cumulative[symbol] = 0

        previous_cumulative = self.symbol_daily_cumulative.get(symbol, 0)
        if tick_vol < previous_cumulative:
            logger.warning(
                "Ignoring regressing cumulative volume for %s: %s < %s",
                symbol,
                tick_vol,
                previous_cumulative,
            )
            volume_delta = 0
            tick_vol = previous_cumulative
        else:
            volume_delta = tick_vol - previous_cumulative
        self.symbol_daily_cumulative[symbol] = tick_vol

        # Check if previous bucket for this symbol completed
        existing = self.active_buckets.get(symbol)
        completed_bar: BarBucket | None = None

        if existing and existing.bar_time < bucket_time:
            # Previous bar closed!
            completed_bar = existing
            await self._persist_and_publish_bar(completed_bar)
            del self.active_buckets[symbol]
        elif existing and existing.bar_time > bucket_time:
            logger.warning("Ignoring out-of-order bucket for %s at %s", symbol, bucket_time)
            return None

        # Update or create active bucket
        if symbol not in self.active_buckets:
            # Start new bucket
            self.active_buckets[symbol] = BarBucket(
                symbol=symbol,
                bar_time=bucket_time,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume_delta,
                cumulative_volume=tick_vol,
                ticks_count=1,
            )
        else:
            bucket = self.active_buckets[symbol]
            bucket.high = max(bucket.high, price)
            bucket.low = min(bucket.low, price)
            bucket.close = price
            bucket.ticks_count += 1
            bucket.volume += volume_delta
            bucket.cumulative_volume = tick_vol

        return completed_bar

    async def _persist_and_publish_bar(self, bar: BarBucket) -> None:
        """Persists completed 5m bar to Postgres and publishes to Redis."""
        bar_dict = {
            "symbol": bar.symbol,
            "bar_time": bar.bar_time.isoformat(),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": bar.volume,
            "cumulative_volume": bar.cumulative_volume,
        }

        # Postgres is authoritative. Publish only after the completed bar is
        # durably committed so a subscriber can always reconstruct the event.
        try:
            async with async_session() as db:
                stmt = text("""
                    INSERT INTO five_minute_bars (
                        symbol, bar_time, open, high, low, close, volume,
                        cumulative_volume, reconciliation_status
                    )
                    VALUES (
                        :symbol, :bar_time, :open, :high, :low, :close, :volume,
                        :cumulative_volume, 'pending'
                    )
                    ON CONFLICT (symbol, bar_time) DO UPDATE
                    SET open = EXCLUDED.open, high = EXCLUDED.high,
                        low = EXCLUDED.low, close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        cumulative_volume = EXCLUDED.cumulative_volume,
                        reconciliation_status = 'pending', reconciled_at = NULL;
                """)
                await db.execute(stmt, {
                    "symbol": bar.symbol,
                    "bar_time": bar.bar_time,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "cumulative_volume": bar.cumulative_volume,
                })
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to persist 5m bar for {bar.symbol}: {e}")
            raise

        try:
            await self.redis.publish(REDIS_CHANNEL_5M_BARS, json.dumps(bar_dict))
        except Exception as e:
            # Redis loss is recoverable because the bar is durable; the entry
            # supervisor's startup sweep replays unprocessed bars.
            logger.error(f"Redis publish error for 5m bar {bar.symbol}: {e}")
