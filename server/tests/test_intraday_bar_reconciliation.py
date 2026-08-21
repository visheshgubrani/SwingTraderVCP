import datetime as dt
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.services.intraday_bar_reconciliation import reconcile_intraday_bars


IST_TZ = ZoneInfo("Asia/Kolkata")


def mappings_all(rows):
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


class IntradayBarReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_eligible_symbols_returns_without_coroutine_error(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_all([])

        @asynccontextmanager
        async def fake_session():
            yield db

        with (
            patch(
                "app.services.intraday_bar_reconciliation.get_valid_access_token",
                new=AsyncMock(return_value="token"),
            ),
            patch(
                "app.services.intraday_bar_reconciliation.async_session",
                new=fake_session,
            ),
        ):
            result = await reconcile_intraday_bars({"redis": AsyncMock()})

        self.assertEqual(result, {"status": "no_symbols", "verified": 0})
        db.execute.assert_awaited_once()

    async def test_populated_symbols_are_persisted_without_replaying_absent_bars(self) -> None:
        symbol_db = AsyncMock()
        symbol_db.execute.return_value = mappings_all(
            [
                {
                    "instrument_id": uuid4(),
                    "symbol": "NSE:EXAMPLE-EQ",
                    "has_profile": True,
                }
            ]
        )

        persist_db = AsyncMock()
        no_previous_rows = MagicMock()
        no_previous_rows.all.return_value = []
        persist_db.execute.side_effect = [
            no_previous_rows,
            MagicMock(),
            MagicMock(),
        ]
        sessions = iter([symbol_db, persist_db])

        @asynccontextmanager
        async def fake_session():
            yield next(sessions)

        yesterday = dt.datetime.now(IST_TZ).date() - dt.timedelta(days=1)
        candle_time = dt.datetime.combine(
            yesterday,
            dt.time(10, 0),
            tzinfo=IST_TZ,
        )
        fyers = MagicMock()
        fyers.history = AsyncMock(
            return_value={
                "s": "ok",
                "candles": [
                    [int(candle_time.timestamp()), 100, 105, 99, 104, 1_000]
                ],
            }
        )
        redis = AsyncMock()

        with (
            patch(
                "app.services.intraday_bar_reconciliation.get_valid_access_token",
                new=AsyncMock(return_value="token"),
            ),
            patch(
                "app.services.intraday_bar_reconciliation.async_session",
                new=fake_session,
            ),
            patch(
                "app.services.intraday_bar_reconciliation.fyersModel.FyersModel",
                return_value=fyers,
            ),
        ):
            result = await reconcile_intraday_bars({"redis": redis})

        self.assertEqual(
            result,
            {"status": "completed", "verified": 0, "failures": 0},
        )
        fyers.history.assert_awaited_once()
        persist_db.commit.assert_awaited_once()
        redis.publish.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
