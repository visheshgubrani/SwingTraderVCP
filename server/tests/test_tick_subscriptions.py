import unittest
from unittest.mock import AsyncMock, MagicMock

from app.domain.market_regime import BENCHMARK_SYMBOL
from app.workers.tick_worker import _load_subscription_symbols, plan_tick_subscription_change


class TickSubscriptionProtectionTests(unittest.TestCase):
    def test_unsubscribe_keeps_open_position_and_benchmark(self) -> None:
        current = {"NSE:RELIANCE-EQ", "NSE:INFY-EQ", BENCHMARK_SYMBOL, "NSE:CHART-EQ"}
        mandatory = {"NSE:RELIANCE-EQ", BENCHMARK_SYMBOL}
        to_add, to_remove, new_current = plan_tick_subscription_change(
            "unsubscribe",
            {"NSE:RELIANCE-EQ", "NSE:CHART-EQ", BENCHMARK_SYMBOL},
            current,
            mandatory,
        )
        self.assertEqual(to_add, set())
        self.assertEqual(to_remove, {"NSE:CHART-EQ"})
        self.assertEqual(
            new_current,
            {"NSE:RELIANCE-EQ", "NSE:INFY-EQ", BENCHMARK_SYMBOL},
        )

    def test_replace_unions_mandatory_symbols(self) -> None:
        current = {"NSE:RELIANCE-EQ", "NSE:CHART-EQ", BENCHMARK_SYMBOL}
        mandatory = {"NSE:RELIANCE-EQ", "NSE:ARMED-EQ", BENCHMARK_SYMBOL}
        to_add, to_remove, new_current = plan_tick_subscription_change(
            "replace",
            {"NSE:CHART-EQ"},
            current,
            mandatory,
        )
        self.assertEqual(to_add, {"NSE:ARMED-EQ"})
        self.assertEqual(to_remove, set())
        self.assertEqual(
            new_current,
            {"NSE:RELIANCE-EQ", "NSE:CHART-EQ", "NSE:ARMED-EQ", BENCHMARK_SYMBOL},
        )

    def test_subscribe_still_adds_chart_symbols(self) -> None:
        current = {BENCHMARK_SYMBOL}
        to_add, to_remove, new_current = plan_tick_subscription_change(
            "subscribe",
            {"NSE:CHART-EQ"},
            current,
            {BENCHMARK_SYMBOL},
        )
        self.assertEqual(to_add, {"NSE:CHART-EQ"})
        self.assertEqual(to_remove, set())
        self.assertEqual(new_current, {BENCHMARK_SYMBOL, "NSE:CHART-EQ"})


class TickSubscriptionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_loads_armed_proposals_and_benchmark(self) -> None:
        result = MagicMock()
        result.fetchall.return_value = [
            ("NSE:BAJAJ-AUTO-EQ",),
            ("NSE:JINDALSAW-EQ",),
            ("NSE:OFSS-EQ",),
        ]
        db = AsyncMock()
        db.execute.return_value = result

        symbols = await _load_subscription_symbols(db)

        self.assertEqual(
            symbols,
            [
                "NSE:BAJAJ-AUTO-EQ",
                "NSE:JINDALSAW-EQ",
                "NSE:OFSS-EQ",
                BENCHMARK_SYMBOL,
            ],
        )
        sql = str(db.execute.await_args.args[0])
        self.assertIn("JOIN entry_legs", sql)
        self.assertIn("el.status = 'armed'", sql)
