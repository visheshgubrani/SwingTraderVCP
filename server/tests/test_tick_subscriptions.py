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
        self.assertIn("el.status IN", sql)
        self.assertIn("'waiting_for_reset'", sql)


class TickWorkerResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_error_triggers_invalidation_and_reconnect(self) -> None:
        from app.workers.tick_worker import (
            TickWorkerState,
            _on_error_factory,
            _safe_close_fyers_socket,
        )
        import threading
        import asyncio

        state = TickWorkerState("test-worker")
        state.status = "ready"
        session_reconnect_event = threading.Event()
        loop = asyncio.get_running_loop()
        redis = AsyncMock()

        on_error = _on_error_factory(state, session_reconnect_event, loop, redis)
        on_error({"type": "cn", "code": -99, "message": "Token is expired", "s": "error"})

        self.assertEqual(state.status, "auth_required")
        self.assertTrue(session_reconnect_event.is_set())

        # Let the loop run any scheduled task
        await asyncio.sleep(0.01)
        redis.delete.assert_awaited()

    def test_safe_close_handles_close_connection_and_exceptions(self) -> None:
        from app.workers.tick_worker import _safe_close_fyers_socket

        mock_ws = MagicMock()
        mock_ws.close_connection.side_effect = Exception("SDK noise")
        # Should not raise
        _safe_close_fyers_socket(mock_ws)
        mock_ws.close_connection.assert_called_once()

