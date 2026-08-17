import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx

from app.config import settings
from app.services.auth_service import AuthUnavailableError
from app.services.execution_engine import _order_tag
from app.services.fyers_broker_reads import (
    BrokerBooks,
    FyersBrokerReadClient,
    normalize_order,
    normalize_trade,
)
from app.services.reconciliation import (
    _aggregate_broker_quantities,
    _match_broker_order,
    _build_order_indexes,
    _reconcile_books,
    run_reconciliation,
)
from app.worker import WorkerSettings


class FakeResult:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []
        self.row = row

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def one_or_none(self):
        return self.row

    def scalar_one(self):
        if self.row is None:
            raise AssertionError("Expected scalar row")
        return self.row


def intent_row(
    *,
    intent_id: UUID | None = None,
    status: str = "submission_unknown",
    fyers_async_id: str | None = "async-1",
    fyers_order_id: str | None = None,
    exchange_order_id: str | None = None,
):
    intent_id = intent_id or uuid4()
    return {
        "id": intent_id,
        "idempotency_key": "key",
        "trade_instruction_id": uuid4(),
        "position_id": uuid4(),
        "intent_type": "entry",
        "side": "buy",
        "quantity": 10,
        "product_type": "CNC",
        "order_type": "limit",
        "status": status,
        "execution_mode": "live",
        "fyers_async_id": fyers_async_id,
        "fyers_order_id": fyers_order_id,
        "exchange_order_id": exchange_order_id,
        "position_state": "pending_entry",
        "open_quantity": 0,
    }


class FyersBrokerReadClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_all_uses_read_only_endpoints(self) -> None:
        seen_paths: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            if request.url.path.endswith("/orders"):
                payload = {
                    "s": "ok",
                    "orderBook": [
                        {
                            "id": "order-1",
                            "exchOrdId": "ex-1",
                            "productType": "CNC",
                            "status": 6,
                            "filledQty": 0,
                            "qty": 10,
                        }
                    ],
                }
            elif request.url.path.endswith("/tradebook"):
                payload = {"s": "ok", "tradeBook": []}
            elif request.url.path.endswith("/positions"):
                payload = {"s": "ok", "netPositions": []}
            elif request.url.path.endswith("/holdings"):
                payload = {"s": "ok", "holdings": []}
            else:
                raise AssertionError(f"Unexpected path: {request.url.path}")
            return httpx.Response(200, json=payload)

        client = FyersBrokerReadClient(
            app_id="APP",
            base_url="https://broker.test/api/v3",
            transport=httpx.MockTransport(handler),
        )
        books = await client.fetch_all(access_token="token")
        self.assertEqual(len(books.orders), 1)
        self.assertEqual(books.orders[0]["orderNumber"], "order-1")
        self.assertEqual(
            seen_paths,
            [
                "/api/v3/orders",
                "/api/v3/tradebook",
                "/api/v3/positions",
                "/api/v3/holdings",
            ],
        )
        for path in seen_paths:
            self.assertNotIn("/orders/async", path)

    async def test_fetch_all_raises_on_broker_error(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"s": "error", "message": "bad token"})

        client = FyersBrokerReadClient(
            app_id="APP",
            base_url="https://broker.test/api/v3",
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(Exception):
            await client.fetch_all(access_token="token")

    def test_normalizers_add_gateway_aliases(self) -> None:
        order = normalize_order({"id": "1", "exchangeOrderNo": "ex-1"})
        self.assertEqual(order["exchOrdId"], "ex-1")
        trade = normalize_trade({"tradeNumber": "t1", "tradeQty": 5, "tradedPrice": 99})
        self.assertEqual(trade["tradedQty"], 5)
        self.assertEqual(trade["tradePrice"], 99)


class ReconciliationMatchingTests(unittest.TestCase):
    def test_match_broker_order_by_async_id(self) -> None:
        intent = intent_row()
        order = normalize_order(
            {"id_fyers": "async-1", "id": "order-1", "productType": "CNC"}
        )
        indexes = _build_order_indexes([order], {str(intent["id"]): intent})
        self.assertIs(_match_broker_order(intent, indexes), order)

    def test_match_broker_order_by_order_tag(self) -> None:
        intent = intent_row()
        order = normalize_order(
            {
                "id": "order-2",
                "orderTag": _order_tag(intent["id"]),
                "productType": "CNC",
            }
        )
        indexes = _build_order_indexes([order], {str(intent["id"]): intent})
        self.assertIs(_match_broker_order(intent, indexes), order)

    def test_aggregate_broker_quantities_sums_holdings_and_positions(self) -> None:
        positions = [{"symbol": "NSE:SBIN-EQ", "productType": "CNC", "netQty": 5}]
        holdings = [{"symbol": "NSE:SBIN-EQ", "remainingQuantity": 8}]
        self.assertEqual(
            _aggregate_broker_quantities(positions, holdings),
            {"NSE:SBIN-EQ": 13},
        )


class ReconcileBooksTests(unittest.IsolatedAsyncioTestCase):
    async def test_heals_submission_unknown_via_gateway(self) -> None:
        intent = intent_row(status="submission_unknown")
        broker_order = normalize_order(
            {
                "id_fyers": "async-1",
                "id": "order-1",
                "exchOrdId": "ex-1",
                "status": 6,
                "filledQty": 0,
                "tradedPrice": 0,
                "qty": 10,
                "productType": "CNC",
                "orderDateTime": "2026-07-26T09:15:00+05:30",
            }
        )
        books = BrokerBooks(
            orders=[broker_order],
            trades=[],
            positions=[],
            holdings=[],
        )
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                FakeResult(rows=[intent]),
                FakeResult(rows=[]),
                FakeResult(rows=[]),
                FakeResult(),
            ]
        )

        with patch(
            "app.services.reconciliation.process_order_message",
            new=AsyncMock(return_value=True),
        ) as heal_order:
            summary = await _reconcile_books(db, recon_run_id=uuid4(), books=books)

        heal_order.assert_awaited_once()
        self.assertEqual(summary["status_mismatch_healed"], 1)
        self.assertEqual(summary["healed"], 1)

    async def test_flags_unresolved_submission_unknown(self) -> None:
        intent = intent_row(status="submission_unknown", fyers_async_id="missing")
        books = BrokerBooks(orders=[], trades=[], positions=[], holdings=[])
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                FakeResult(rows=[intent]),
                FakeResult(rows=[]),
                FakeResult(rows=[]),
                FakeResult(),
            ]
        )
        summary = await _reconcile_books(db, recon_run_id=uuid4(), books=books)
        self.assertEqual(summary["submission_unknown_unresolved"], 1)
        self.assertEqual(summary["critical_open"], 1)

    async def test_heals_missing_fill_and_is_idempotent_on_second_pass(self) -> None:
        intent = intent_row(status="submitted", fyers_order_id="order-1")
        trade = normalize_trade(
            {
                "orderNumber": "order-1",
                "exchangeOrderNo": "ex-1",
                "tradeNumber": "trade-1",
                "tradedQty": 10,
                "tradePrice": 101.25,
                "productType": "CNC",
                "orderDateTime": "2026-07-26T09:16:00+05:30",
            }
        )
        books = BrokerBooks(orders=[], trades=[trade], positions=[], holdings=[])
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                FakeResult(rows=[intent]),
                FakeResult(rows=[]),
                FakeResult(rows=[]),
                FakeResult(),
            ]
        )
        heal_trade = AsyncMock(side_effect=[True, False])
        with patch(
            "app.services.reconciliation.process_trade_message",
            new=heal_trade,
        ):
            summary = await _reconcile_books(db, recon_run_id=uuid4(), books=books)
            db.execute = AsyncMock(
                side_effect=[
                    FakeResult(rows=[intent]),
                    FakeResult(rows=[{"fyers_trade_id": "trade-1"}]),
                    FakeResult(rows=[]),
                ]
            )
            second = await _reconcile_books(db, recon_run_id=uuid4(), books=books)

        self.assertEqual(summary["missing_fill_healed"], 1)
        self.assertNotIn("missing_fill_healed", second)

    async def test_flags_external_unmatched_trade_without_creating_positions(self) -> None:
        trade = normalize_trade(
            {
                "orderNumber": "external-order",
                "tradeNumber": "external-trade",
                "tradedQty": 5,
                "tradePrice": 50,
                "productType": "CNC",
            }
        )
        books = BrokerBooks(orders=[], trades=[trade], positions=[], holdings=[])
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                FakeResult(rows=[]),
                FakeResult(rows=[]),
                FakeResult(rows=[]),
                FakeResult(),
            ]
        )
        summary = await _reconcile_books(db, recon_run_id=uuid4(), books=books)
        self.assertEqual(summary["external_unmatched_trade"], 1)
        insert_calls = [
            call
            for call in db.execute.await_args_list
            if "INSERT INTO reconciliation_items" in str(call.args[0])
        ]
        self.assertEqual(len(insert_calls), 1)
        params = insert_calls[0].args[1]
        self.assertEqual(params["issue_type"], "external_unmatched_trade")

    async def test_flags_qty_mismatch_without_updating_positions(self) -> None:
        position_id = uuid4()
        books = BrokerBooks(
            orders=[],
            trades=[],
            positions=[
                {
                    "symbol": "NSE:SBIN-EQ",
                    "productType": "CNC",
                    "netQty": 7,
                }
            ],
            holdings=[],
        )
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                FakeResult(rows=[]),
                FakeResult(rows=[]),
                FakeResult(
                    rows=[
                        {
                            "id": position_id,
                            "state": "open",
                            "open_quantity": 5,
                            "quantity": 5,
                            "fyers_symbol": "NSE:SBIN-EQ",
                            "isin": None,
                        }
                    ]
                ),
                FakeResult(),
            ]
        )
        summary = await _reconcile_books(db, recon_run_id=uuid4(), books=books)
        self.assertEqual(summary["qty_mismatch"], 1)
        self.assertEqual(summary["critical_open"], 1)
        update_position_calls = [
            call
            for call in db.execute.await_args_list
            if "UPDATE positions" in str(call.args[0])
        ]
        self.assertEqual(update_position_calls, [])


class RunReconciliationJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_failure_marks_run_failed(self) -> None:
        ctx = {"redis": AsyncMock(), "job_id": "job-1"}
        db = AsyncMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                FakeResult(row=uuid4()),
                FakeResult(row=uuid4()),
                FakeResult(),
                FakeResult(),
                FakeResult(),
            ]
        )

        with patch("app.services.reconciliation.async_session") as session_ctx:
            session_ctx.return_value.__aenter__.return_value = db
            with (
                patch.object(settings, "execution_mode", "live"),
                patch(
                    "app.services.reconciliation.get_valid_access_token",
                    new=AsyncMock(
                        side_effect=AuthUnavailableError("auth down"),
                    ),
                ),
            ):
                result = await run_reconciliation(ctx, triggered_by="manual")

        self.assertEqual(result["status"], "failed")
        failed_updates = [
            call
            for call in db.execute.await_args_list
            if "status = 'failed'" in str(call.args[0])
        ]
        self.assertTrue(failed_updates)

    async def test_paper_unseeded_marks_run_failed(self) -> None:
        ctx = {"redis": AsyncMock(), "job_id": "job-paper"}
        db = AsyncMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                FakeResult(row=uuid4()),
                FakeResult(row=uuid4()),
                FakeResult(row=None),
                FakeResult(),
                FakeResult(),
                FakeResult(),
            ]
        )

        with patch("app.services.reconciliation.async_session") as session_ctx:
            session_ctx.return_value.__aenter__.return_value = db
            with patch.object(settings, "execution_mode", "paper"):
                result = await run_reconciliation(ctx, triggered_by="manual")

        self.assertEqual(result["status"], "failed")
        self.assertIn("not seeded", result["error"])


class ReconciliationScheduleTests(unittest.TestCase):
    def test_reconciliation_runs_during_market_hours_ist(self) -> None:
        jobs = [job for job in WorkerSettings.cron_jobs if job.name == "fyers_reconciliation"]
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.weekday, {0, 1, 2, 3, 4})
        self.assertEqual(job.hour, {9, 10, 11, 12, 13, 14, 15})
        self.assertEqual(job.minute, {0, 15, 30, 45})


if __name__ == "__main__":
    unittest.main()
