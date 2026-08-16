"""Paper P10 path uses claim → paper broker → gateway fill processors."""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.config import settings
from app.services.execution_engine import (
    SubmissionResult,
    _complete_paper_submission,
    ensure_execution_mode_armed,
)
from app.services.paper_broker import PaperPlaceResult


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class PaperUnifiedSubmitTests(unittest.IsolatedAsyncioTestCase):
    async def test_paper_submit_applies_gateway_messages(self) -> None:
        intent_id = uuid4()
        snapshot = {
            "id": intent_id,
            "symbol": "NSE:SBIN-EQ",
            "quantity": 2,
            "side": "buy",
            "position_id": uuid4(),
            "trade_instruction_id": None,
        }
        paper_result = PaperPlaceResult(
            fyers_async_id=f"paper-async:{intent_id}",
            fyers_order_id=f"paper-ord:{intent_id}",
            trade_number=f"paper-trd:{intent_id}",
            payload={"s": "ok"},
            order_message={"orders": {"id_fyers": "a", "status": 2}},
            trade_message={"trades": {"tradedQty": 2}},
        )
        db = AsyncMock()
        db.execute.return_value = FakeResult({"id": intent_id})
        redis = AsyncMock()
        with (
            patch(
                "app.services.paper_broker.place_paper_order",
                new=AsyncMock(return_value=paper_result),
            ),
            patch(
                "app.services.order_gateway.process_order_message",
                new=AsyncMock(return_value=True),
            ) as process_order,
            patch(
                "app.services.order_gateway.process_trade_message",
                new=AsyncMock(return_value=True),
            ) as process_trade,
            patch(
                "app.services.paper_broker.publish_paper_fill_events",
                new=AsyncMock(),
            ),
        ):
            result = await _complete_paper_submission(
                db,
                redis,
                snapshot=snapshot,
                fill_price=Decimal("100"),
            )
        self.assertIsInstance(result, SubmissionResult)
        self.assertEqual(result.outcome, "submitted")
        process_order.assert_awaited_once()
        process_trade.assert_awaited_once()
        db.commit.assert_awaited()

    async def test_live_submit_still_refuses_unarmed_mode(self) -> None:
        with patch.object(settings, "execution_mode", "live"):
            with patch.object(settings, "live_order_placement_enabled", False):
                with self.assertRaises(Exception):
                    ensure_execution_mode_armed()


if __name__ == "__main__":
    unittest.main()
