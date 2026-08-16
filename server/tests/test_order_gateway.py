import unittest
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

from app.services.order_gateway import (
    _map_order_status,
    process_order_message,
    process_trade_message,
)


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


def intent_row(*, state="pending_entry", open_quantity=0):
    return {
        "id": uuid4(),
        "idempotency_key": "key",
        "trade_instruction_id": uuid4(),
        "position_id": uuid4(),
        "intent_type": "entry",
        "side": "buy",
        "quantity": 10,
        "product_type": "CNC",
        "order_type": "limit",
        "status": "submitted",
        "execution_mode": "live",
        "fyers_async_id": "async-1",
        "fyers_order_id": None,
        "exchange_order_id": None,
        "position_state": state,
        "open_quantity": open_quantity,
    }


class OrderGatewayTests(unittest.IsolatedAsyncioTestCase):
    def test_partial_fill_overrides_pending_status(self) -> None:
        self.assertEqual(
            _map_order_status(
                raw_status=6,
                filled_quantity=3,
                requested_quantity=10,
            ),
            "partially_filled",
        )

    async def test_order_update_correlates_async_id_and_is_replay_safe(self) -> None:
        intent = intent_row()
        message = {
            "s": "ok",
            "orders": {
                "id_fyers": "async-1",
                "id": "fyers-order-1",
                "exchOrdId": "exchange-1",
                "status": 6,
                "filledQty": 0,
                "tradedPrice": 0,
                "orderDateTime": "2026-07-26T09:15:00+05:30",
            },
        }
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(intent),
            FakeResult({"id": 1}),
            FakeResult(),
            FakeResult(),
        ]
        self.assertTrue(await process_order_message(db, message))
        update_params = db.execute.await_args_list[2].args[1]
        self.assertEqual(update_params["status"], "acknowledged")
        self.assertEqual(update_params["fyers_order_id"], "fyers-order-1")

        duplicate_db = AsyncMock()
        duplicate_db.execute.side_effect = [
            FakeResult(intent),
            FakeResult(),
        ]
        self.assertFalse(await process_order_message(duplicate_db, message))
        self.assertEqual(duplicate_db.execute.await_count, 2)

    async def test_trade_fill_opens_position_and_updates_weighted_aggregate(self) -> None:
        intent = intent_row()
        message = {
            "s": "ok",
            "trades": {
                "id_fyers": "async-1",
                "orderNumber": "fyers-order-1",
                "exchangeOrderNo": "exchange-1",
                "tradeNumber": "trade-1",
                "tradedQty": 10,
                "tradePrice": 101.25,
                "orderDateTime": "2026-07-26T09:16:00+05:30",
            },
        }
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(intent),
            FakeResult({"id": uuid4()}),
            FakeResult({"id": uuid4()}),
            FakeResult(),
            FakeResult(
                {
                    "filled_quantity": 10,
                    "average_price": Decimal("101.25"),
                }
            ),
            FakeResult(),
            FakeResult({"quantity": 10, "average_price": Decimal("101.25")}),
            FakeResult(),
            FakeResult(),
            FakeResult(),
        ]
        self.assertTrue(await process_trade_message(db, message))
        intent_update = db.execute.await_args_list[5].args[1]
        self.assertEqual(intent_update["status"], "filled")
        position_update = db.execute.await_args_list[7].args[1]
        self.assertEqual(position_update["open_quantity"], 10)
        self.assertEqual(position_update["average_price"], Decimal("101.25"))
        event_params = db.execute.await_args_list[8].args[1]
        self.assertEqual(event_params["event_type"], "entry_filled")
        self.assertEqual(event_params["from_state"], "pending_entry")

    async def test_partial_trade_fill_opens_only_the_filled_quantity(self) -> None:
        intent = intent_row()
        message = {
            "trades": {
                "id_fyers": "async-1",
                "tradeNumber": "trade-partial-1",
                "tradedQty": 3,
                "tradePrice": 100.5,
            }
        }
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(intent),
            FakeResult({"id": uuid4()}),
            FakeResult({"id": uuid4()}),
            FakeResult(),
            FakeResult(
                {
                    "filled_quantity": 3,
                    "average_price": Decimal("100.50"),
                }
            ),
            FakeResult(),
            FakeResult({"quantity": 3, "average_price": Decimal("100.50")}),
            FakeResult(),
            FakeResult(),
            FakeResult(),
        ]
        self.assertTrue(await process_trade_message(db, message))
        self.assertEqual(
            db.execute.await_args_list[5].args[1]["status"],
            "partially_filled",
        )
        self.assertEqual(
            db.execute.await_args_list[7].args[1]["open_quantity"],
            3,
        )
        self.assertEqual(
            db.execute.await_args_list[8].args[1]["event_type"],
            "entry_partially_filled",
        )

    async def test_duplicate_trade_does_not_apply_fill_twice(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(intent_row()),
            FakeResult(),
        ]
        message = {
            "trades": {
                "id_fyers": "async-1",
                "tradeNumber": "trade-1",
                "tradedQty": 5,
                "tradePrice": 100,
            }
        }
        self.assertFalse(await process_trade_message(db, message))
        self.assertEqual(db.execute.await_count, 2)


if __name__ == "__main__":
    unittest.main()
