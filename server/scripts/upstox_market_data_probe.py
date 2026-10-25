#!/usr/bin/env python3
"""Live read-only probe for the Upstox market-data provider (run on the VPS).

Validate before setting MARKET_DATA_PROVIDER=upstox:

    python scripts/upstox_market_data_probe.py --all

Checks (non-zero exit on any failure):
  config    token present, JWT/env expiry
  daily     v3 days/1 for RELIANCE, stored-convention timestamps (UTC midnight)
  intraday  v3 minutes/5 for the last session (75 bars, bar-start stamps)
  ltp       every mapped index + one equity resolves via instrument_token
  compare   Upstox vs stored Fyers daily OHLCV over recent sessions (adjustment basis)

Never prints the token, never writes Postgres (only the shared Redis budget).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import statistics
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import async_session  # noqa: E402
from app.domain.upstox_instrument_keys import UPSTOX_INDEX_KEYS  # noqa: E402
from app.redis_pool import create_async_redis  # noqa: E402
from app.services.fyers_history import fetch_history  # noqa: E402
from app.services.token_refresh import upstox_token_days_left  # noqa: E402
from app.services.upstox_market_data import IST, open_upstox_client  # noqa: E402

EQUITY = "NSE:RELIANCE-EQ"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("config", "daily", "intraday", "ltp", "compare", "all"):
        parser.add_argument(f"--{name}", action="store_true")
    parser.add_argument("--compare-symbols", type=int, default=50)
    parser.add_argument("--compare-sessions", type=int, default=60)
    return parser.parse_args()


def _last_session(today: dt.date) -> dt.date:
    holidays = set(settings.nse_trading_holidays)
    day = today - dt.timedelta(days=1)
    while day.weekday() >= 5 or day.isoformat() in holidays:
        day -= dt.timedelta(days=1)
    return day


async def main() -> int:
    args = _args()
    run_all = args.all or not any(
        (args.config, args.daily, args.intraday, args.ltp, args.compare)
    )
    failures: list[str] = []
    today = dt.datetime.now(IST).date()

    def report(name: str, ok: bool, detail: str) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            failures.append(name)

    if run_all or args.config:
        token = settings.upstox_analytics_token.strip()
        days = upstox_token_days_left(token, settings.upstox_analytics_token_expires_on, today)
        report(
            "config",
            bool(token),
            f"token={'set' if token else 'MISSING'} provider={settings.market_data_provider} "
            f"days_left={days if days is not None else 'unknown'}",
        )
        if not token:
            return 1

    redis = await create_async_redis()
    client = await open_upstox_client(redis)
    try:
        if run_all or args.daily:
            response = await fetch_history(redis, client, {
                "symbol": EQUITY, "resolution": "D",
                "range_from": (today - dt.timedelta(days=14)).isoformat(),
                "range_to": today.isoformat(),
            })
            candles = response.get("candles") or []
            midnight = all(
                dt.datetime.fromtimestamp(c[0], dt.timezone.utc).time() == dt.time() for c in candles
            )
            last = (
                dt.datetime.fromtimestamp(candles[-1][0], dt.timezone.utc).date().isoformat()
                if candles else "none"
            )
            report("daily", response.get("s") == "ok" and bool(candles) and midnight,
                   f"{len(candles)} candles, utc_midnight={midnight}, last={last}")

        if run_all or args.intraday:
            session = _last_session(today)
            response = await fetch_history(redis, client, {
                "symbol": EQUITY, "resolution": "5",
                "range_from": session.isoformat(), "range_to": session.isoformat(),
            })
            candles = response.get("candles") or []
            first = dt.datetime.fromtimestamp(candles[0][0], IST).time() if candles else None
            report("intraday", response.get("s") == "ok" and len(candles) == 75 and first == dt.time(9, 15),
                   f"{session}: {len(candles)} bars, first={first}")

        if run_all or args.ltp:
            symbols = [EQUITY, *UPSTOX_INDEX_KEYS]
            quotes = await client.fetch_ltp(redis, symbols)
            missing = sorted(set(symbols) - set(quotes))
            report("ltp", not missing, f"{len(quotes)}/{len(symbols)} resolved; missing={missing}")

        if run_all or args.compare:
            async with async_session() as db:
                rows = (await db.execute(text(
                    """
                    SELECT i.fyers_symbol, (c.candle_start AT TIME ZONE 'UTC')::date AS d,
                           c.close_price, c.volume
                    FROM market_candles c JOIN instruments i ON i.id = c.instrument_id
                    WHERE c.timeframe = '1d' AND c.source = 'fyers'
                      AND i.fyers_symbol IN (
                          SELECT fyers_symbol FROM instruments
                          WHERE active AND fyers_symbol LIKE '%-EQ'
                          ORDER BY fyers_symbol LIMIT :n)
                      AND c.candle_start >= now() - make_interval(days => :days)
                    """
                ), {"n": args.compare_symbols, "days": int(args.compare_sessions * 1.5)})).all()
            stored: dict[str, dict[dt.date, tuple[float, int]]] = {}
            for row in rows:
                stored.setdefault(row.fyers_symbol, {})[row.d] = (float(row.close_price), int(row.volume))
            close_dev: list[float] = []
            worst: list[tuple[float, str]] = []
            for symbol, by_date in stored.items():
                start, end = min(by_date), max(by_date)
                response = await fetch_history(redis, client, {
                    "symbol": symbol, "resolution": "D",
                    "range_from": start.isoformat(), "range_to": end.isoformat(),
                })
                for candle in response.get("candles") or []:
                    day = dt.datetime.fromtimestamp(candle[0], dt.timezone.utc).date()
                    if day in by_date and by_date[day][0] > 0:
                        dev = abs(float(candle[4]) - by_date[day][0]) / by_date[day][0] * 100
                        close_dev.append(dev)
                        worst.append((dev, f"{symbol} {day}"))
            worst.sort(reverse=True)
            median = statistics.median(close_dev) if close_dev else None
            ok = bool(close_dev) and median is not None and median < 0.05 and worst[0][0] < 1.0
            report("compare", ok,
                   f"{len(close_dev)} overlapping closes, median_dev={median}%, worst={worst[:5]}")
    finally:
        await client.close()
        await redis.aclose()

    print("OK" if not failures else f"FAILED: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
