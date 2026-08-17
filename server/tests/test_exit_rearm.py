"""Tests for exit intent rejection/cancellation position re-arming (TRD-002 / OG-002)."""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.services.execution_engine import (
    ExecutionBlockedError,
    _record_definite_rejection,
    restore_rejected_exit_position,
    submit_live_exit_intent,
)
from app.services.order_gateway import (
    process_order_message,
)


class FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or ([] if row is None else [row])

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


class NoWaitLimiter:
    async def acquire(self) -> None:
        return None


class ExitRearmTests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_rejected_exit_position_open_state(self):
        """Verify restore_rejected_exit_position restores position from exit_pending to open using prior event."""
        position_id = uuid4()
        order_intent_id = uuid4()
        db = AsyncMock()

        db.execute.side_effect = [
            # 1. SELECT position
            FakeResult({
                "id": position_id,
                "state": "exit_pending",
                "open_quantity": 50,
                "trailing_stop": None,
                "t2_filled_shares": 0,
                "t2_shares": 0,
            }),
            # 2. SELECT from_state FROM position_events
            FakeResult({"from_state": "open"}),
            # 3. UPDATE positions SET state = 'open'
            FakeResult(),
            # 4. INSERT INTO position_events
            FakeResult(),
            # 5. INSERT INTO system_events
            FakeResult(),
        ]

        restored = await restore_rejected_exit_position(
            db,
            position_id=position_id,
            order_intent_id=order_intent_id,
            trade_instruction_id=None,
            reason="Order rejected by risk validator",
            details={"code": "RISK_LIMIT"},
        )

        self.assertEqual(restored, "open")
        self.assertEqual(db.execute.await_count, 5)

        # Check UPDATE query
        update_call = db.execute.await_args_list[2]
        update_sql = str(update_call.args[0])
        params = update_call.args[1]
        self.assertIn("SET state = :restored_state", update_sql)
        self.assertEqual(params["restored_state"], "open")
        self.assertEqual(params["position_id"], position_id)

        # Check system_events query
        event_call = db.execute.await_args_list[4]
        event_params = event_call.args[1]
        self.assertEqual(event_params["event_type"], "exit_intent_rejected_position_rearmed")
        self.assertEqual(event_params["position_id"], position_id)

    async def test_restore_rejected_exit_position_trailing_state_from_event(self):
        """Verify restore_rejected_exit_position uses exact from_state ('trailing_active') from position_events."""
        position_id = uuid4()
        order_intent_id = uuid4()
        db = AsyncMock()

        db.execute.side_effect = [
            # 1. SELECT position
            FakeResult({
                "id": position_id,
                "state": "exit_pending",
                "open_quantity": 25,
                "trailing_stop": Decimal("150.00"),
                "t2_filled_shares": 25,
                "t2_shares": 25,
            }),
            # 2. SELECT from_state FROM position_events
            FakeResult({"from_state": "trailing_active"}),
            # 3. UPDATE positions SET state = 'trailing_active'
            FakeResult(),
            # 4. INSERT INTO position_events
            FakeResult(),
            # 5. INSERT INTO system_events
            FakeResult(),
        ]

        restored = await restore_rejected_exit_position(
            db,
            position_id=position_id,
            order_intent_id=order_intent_id,
            trade_instruction_id=None,
            reason="Trailing exit rejected",
            details={"code": "BROKER_REJECT"},
        )

        self.assertEqual(restored, "trailing_active")
        update_call = db.execute.await_args_list[2]
        params = update_call.args[1]
        self.assertEqual(params["restored_state"], "trailing_active")

    async def test_restore_rejected_exit_position_p10_without_t2_restores_open(self):
        """Verify P10 position in open state does not falsely restore to trailing_active."""
        position_id = uuid4()
        order_intent_id = uuid4()
        db = AsyncMock()

        db.execute.side_effect = [
            # 1. SELECT position (P10 position, T2 not yet filled, trailing_stop is None)
            FakeResult({
                "id": position_id,
                "state": "exit_pending",
                "open_quantity": 100,
                "trailing_stop": None,
                "t2_filled_shares": 0,
                "t2_shares": 50,
            }),
            # 2. SELECT from_state FROM position_events -> was 'open'
            FakeResult({"from_state": "open"}),
            # 3. UPDATE positions SET state = 'open'
            FakeResult(),
            # 4. INSERT INTO position_events
            FakeResult(),
            # 5. INSERT INTO system_events
            FakeResult(),
        ]

        restored = await restore_rejected_exit_position(
            db,
            position_id=position_id,
            order_intent_id=order_intent_id,
            trade_instruction_id=None,
            reason="Stop exit rejected",
            details={"code": "RISK_REJECT"},
        )

        self.assertEqual(restored, "open")
        update_call = db.execute.await_args_list[2]
        self.assertEqual(update_call.args[1]["restored_state"], "open")

    async def test_record_definite_rejection_branches_on_intent_type(self):
        """Verify _record_definite_rejection restores exit positions and cancels entry positions."""
        position_id = uuid4()
        order_intent_id = uuid4()
        db = AsyncMock()

        # Mock for exit intent
        db.execute.side_effect = [
            # 1. UPDATE order_intents
            FakeResult(),
            # 2. SELECT position for update
            FakeResult({
                "id": position_id,
                "state": "exit_pending",
                "open_quantity": 100,
                "trailing_stop": None,
                "t2_filled_shares": 0,
                "t2_shares": 0,
            }),
            # 3. SELECT from_state FROM position_events
            FakeResult({"from_state": "open"}),
            # 4. UPDATE positions
            FakeResult(),
            # 5. INSERT position_events
            FakeResult(),
            # 6. INSERT system_events
            FakeResult(),
            # 7. INSERT order_events (submission_event)
            FakeResult(),
        ]

        snapshot = {
            "id": order_intent_id,
            "position_id": position_id,
            "trade_instruction_id": None,
            "intent_type": "exit",
        }

        await _record_definite_rejection(
            db,
            snapshot=snapshot,
            payload={"s": "error"},
            message="Broker rejected exit order",
        )

        # Check that positions was updated to open
        pos_update_call = db.execute.await_args_list[3]
        self.assertIn("SET state = :restored_state", str(pos_update_call.args[0]))
        self.assertEqual(pos_update_call.args[1]["restored_state"], "open")

    async def test_gateway_order_rejection_rearms_exit_position(self):
        """Verify process_order_message re-arms position when broker sends order rejected/cancelled (OG-002)."""
        position_id = uuid4()
        order_intent_id = uuid4()
        event_id = uuid4()
        db = AsyncMock()

        db.execute.side_effect = [
            # 1. _find_intent
            FakeResult({
                "id": order_intent_id,
                "position_id": position_id,
                "trade_instruction_id": None,
                "intent_type": "stop_loss",
                "quantity": 50,
                "status": "submitted",
                "fyers_async_id": "ASYNC123",
                "fyers_order_id": "ORD123",
                "exchange_order_id": "EX123",
            }),
            # 2. _record_order_event -> INSERT order_events RETURNING id
            FakeResult({"id": event_id}),
            # 3. UPDATE order_intents
            FakeResult(),
            # 4. restore_rejected_exit_position -> SELECT position
            FakeResult({
                "id": position_id,
                "state": "exit_pending",
                "open_quantity": 50,
                "trailing_stop": None,
                "t2_filled_shares": 0,
                "t2_shares": 0,
            }),
            # 5. SELECT from_state FROM position_events
            FakeResult({"from_state": "open"}),
            # 6. UPDATE positions
            FakeResult(),
            # 7. INSERT position_events
            FakeResult(),
            # 8. INSERT system_events
            FakeResult(),
        ]

        order_message = {
            "orders": {
                "id_fyers": "ASYNC123",
                "orderNumber": "ORD123",
                "exchangeOrderNo": "EX123",
                "status": 5,  # 5 = rejected in Fyers
                "message": "Insufficient margin or market closed",
            }
        }

        result = await process_order_message(db, order_message)
        self.assertTrue(result)

        # Check position state restored query is present
        pos_update_call = next(
            c for c in db.execute.await_args_list if "SET state = :restored_state" in str(c.args[0])
        )
        self.assertEqual(pos_update_call.args[1]["restored_state"], "open")

    async def test_paper_exit_missing_ltp_rearms_position(self):
        """Verify paper exit submission with missing LTP rejects and restores position to open."""
        position_id = uuid4()
        order_intent_id = uuid4()
        db = AsyncMock()
        redis = AsyncMock()

        # Redis has no LTP cached for the symbol
        redis.get.return_value = None

        db.execute.side_effect = [
            # 1. _find_submission_snapshot
            FakeResult({
                "id": order_intent_id,
                "position_id": position_id,
                "trade_instruction_id": None,
                "intent_type": "stop_loss_exit",
                "side": "sell",
                "quantity": 20,
                "product_type": "CNC",
                "order_type": "market",
                "limit_price": None,
                "status": "created",
                "execution_mode": "paper",
                "symbol": "NSE:TCS-EQ",
                "position_state": "exit_pending",
                "position_side": "long",
                "open_quantity": 20,
                "current_stop_loss": Decimal("3500.00"),
                "lot_size": 1,
                "tick_size": Decimal("0.05"),
                "entry_leg_id": None,
            }),
            # 2. ensure_orders_allowed (first check before limiter)
            FakeResult({"enabled": False, "reason": None}),
            # 3. ensure_orders_allowed (second check after limiter)
            FakeResult({"enabled": False, "reason": None}),
            # 4. claim intent -> UPDATE order_intents SET status = 'submission_pending'
            FakeResult({"id": order_intent_id}),
            # 5. _record_definite_rejection -> UPDATE order_intents SET status = 'rejected'
            FakeResult(),
            # 6. restore_rejected_exit_position -> SELECT position FOR UPDATE
            FakeResult({
                "id": position_id,
                "state": "exit_pending",
                "open_quantity": 20,
                "trailing_stop": None,
                "t2_filled_shares": 0,
                "t2_shares": 0,
            }),
            # 7. SELECT from_state FROM position_events
            FakeResult({"from_state": "open"}),
            # 8. UPDATE positions SET state = 'open'
            FakeResult(),
            # 9. INSERT position_events
            FakeResult(),
            # 10. INSERT system_events
            FakeResult(),
            # 11. INSERT submission_event (order_events)
            FakeResult(),
        ]

        with patch("app.services.execution_engine.ensure_order_gateway_ready", new=AsyncMock()):
            result = await submit_live_exit_intent(
                db,
                redis,
                order_intent_id=order_intent_id,
                fill_price=None,
                rate_limiter=NoWaitLimiter(),
            )

        self.assertEqual(result.outcome, "rejected")
        self.assertIn("requires a fill price or fresh LTP", result.message)

        # Check position state restored query is executed
        pos_update_call = next(
            c for c in db.execute.await_args_list if "SET state = :restored_state" in str(c.args[0])
        )
        self.assertEqual(pos_update_call.args[1]["restored_state"], "open")
        self.assertEqual(pos_update_call.args[1]["position_id"], position_id)

    async def test_pre_claim_blocked_submit_rearms_position(self):
        """Verify submit_live_exit_intent rejects created intent and restores position if blocked pre-claim."""
        position_id = uuid4()
        order_intent_id = uuid4()
        db = AsyncMock()
        redis = AsyncMock()

        db.execute.side_effect = [
            # 1. _load_live_intent_for_submission
            FakeResult({
                "id": order_intent_id,
                "idempotency_key": f"position:{position_id}:exit:v1",
                "position_id": position_id,
                "trade_instruction_id": None,
                "proposal_id": None,
                "entry_leg_id": None,
                "intent_type": "stop_loss_exit",
                "side": "sell",
                "quantity": 30,
                "product_type": "CNC",
                "order_type": "market",
                "limit_price": None,
                "trigger_price": None,
                "status": "created",
                "execution_mode": "paper",
                "symbol": "NSE:INFY-EQ",
                "position_state": "exit_pending",
                "position_side": "long",
                "open_quantity": 30,
                "manual_confirmed_at": None,
                "proposal_status": None,
                "proposal_hash": None,
                "live_eligible": None,
                "entry_session_date": None,
                "expected_proposal_hash": None,
                "entry_leg_status": None,
                "entry_leg_index": None,
                "eligible_session_start": None,
                "eligible_session_end": None,
            }),
            # 2. ensure_orders_allowed -> Kill switch engaged!
            FakeResult({"enabled": True, "reason": "Emergency halt"}),
            # 3. Pre-claim failure handler -> UPDATE order_intents SET status = 'rejected'
            FakeResult(),
            # 4. restore_rejected_exit_position -> SELECT position
            FakeResult({
                "id": position_id,
                "state": "exit_pending",
                "open_quantity": 30,
                "trailing_stop": None,
                "t2_filled_shares": 0,
                "t2_shares": 0,
            }),
            # 5. SELECT from_state FROM position_events
            FakeResult({"from_state": "open"}),
            # 6. UPDATE positions SET state = 'open'
            FakeResult(),
            # 7. INSERT position_events
            FakeResult(),
            # 8. INSERT system_events
            FakeResult(),
        ]

        with self.assertRaisesRegex(ExecutionBlockedError, "Emergency halt"):
            await submit_live_exit_intent(
                db,
                redis,
                order_intent_id=order_intent_id,
                rate_limiter=NoWaitLimiter(),
            )

        executed = [str(c.args[0]) for c in db.execute.await_args_list]
        self.assertGreaterEqual(len(executed), 4, f"Executed only: {executed}")

        # Check intent was marked rejected
        intent_reject_call = next(
            c for c in db.execute.await_args_list if "UPDATE order_intents" in str(c.args[0]) and "status = 'rejected'" in str(c.args[0])
        )
        self.assertEqual(intent_reject_call.args[1]["order_intent_id"], order_intent_id)

        # Check position state was restored
        pos_update_call = next(
            c for c in db.execute.await_args_list if "SET state = :restored_state" in str(c.args[0])
        )
        self.assertEqual(pos_update_call.args[1]["restored_state"], "open")
        self.assertEqual(pos_update_call.args[1]["position_id"], position_id)


if __name__ == "__main__":
    unittest.main()
