import unittest
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app.config import settings
from app.services.execution_engine import (
    AsyncOrderAcceptance,
    BrokerOrderRejectedError,
    BrokerSubmissionUnknownError,
    ExecutionBlockedError,
    ExecutionSafetyError,
    FyersAsyncOrderClient,
    RedisOrderRateLimiter,
    SubmissionResult,
    _build_fyers_order_payload,
    _order_tag,
    ensure_order_gateway_ready,
    submit_live_entry_intent,
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


class NoWaitLimiter:
    async def acquire(self) -> None:
        return None


class AssertingBroker:
    def __init__(self, db, result=None, error=None):
        self.db = db
        self.result = result
        self.error = error
        self.calls = 0

    async def place_order(self, *, access_token, payload):
        self.calls += 1
        if self.db.commit.await_count != 1:
            raise AssertionError("Submission claim was not committed before HTTP")
        if access_token != "token":
            raise AssertionError("Shared auth token path was not used")
        if self.error:
            raise self.error
        return self.result


def live_snapshot(*, status="created"):
    return {
        "id": uuid4(),
        "idempotency_key": "trade-instruction:test:entry:v1",
        "trade_instruction_id": uuid4(),
        "position_id": uuid4(),
        "intent_type": "entry",
        "side": "buy",
        "quantity": 2,
        "product_type": "CNC",
        "order_type": "limit",
        "limit_price": Decimal("100.00"),
        "trigger_price": None,
        "status": status,
        "execution_mode": "live",
        "symbol": "NSE:SBIN-EQ",
        "manual_confirmed_at": object(),
    }


class FyersAsyncOrderClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_acceptance_requires_id_fyers(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v3/orders/async")
            self.assertEqual(request.headers["authorization"], "APP:token")
            return httpx.Response(200, json={"s": "ok", "id_fyers": "async-1"})

        client = FyersAsyncOrderClient(
            app_id="APP",
            endpoint="https://broker.test/api/v3/orders/async",
            transport=httpx.MockTransport(handler),
        )
        result = await client.place_order(access_token="token", payload={"qty": 1})
        self.assertEqual(result.fyers_async_id, "async-1")

    async def test_definite_broker_error_is_not_unknown(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"s": "error", "message": "RMS rejected"},
            )

        client = FyersAsyncOrderClient(
            app_id="APP",
            endpoint="https://broker.test/api/v3/orders/async",
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaisesRegex(BrokerOrderRejectedError, "RMS rejected"):
            await client.place_order(access_token="token", payload={"qty": 1})

    async def test_timeout_is_unknown_and_never_retried(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("timed out", request=request)

        client = FyersAsyncOrderClient(
            app_id="APP",
            endpoint="https://broker.test/api/v3/orders/async",
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(BrokerSubmissionUnknownError):
            await client.place_order(access_token="token", payload={"qty": 1})
        self.assertEqual(calls, 1)


class LiveExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mode_patch = patch.object(settings, "execution_mode", "live")
        self.arm_patch = patch.object(
            settings,
            "live_order_placement_enabled",
            True,
        )
        self.app_patch = patch.object(settings, "fyers_app_id", "APP")
        self.mode_patch.start()
        self.arm_patch.start()
        self.app_patch.start()

    def tearDown(self) -> None:
        self.app_patch.stop()
        self.arm_patch.stop()
        self.mode_patch.stop()

    async def test_claim_is_committed_before_one_broker_call(self) -> None:
        snapshot = live_snapshot()
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(snapshot),
            FakeResult({"enabled": False, "reason": None}),
            FakeResult({"enabled": False, "reason": None}),
            FakeResult({"id": snapshot["id"]}),
            FakeResult({"id": snapshot["id"]}),
            FakeResult(),
            FakeResult(),
            FakeResult(),
        ]
        broker = AssertingBroker(
            db,
            result=AsyncOrderAcceptance(
                fyers_async_id="async-1",
                payload={"s": "ok", "id_fyers": "async-1"},
            ),
        )
        with (
            patch(
                "app.services.execution_engine.get_valid_access_token",
                new=AsyncMock(return_value="token"),
            ),
            patch(
                "app.services.execution_engine.ensure_order_gateway_ready",
                new=AsyncMock(),
            ),
        ):
            result = await submit_live_entry_intent(
                db,
                object(),
                order_intent_id=snapshot["id"],
                broker_client=broker,
                rate_limiter=NoWaitLimiter(),
            )

        self.assertEqual(
            result,
            SubmissionResult(
                broker_call_made=True,
                outcome="submitted",
                message="Fyers accepted the async request; awaiting order updates.",
            ),
        )
        self.assertEqual(broker.calls, 1)
        self.assertEqual(db.commit.await_count, 2)
        claim_sql = str(db.execute.await_args_list[3].args[0])
        self.assertIn("status = 'submission_pending'", claim_sql)
        accepted_params = db.execute.await_args_list[4].args[1]
        self.assertEqual(accepted_params["fyers_async_id"], "async-1")

    async def test_rate_limiter_queues_until_token_is_available(self) -> None:
        redis = AsyncMock()
        redis.eval.side_effect = [75, 0]
        limiter = RedisOrderRateLimiter(redis, rate=10)
        with patch(
            "app.services.execution_engine.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            await limiter.acquire()
        sleep.assert_awaited_once_with(0.075)
        self.assertEqual(redis.eval.await_count, 2)
        # Capacity one enforces a conservative maximum of one operation per
        # 100ms even when multiple processes burst at once.
        self.assertEqual(redis.eval.await_args_list[0].args[-1], 1)

    async def test_live_submission_requires_fresh_gateway_heartbeat(self) -> None:
        missing = AsyncMock()
        missing.get.return_value = None
        with self.assertRaisesRegex(ExecutionBlockedError, "heartbeat"):
            await ensure_order_gateway_ready(missing)

        healthy = AsyncMock()
        healthy.get.return_value = json.dumps(
            {
                "status": "ready",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        await ensure_order_gateway_ready(healthy)

    async def test_concurrent_claim_does_not_call_broker_twice(self) -> None:
        snapshot = live_snapshot()
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(snapshot),
            FakeResult({"enabled": False, "reason": None}),
            FakeResult({"enabled": False, "reason": None}),
            FakeResult(),
        ]
        broker = AssertingBroker(db)
        with (
            patch(
                "app.services.execution_engine.get_valid_access_token",
                new=AsyncMock(return_value="token"),
            ),
            patch(
                "app.services.execution_engine.ensure_order_gateway_ready",
                new=AsyncMock(),
            ),
        ):
            result = await submit_live_entry_intent(
                db,
                object(),
                order_intent_id=snapshot["id"],
                broker_client=broker,
                rate_limiter=NoWaitLimiter(),
            )
        self.assertEqual(result.outcome, "already_in_progress")
        self.assertEqual(broker.calls, 0)
        db.rollback.assert_awaited_once()

    async def test_unknown_outcome_is_persisted_and_not_retried(self) -> None:
        snapshot = live_snapshot()
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(snapshot),
            FakeResult({"enabled": False, "reason": None}),
            FakeResult({"enabled": False, "reason": None}),
            FakeResult({"id": snapshot["id"]}),
            FakeResult(),
            FakeResult(),
            FakeResult(),
        ]
        broker = AssertingBroker(
            db,
            error=BrokerSubmissionUnknownError("timeout outcome unknown"),
        )
        with (
            patch(
                "app.services.execution_engine.get_valid_access_token",
                new=AsyncMock(return_value="token"),
            ),
            patch(
                "app.services.execution_engine.ensure_order_gateway_ready",
                new=AsyncMock(),
            ),
        ):
            result = await submit_live_entry_intent(
                db,
                object(),
                order_intent_id=snapshot["id"],
                broker_client=broker,
                rate_limiter=NoWaitLimiter(),
            )
        self.assertEqual(result.outcome, "submission_unknown")
        self.assertEqual(broker.calls, 1)
        unknown_sql = str(db.execute.await_args_list[4].args[0])
        self.assertIn("status = 'submission_unknown'", unknown_sql)

        replay_db = AsyncMock()
        replay_db.execute.return_value = FakeResult(
            live_snapshot(status="submission_unknown")
        )
        with self.assertRaisesRegex(ExecutionSafetyError, "Automatic retry"):
            await submit_live_entry_intent(
                replay_db,
                object(),
                order_intent_id=snapshot["id"],
                broker_client=broker,
                rate_limiter=NoWaitLimiter(),
            )
        self.assertEqual(broker.calls, 1)

    def test_payload_is_plain_cnc_without_exchange_held_bracket_fields(self) -> None:
        payload = _build_fyers_order_payload(live_snapshot())
        self.assertEqual(payload["productType"], "CNC")
        self.assertEqual(payload["type"], 1)
        self.assertEqual(payload["side"], 1)
        self.assertEqual(payload["limitPrice"], 100.0)
        self.assertNotIn("stopLoss", payload)
        self.assertNotIn("takeProfit", payload)
        self.assertLessEqual(len(payload["orderTag"]), 30)
        self.assertTrue(payload["orderTag"].startswith("stv-"))


if __name__ == "__main__":
    unittest.main()
