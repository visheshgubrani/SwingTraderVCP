import datetime as dt
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.services.execution_engine import ExecutionBlockedError
from app.services.intraday_bar_reconciliation import build_volume_profile
from app.workers.entry_supervisor import _load_volume_profile


IST = ZoneInfo("Asia/Kolkata")


def regular_july_sessions(count: int) -> list[dt.date]:
    dates = [dt.date(2026, 7, day) for day in range(1, 32)]
    return [day for day in dates if day.weekday() < 5][:count]


class VolumeProfileTests(unittest.TestCase):
    def test_expected_fraction_is_normalized_per_session(self):
        candles: list[list[float | int]] = []
        for session_index, session in enumerate(regular_july_sessions(15)):
            per_bar_volume = 100 + (session_index * 50)
            for bucket in range(70):
                timestamp = dt.datetime.combine(
                    session,
                    dt.time(9, 15),
                    tzinfo=IST,
                ) + dt.timedelta(minutes=5 * bucket)
                candles.append(
                    [int(timestamp.timestamp()), 100, 101, 99, 100, per_bar_volume]
                )

        result = build_volume_profile(
            candles,
            evaluation_session_date=dt.date(2026, 8, 1),
        )
        self.assertIsNotNone(result)
        _, adv20, points, sessions_used = result  # type: ignore[misc]
        self.assertGreater(adv20, 0)
        self.assertEqual(sessions_used, 15)
        self.assertEqual(points[-1]["cumulative_fraction"], "1.000000")
        self.assertEqual(points[34]["cumulative_fraction"], "0.500000")

    def test_evaluated_session_cannot_contaminate_its_own_profile(self):
        evaluation_date = dt.date(2026, 8, 3)
        history: list[list[float | int]] = []
        for session in regular_july_sessions(20):
            for bucket in range(70):
                timestamp = dt.datetime.combine(
                    session, dt.time(9, 15), tzinfo=IST
                ) + dt.timedelta(minutes=5 * bucket)
                history.append([int(timestamp.timestamp()), 100, 101, 99, 100, 100])

        def evaluated_session(multiplier: int) -> list[list[float | int]]:
            rows: list[list[float | int]] = []
            for bucket in range(70):
                timestamp = dt.datetime.combine(
                    evaluation_date, dt.time(9, 15), tzinfo=IST
                ) + dt.timedelta(minutes=5 * bucket)
                rows.append(
                    [
                        int(timestamp.timestamp()),
                        100,
                        101,
                        99,
                        100,
                        multiplier * (bucket + 1),
                    ]
                )
            return rows

        baseline = build_volume_profile(
            history + evaluated_session(1),
            evaluation_session_date=evaluation_date,
        )
        changed = build_volume_profile(
            history + evaluated_session(10_000),
            evaluation_session_date=evaluation_date,
        )
        self.assertIsNotNone(baseline)
        self.assertEqual(baseline, changed)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "requires TEST_POSTGRES_URL (asyncpg)")
class VolumeProfilePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_ignores_muhurat_and_configured_holiday_but_rejects_stale_regular_profile(self):
        friday = dt.date(2026, 11, 6)
        muhurat = dt.date(2026, 11, 8)
        monday = dt.date(2026, 11, 9)
        holiday = dt.date(2026, 11, 10)
        wednesday = dt.date(2026, 11, 11)
        symbol = "NSE:EXAMPLE-EQ"
        october_holiday = dt.date(2026, 10, 20)
        history = [
            dt.date(2026, 10, 12) + dt.timedelta(days=offset) for offset in range(26)
        ]
        configured_holidays = [october_holiday.isoformat(), holiday.isoformat()]
        with patch.object(settings, "nse_trading_holidays", configured_holidays):
            candles = []
            for day in [*history, muhurat, holiday, wednesday]:
                volume = 1000 if day in {muhurat, holiday, wednesday, october_holiday} else 100
                start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
                for bucket in range(70):
                    timestamp = start + dt.timedelta(minutes=5 * bucket)
                    candles.append([int(timestamp.timestamp()), 100, 101, 99, 100, volume])
            profile = build_volume_profile(candles, evaluation_session_date=wednesday)
            self.assertIsNotNone(profile)
            assert profile is not None
            as_of, adv20, buckets, sessions_used = profile
            self.assertEqual((as_of, adv20, sessions_used), (friday, 7000, 19))

            engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
            self.addAsyncCleanup(engine.dispose)
            async with AsyncSession(engine) as db:
                # Temporary tables shadow the real tables for this session only.
                await db.execute(text("""
                    CREATE TEMP TABLE volume_profiles (
                        symbol text, as_of_date date, adv20_robust bigint,
                        bucket_medians jsonb, sessions_used integer
                    ) ON COMMIT DROP
                """))
                await db.execute(text("""
                    CREATE TEMP TABLE five_minute_bars (
                        symbol text, bar_time timestamptz, reconciliation_status text
                    ) ON COMMIT DROP
                """))
                await db.execute(text("""
                    INSERT INTO volume_profiles VALUES
                        (:symbol, :day, :adv20, CAST(:buckets AS jsonb), :sessions_used)
                """), [
                    {
                        "symbol": symbol,
                        "day": day,
                        "adv20": adv20,
                        "buckets": json.dumps(buckets),
                        "sessions_used": sessions_used,
                    }
                    for day in [friday, muhurat, holiday]
                ])
                insert_verified = text("""
                    INSERT INTO five_minute_bars VALUES (:symbol, :bar_time, 'verified')
                """)
                await db.execute(insert_verified, [
                    {
                        "symbol": symbol,
                        "bar_time": dt.datetime.combine(day, dt.time(14, 20), tzinfo=IST)
                        + dt.timedelta(minutes=5 * bucket),
                    }
                    for day, count in [(friday, 1), (muhurat, 14), (holiday, 1)]
                    for bucket in range(count)
                ])

                for evaluation_day in [monday, wednesday]:
                    point = await _load_volume_profile(
                        db, symbol=symbol,
                        bar_time=dt.datetime.combine(evaluation_day, dt.time(10), tzinfo=IST),
                        require_bar_fraction=True,
                    )
                    self.assertEqual(point.adv20_robust, 7000)
                    self.assertGreater(point.expected_bar_fraction, Decimal("0"))

                await db.execute(insert_verified, {
                    "symbol": symbol,
                    "bar_time": dt.datetime.combine(monday, dt.time(14, 20), tzinfo=IST),
                })
                with self.assertRaisesRegex(ExecutionBlockedError, "stale"):
                    await _load_volume_profile(
                        db, symbol=symbol,
                        bar_time=dt.datetime.combine(wednesday, dt.time(10), tzinfo=IST),
                        require_bar_fraction=True,
                    )


if __name__ == "__main__":
    unittest.main()
