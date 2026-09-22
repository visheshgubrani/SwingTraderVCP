import asyncio
import datetime
import json
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.auth_service import persist_and_cache_fyers_token
from app.services.execution_engine import (
    ExecutionBlockedError,
    _complete_paper_submission,
    complete_paper_entry_fill,
)
from app.services.journal_processor import _claim_pending_events, _count_pending
from app.workers.position_monitor import PositionMonitorRuntime
from app.workers.tick_worker import TickWorkerState, _on_message_factory


class FakeResult:
    def __init__(self, row=None, rows=None, scalar=None):
        self.row = row
        self.rows = rows or ([] if row is None else [row])
        self._scalar = scalar

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def one(self):
        if self.row is None:
            raise AssertionError("Expected one row")
        return self.row

    def all(self):
        return self.rows

    def scalar_one(self):
        if self._scalar is not None:
            return self._scalar
        return len(self.rows)


class BatchBSecurityTests(unittest.IsolatedAsyncioTestCase):
    # --- MD-001: Tick thread safety and non-positive tick filtering ---
    def test_on_message_filters_non_positive_and_uses_call_soon_threadsafe(self) -> None:
        publish_queue = asyncio.Queue()
        mock_loop = MagicMock()
        state = TickWorkerState("worker-1")
        callback = _on_message_factory(publish_queue, mock_loop, state)

        # 1. Non-positive tick should be dropped immediately without queueing
        callback({"symbol": "NSE:TEST-EQ", "ltp": 0.0})
        callback({"symbol": "NSE:TEST-EQ", "ltp": -10.5})
        self.assertEqual(mock_loop.call_soon_threadsafe.call_count, 0)

        # 2. Valid positive tick should be scheduled via loop.call_soon_threadsafe
        callback({"symbol": "NSE:TEST-EQ", "ltp": 250.75})
        self.assertEqual(mock_loop.call_soon_threadsafe.call_count, 1)

        # Execute scheduled callback to verify queue delivery
        scheduled_func = mock_loop.call_soon_threadsafe.call_args[0][0]
        scheduled_func()
        self.assertEqual(publish_queue.qsize(), 1)
        item = publish_queue.get_nowait()
        self.assertEqual(item["symbol"], "NSE:TEST-EQ")
        self.assertEqual(item["ltp"], 250.75)

    # --- MD-003: Position monitor tick freshness ---
    async def test_position_monitor_drops_stale_or_non_positive_ticks(self) -> None:
        runtime = PositionMonitorRuntime("worker-pm-1")
        mock_pos = MagicMock()
        mock_pos.state = "open"
        runtime.positions_by_symbol["NSE:TEST-EQ"] = [mock_pos]

        redis = AsyncMock()

        with patch("app.workers.position_monitor.process_position_tick", new_callable=AsyncMock) as mock_process:
            # 1. Non-positive tick is dropped
            await runtime.handle_tick(redis, {"symbol": "NSE:TEST-EQ", "ltp": 0})
            mock_process.assert_not_called()

            # 2. Stale tick (> 10s) is dropped
            stale_time = (
                datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=20)
            ).isoformat()
            await runtime.handle_tick(
                redis,
                {"symbol": "NSE:TEST-EQ", "ltp": 100, "received_at": stale_time},
            )
            mock_process.assert_not_called()

            # 3. Future tick (< -5s) is dropped
            future_time = (
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=15)
            ).isoformat()
            await runtime.handle_tick(
                redis,
                {"symbol": "NSE:TEST-EQ", "ltp": 100, "received_at": future_time},
            )
            mock_process.assert_not_called()

            # 4. Missing received_at is dropped
            await runtime.handle_tick(
                redis,
                {"symbol": "NSE:TEST-EQ", "ltp": 100},
            )
            mock_process.assert_not_called()

    # --- MD-003: Paper submission stale LTP rejection ---
    async def test_paper_submission_rejects_stale_or_non_positive_cached_ltp(self) -> None:
        db = AsyncMock()
        redis = AsyncMock()
        snapshot = {"symbol": "NSE:TEST-EQ", "id": uuid4()}

        # 1. Missing LTP
        redis.get.return_value = None
        result = await _complete_paper_submission(db, redis, snapshot=snapshot, fill_price=None)
        self.assertEqual(result.outcome, "rejected")
        self.assertIn("requires a fill price or fresh LTP", result.message)

        # 2. Non-positive LTP in Redis
        redis.get.return_value = json.dumps({
            "ltp": 0.0,
            "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        result = await _complete_paper_submission(db, redis, snapshot=snapshot, fill_price=None)
        self.assertEqual(result.outcome, "rejected")
        self.assertIn("non-positive", result.message)

        # 3. Missing received_at in Redis
        redis.get.return_value = json.dumps({
            "ltp": 500.0,
        })
        result = await _complete_paper_submission(db, redis, snapshot=snapshot, fill_price=None)
        self.assertEqual(result.outcome, "rejected")
        self.assertIn("missing received_at", result.message)

        # 4. Stale LTP (> 15s) in Redis
        stale_time = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=25)
        ).isoformat()
        redis.get.return_value = json.dumps({
            "ltp": 500.0,
            "received_at": stale_time,
        })
        result = await _complete_paper_submission(db, redis, snapshot=snapshot, fill_price=None)
        self.assertEqual(result.outcome, "rejected")
        self.assertIn("stale", result.message)

    # --- AUTH-002: Token-save unification ---
    async def test_persist_and_cache_fyers_token_updates_db_and_redis(self) -> None:
        db = AsyncMock()
        redis = AsyncMock()
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=3600)

        with patch("app.services.auth_service.save_fyers_token", new_callable=AsyncMock) as mock_save_db:
            await persist_and_cache_fyers_token(
                db,
                redis,
                access_token="test-access-token-123",
                refresh_token="test-refresh-token-456",
                expires_at=expires_at,
                expires_in=3600,
            )

            mock_save_db.assert_awaited_once_with(
                db, "test-access-token-123", "test-refresh-token-456", expires_at
            )
            # Verify Redis token, expiry, and health keys set
            self.assertTrue(redis.set.await_count >= 3)
            set_calls = {call.args[0]: call.args[1] for call in redis.set.await_args_list}
            self.assertEqual(set_calls["auth:fyers:access_token"], "test-access-token-123")
            self.assertEqual(set_calls["auth:fyers:expires_at"], expires_at.isoformat())
            self.assertEqual(set_calls["auth:fyers:healthy"], "1")

    # --- JRN-001: Journal outbox reclaim ---
    async def test_journal_outbox_reclaim_query_includes_stranded_processing(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(scalar=3),
            FakeResult(rows=[{"id": uuid4(), "order_fill_id": uuid4(), "position_id": uuid4(), "fill_side": "entry"}]),
        ]

        count = await _count_pending(db)
        self.assertEqual(count, 3)
        count_sql = str(db.execute.await_args_list[0].args[0])
        self.assertIn("status = 'processing' AND processed_at < now() - interval '5 minutes'", count_sql)

        claimed = await _claim_pending_events(db, limit=10)
        self.assertEqual(len(claimed), 1)
        claim_sql = str(db.execute.await_args_list[1].args[0])
        self.assertIn("status = 'processing' AND processed_at < now() - interval '5 minutes'", claim_sql)

    # --- P10-002: Block manual complete_paper_entry_fill during active P10 ---
    async def test_complete_paper_entry_fill_blocked_during_p10_paper_mode(self) -> None:
        db = AsyncMock()
        db.execute.return_value = FakeResult({"stage": "paper"})

        with patch("app.services.execution_engine.settings") as mock_settings:
            mock_settings.execution_mode = "paper"
            with self.assertRaises(ExecutionBlockedError) as ctx:
                await complete_paper_entry_fill(
                    db,
                    order_intent_id=uuid4(),
                    position_id=uuid4(),
                    fill_price=Decimal("150.0"),
                    quantity=10,
                )
            self.assertIn("Manual paper entry fills are disabled while P10", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
