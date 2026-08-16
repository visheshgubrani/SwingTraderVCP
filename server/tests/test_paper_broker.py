import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.services.paper_broker import (
    PaperBrokerError,
    PaperPlaceResult,
    build_paper_fill_messages,
    fetch_preflight,
    place_paper_order,
    release_unaccepted_paper_claims,
    seed_paper_account,
)


class FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

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


class PaperBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_seed_skips_existing_account(self) -> None:
        db = AsyncMock()
        existing = {
            "starting_cash": Decimal("100000"),
            "cash_available": Decimal("90000"),
            "seeded_from_policy_version": 1,
        }
        db.execute.return_value = FakeResult(existing)
        result = await seed_paper_account(db, starting_cash=Decimal("100000"))
        self.assertEqual(result["cash_available"], Decimal("90000"))
        self.assertEqual(db.execute.await_count, 1)

    async def test_place_rejects_insufficient_cash(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(
                {
                    "starting_cash": Decimal("100000"),
                    "cash_available": Decimal("10"),
                    "seeded_from_policy_version": 1,
                }
            ),
            FakeResult(None),
        ]
        snapshot = {
            "id": uuid4(),
            "quantity": 10,
            "side": "buy",
            "symbol": "NSE:SBIN-EQ",
        }
        with self.assertRaises(PaperBrokerError) as ctx:
            await place_paper_order(
                db, snapshot=snapshot, fill_price=Decimal("100")
            )
        self.assertIn("insufficient cash", str(ctx.exception))

    async def test_replay_messages_are_gateway_shaped(self) -> None:
        intent_id = uuid4()
        result = build_paper_fill_messages(
            snapshot={
                "id": intent_id,
                "quantity": 4,
                "symbol": "NSE:SBIN-EQ",
                "side": "buy",
            },
            fyers_async_id=f"paper-async:{intent_id}",
            fyers_order_id=f"paper-ord:{intent_id}",
            trade_number=f"paper-trd:{intent_id}",
            fill_price=Decimal("101.50"),
        )
        self.assertIsInstance(result, PaperPlaceResult)
        self.assertEqual(result.order_message["orders"]["status"], 2)
        self.assertEqual(result.trade_message["trades"]["tradedQty"], 4)
        self.assertIn("orderTag", result.order_message["orders"])

    async def test_preflight_fails_when_unseeded(self) -> None:
        db = AsyncMock()
        db.execute.return_value = FakeResult(None)
        with self.assertRaises(PaperBrokerError):
            await fetch_preflight(db)

    async def test_release_unaccepted_claims_returns_count(self) -> None:
        db = AsyncMock()
        db.execute.return_value = FakeResult(rows=[{"id": uuid4()}])
        released = await release_unaccepted_paper_claims(db)
        self.assertEqual(released, 1)


class PaperPreflightIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_paper_preflight_never_constructs_fyers_client(self) -> None:
        from app.workers.entry_supervisor import _fetch_broker_preflight
        from app.config import settings

        redis = AsyncMock()
        snapshot = object()
        session_cm = AsyncMock()
        session_cm.__aenter__.return_value = AsyncMock()
        session_cm.__aexit__.return_value = False
        with (
            patch.object(settings, "execution_mode", "paper"),
            patch(
                "app.workers.entry_supervisor.async_session",
                return_value=session_cm,
            ),
            patch(
                "app.workers.entry_supervisor.fetch_paper_preflight",
                new=AsyncMock(return_value=snapshot),
            ),
            patch("app.workers.entry_supervisor.FyersBrokerReadClient") as fyers,
        ):
            result = await _fetch_broker_preflight(redis)
        self.assertIs(result, snapshot)
        fyers.assert_not_called()


class BrokerStateVerifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_quantity_mismatch_fails_closed(self) -> None:
        from datetime import datetime, timezone

        from app.services.fyers_broker_reads import BrokerBooks, BrokerPreflightSnapshot
        from app.workers.entry_supervisor import (
            ExecutionBlockedError,
            verify_broker_state_under_lock,
        )

        db = AsyncMock()
        db.execute.return_value = FakeResult(
            rows=[{"symbol": "NSE:SBIN-EQ", "quantity": 4}]
        )
        snapshot = BrokerPreflightSnapshot(
            books=BrokerBooks(
                orders=[],
                trades=[],
                positions=[
                    {
                        "symbol": "NSE:SBIN-EQ",
                        "netQty": 10,
                        "productType": "CNC",
                    }
                ],
                holdings=[],
            ),
            available_funds=Decimal("100000"),
            fetched_at=datetime.now(timezone.utc),
        )
        with self.assertRaises(ExecutionBlockedError):
            await verify_broker_state_under_lock(db, snapshot)


if __name__ == "__main__":
    unittest.main()
