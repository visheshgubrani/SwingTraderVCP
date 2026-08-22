import datetime as dt
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.routers.automation import _market_data_status, get_entry_supervisor_status


class MarketDataStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = dt.datetime(2026, 8, 21, 8, 0, tzinfo=dt.timezone.utc)

    def heartbeat(self, *, status: str, age_seconds: int, symbols: int = 4) -> str:
        return json.dumps(
            {
                "status": status,
                "timestamp": (self.now - dt.timedelta(seconds=age_seconds)).isoformat(),
                "symbol_count": symbols,
            }
        )

    def test_ready_heartbeat(self) -> None:
        result = _market_data_status(
            self.heartbeat(status="ready", age_seconds=10),
            now=self.now,
        )
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["ready"])
        self.assertEqual(result["symbol_count"], 4)

    def test_stale_ready_heartbeat_fails_closed(self) -> None:
        result = _market_data_status(
            self.heartbeat(status="ready", age_seconds=31),
            now=self.now,
        )
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result["ready"])

    def test_stopped_heartbeat_is_not_ready(self) -> None:
        result = _market_data_status(
            self.heartbeat(status="stopped", age_seconds=5, symbols=0),
            now=self.now,
        )
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result["ready"])

    def test_missing_heartbeat_is_offline(self) -> None:
        self.assertEqual(
            _market_data_status(None, now=self.now),
            {
                "status": "offline",
                "timestamp": None,
                "symbol_count": 0,
                "ready": False,
            },
        )


class EntrySupervisorStatusApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_includes_market_data_readiness(self) -> None:
        armed_rows = MagicMock()
        armed_rows.fetchall.return_value = []
        ledger_rows = MagicMock()
        ledger_rows.fetchall.return_value = []
        trigger_rows = MagicMock()
        trigger_rows.mappings.return_value.all.return_value = []
        zero_count = MagicMock()
        zero_count.scalar_one.return_value = 0
        db = AsyncMock()
        db.execute.side_effect = [armed_rows, ledger_rows, trigger_rows, zero_count]

        now = dt.datetime.now(dt.timezone.utc)
        redis = AsyncMock()
        redis.get.side_effect = [
            json.dumps({"status": "running", "timestamp": now.isoformat()}),
            json.dumps(
                {
                    "status": "ready",
                    "timestamp": now.isoformat(),
                    "symbol_count": 4,
                }
            ),
        ]
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(redis=redis))
        )

        response = await get_entry_supervisor_status(request, db)

        self.assertEqual(response["status"], "active")
        self.assertEqual(response["market_data"]["status"], "ready")
        self.assertTrue(response["market_data"]["ready"])
        self.assertEqual(response["market_data"]["symbol_count"], 4)

    async def test_closed_window_legs_are_not_counted_as_armed(self) -> None:
        stale_armed_rows = MagicMock()
        stale_armed_rows.fetchall.return_value = [
            SimpleNamespace(
                _mapping={
                    "id": "00000000-0000-0000-0000-000000000001",
                    "leg_index": 1,
                    "risk_allocation_pct": 1.0,
                    "status": "armed",
                    "trigger_price": 100.0,
                    "chase_ceiling": 102.0,
                    "symbol": "NSE:OFSS-EQ",
                    "entry_template": "single",
                    # Far in the past, so the window is closed under any clock.
                    "eligible_session_end": dt.date(2020, 1, 1),
                }
            )
        ]
        ledger_rows = MagicMock()
        ledger_rows.fetchall.return_value = []
        trigger_rows = MagicMock()
        trigger_rows.mappings.return_value.all.return_value = []
        zero_count = MagicMock()
        zero_count.scalar_one.return_value = 0
        db = AsyncMock()
        db.execute.side_effect = [stale_armed_rows, ledger_rows, trigger_rows, zero_count]

        now = dt.datetime.now(dt.timezone.utc)
        redis = AsyncMock()
        redis.get.side_effect = [
            json.dumps({"status": "running", "timestamp": now.isoformat()}),
            json.dumps(
                {
                    "status": "ready",
                    "timestamp": now.isoformat(),
                    "symbol_count": 4,
                }
            ),
        ]
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(redis=redis))
        )

        response = await get_entry_supervisor_status(request, db)

        # The armed leg's window has closed (eligible_session_end in 2020),
        # so it must not be reported as armed.
        self.assertEqual(response["armed_legs_count"], 0)
        self.assertEqual(response["armed_legs"], [])


if __name__ == "__main__":
    unittest.main()
