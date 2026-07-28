import unittest
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

from app.domain.trading import (
    TradeValidationError,
    is_tick_aligned,
    validate_trade_plan,
)
from app.schemas.trading import TrailingRule
from app.services.execution_engine import (
    ExecutionBlockedError,
    create_paper_entry_intent,
    ensure_orders_allowed,
)
from pydantic import ValidationError


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def one(self):
        if self.row is None:
            raise AssertionError("Expected one row")
        return self.row


class TradeDomainTests(unittest.TestCase):
    def test_tick_alignment_uses_exact_decimal_math(self) -> None:
        self.assertTrue(is_tick_aligned(Decimal("845.50"), Decimal("0.05")))
        self.assertFalse(is_tick_aligned(Decimal("845.53"), Decimal("0.05")))

    def test_buy_plan_requires_valid_lot_tick_and_price_direction(self) -> None:
        validate_trade_plan(
            side="buy",
            quantity=10,
            lot_size=1,
            tick_size=Decimal("0.05"),
            planned_entry_price=Decimal("100.00"),
            entry_order_type="limit",
            entry_limit_price=Decimal("100.00"),
            initial_stop_loss=Decimal("95.00"),
            initial_target=Decimal("110.00"),
        )

        invalid_plans = [
            {"quantity": 3, "lot_size": 2},
            {"initial_stop_loss": Decimal("100.00")},
            {"initial_target": Decimal("99.00")},
            {"planned_entry_price": Decimal("100.03")},
        ]
        base = {
            "side": "buy",
            "quantity": 10,
            "lot_size": 1,
            "tick_size": Decimal("0.05"),
            "planned_entry_price": Decimal("100.00"),
            "entry_order_type": "limit",
            "entry_limit_price": Decimal("100.00"),
            "initial_stop_loss": Decimal("95.00"),
            "initial_target": Decimal("110.00"),
        }
        for change in invalid_plans:
            with self.subTest(change=change):
                with self.assertRaises(TradeValidationError):
                    validate_trade_plan(**(base | change))

    def test_sell_plan_inverts_stop_and_target_direction(self) -> None:
        validate_trade_plan(
            side="sell",
            quantity=1,
            lot_size=1,
            tick_size=Decimal("0.05"),
            planned_entry_price=Decimal("100.00"),
            entry_order_type="market",
            entry_limit_price=None,
            initial_stop_loss=Decimal("105.00"),
            initial_target=Decimal("90.00"),
        )

    def test_trailing_rule_rejects_missing_or_irrelevant_value(self) -> None:
        with self.assertRaises(ValidationError):
            TrailingRule(type="step_pct")
        with self.assertRaises(ValidationError):
            TrailingRule(type="none", value=Decimal("2"))


class PaperExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_kill_switch_blocks_before_intent_insert(self) -> None:
        db = AsyncMock()
        db.execute.return_value = FakeResult(
            {"enabled": True, "reason": "Operator pause"}
        )

        with self.assertRaisesRegex(ExecutionBlockedError, "Operator pause"):
            await ensure_orders_allowed(db)

        self.assertEqual(db.execute.await_count, 1)

    async def test_missing_kill_switch_fails_closed(self) -> None:
        db = AsyncMock()
        db.execute.return_value = FakeResult()

        with self.assertRaisesRegex(ExecutionBlockedError, "fails closed"):
            await ensure_orders_allowed(db)

    async def test_paper_intent_is_logged_once_with_deterministic_key(self) -> None:
        instruction_id = uuid4()
        position_id = uuid4()
        intent_id = uuid4()
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult({"enabled": False, "reason": None}),
            FakeResult(
                {
                    "id": intent_id,
                    "idempotency_key": (
                        f"trade-instruction:{instruction_id}:entry:v1"
                    ),
                    "execution_mode": "paper",
                }
            ),
            FakeResult(),
        ]

        result = await create_paper_entry_intent(
            db,
            trade_instruction_id=instruction_id,
            position_id=position_id,
            side="buy",
            quantity=10,
            product_type="CNC",
            order_type="limit",
            limit_price=Decimal("100.00"),
        )

        self.assertEqual(result.id, intent_id)
        self.assertEqual(
            result.idempotency_key,
            f"trade-instruction:{instruction_id}:entry:v1",
        )
        insert_sql = str(db.execute.await_args_list[1].args[0])
        self.assertIn("ON CONFLICT (idempotency_key) DO NOTHING", insert_sql)
        self.assertEqual(
            db.execute.await_args_list[1].args[1]["execution_mode"],
            "paper",
        )

    async def test_existing_idempotency_key_returns_same_intent(self) -> None:
        instruction_id = uuid4()
        position_id = uuid4()
        intent_id = uuid4()
        key = f"trade-instruction:{instruction_id}:entry:v1"
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult({"enabled": False, "reason": None}),
            FakeResult(),
            FakeResult(
                {
                    "id": intent_id,
                    "idempotency_key": key,
                    "trade_instruction_id": instruction_id,
                    "position_id": position_id,
                    "execution_mode": "paper",
                }
            ),
        ]

        result = await create_paper_entry_intent(
            db,
            trade_instruction_id=instruction_id,
            position_id=position_id,
            side="buy",
            quantity=10,
            product_type="CNC",
            order_type="limit",
            limit_price=Decimal("100.00"),
        )

        self.assertEqual(result.id, intent_id)
        self.assertEqual(result.idempotency_key, key)
        self.assertEqual(db.execute.await_count, 3)


if __name__ == "__main__":
    unittest.main()
