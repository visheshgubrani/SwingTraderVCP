import datetime
import json
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.services.auth_service import AuthUnavailableError
from app.services.execution_engine import ExecutionBlockedError, ensure_order_gateway_ready
from app.services.fyers_broker_reads import BrokerBooks, BrokerPreflightSnapshot
from app.services.reconciliation import (
    _aggregate_broker_quantities,
    _redact_broker_snapshot,
    _reconcile_books,
)
from app.workers.entry_supervisor import load_portfolio_state_under_lock


class FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar


class TestBatchCLiveHardening(unittest.IsolatedAsyncioTestCase):

    # --- OG-001: Gateway ready vs connecting/degraded/stopped ---
    async def test_ensure_order_gateway_ready_enforces_ready_state(self) -> None:
        redis = AsyncMock()

        # 1. Missing heartbeat
        redis.get.return_value = None
        with self.assertRaises(ExecutionBlockedError) as ctx:
            await ensure_order_gateway_ready(redis)
        self.assertIn("heartbeat is unavailable", str(ctx.exception))

        # 2. Connecting status fails
        redis.get.return_value = json.dumps({
            "status": "connecting",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        with self.assertRaises(ExecutionBlockedError) as ctx:
            await ensure_order_gateway_ready(redis)
        self.assertIn("not ready (status=connecting", str(ctx.exception))

        # 3. Degraded status fails
        redis.get.return_value = json.dumps({
            "status": "degraded",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        with self.assertRaises(ExecutionBlockedError) as ctx:
            await ensure_order_gateway_ready(redis)
        self.assertIn("not ready (status=degraded", str(ctx.exception))

        # 4. Running status fails (OG-001: must be ready only)
        redis.get.return_value = json.dumps({
            "status": "running",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        with self.assertRaises(ExecutionBlockedError) as ctx:
            await ensure_order_gateway_ready(redis)
        self.assertIn("not ready (status=running", str(ctx.exception))

        # 5. Stale heartbeat (> 30s) fails
        stale_time = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=45)
        ).isoformat()
        redis.get.return_value = json.dumps({
            "status": "ready",
            "timestamp": stale_time,
        })
        with self.assertRaises(ExecutionBlockedError) as ctx:
            await ensure_order_gateway_ready(redis)
        self.assertIn("not ready", str(ctx.exception))

        # 6. Fresh ready heartbeat passes
        fresh_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        redis.get.return_value = json.dumps({
            "status": "ready",
            "timestamp": fresh_time,
        })
        await ensure_order_gateway_ready(redis)

    # --- REC-002: Reconciliation SUM by symbol for multiple local positions ---
    async def test_reconciliation_sums_multiple_local_positions_per_symbol(self) -> None:
        db = AsyncMock()
        # Mock 2 separate positions for same symbol (e.g. qty 10 and qty 15 = 25 total)
        local_positions = [
            {"id": uuid4(), "fyers_symbol": "NSE:RELIANCE-EQ", "open_quantity": 10, "state": "open", "quantity": 10},
            {"id": uuid4(), "fyers_symbol": "NSE:RELIANCE-EQ", "open_quantity": 15, "state": "open", "quantity": 15},
        ]
        # Broker has 25 shares (matching the sum of 10 + 15)
        books = BrokerBooks(
            orders=[],
            trades=[],
            positions=[{"symbol": "NSE:RELIANCE-EQ", "productType": "CNC", "netQty": 25}],
            holdings=[],
        )

        with patch("app.services.reconciliation._load_live_intents", return_value=[]), \
             patch("app.services.reconciliation._load_known_trade_ids", return_value=set()), \
             patch("app.services.reconciliation._load_open_positions", return_value=local_positions), \
             patch("app.services.reconciliation._insert_item", new_callable=AsyncMock) as mock_insert:

            summary = await _reconcile_books(db, recon_run_id=uuid4(), books=books)
            # No qty mismatch should be raised because 10 + 15 == 25
            self.assertEqual(summary.get("qty_mismatch", 0), 0)
            self.assertEqual(summary.get("critical_open", 0), 0)
            mock_insert.assert_not_called()

    # --- REC-003: Reconciliation CNC inventory calculation (holdings + netQty) ---
    def test_aggregate_broker_quantities_adds_holdings_and_positions(self) -> None:
        positions = [
            {"symbol": "NSE:INFY-EQ", "productType": "CNC", "netQty": 10},
            {"symbol": "NSE:TCS-EQ", "productType": "CNC", "netQty": -5},
            {"symbol": "NSE:INTRADAY-EQ", "productType": "INTRADAY", "netQty": 50},  # ignored non-CNC
        ]
        holdings = [
            {"symbol": "NSE:INFY-EQ", "remainingQuantity": 20},
            {"symbol": "NSE:TCS-EQ", "remainingQuantity": 15},
            {"symbol": "NSE:WIPRO-EQ", "remainingQuantity": 30},
        ]

        result = _aggregate_broker_quantities(positions, holdings)
        # INFY: 20 held + 10 bought today = 30
        self.assertEqual(result["NSE:INFY-EQ"], 30)
        # TCS: 15 held - 5 sold today = 10
        self.assertEqual(result["NSE:TCS-EQ"], 10)
        # WIPRO: 30 held + 0 today = 30
        self.assertEqual(result["NSE:WIPRO-EQ"], 30)
        # INTRADAY: filtered out
        self.assertNotIn("NSE:INTRADAY-EQ", result)

    # --- REC-004: Aging submission_pending intents ---
    async def test_reconciliation_flags_aged_submission_pending_intents(self) -> None:
        db = AsyncMock()
        now = datetime.datetime.now(datetime.timezone.utc)
        # Aged intent submitted 90s ago -> should flag
        aged_intent = {
            "id": uuid4(),
            "status": "submission_pending",
            "quantity": 10,
            "side": "buy",
            "intent_type": "entry",
            "limit_price": 100.0,
            "fyers_symbol": "NSE:TEST-EQ",
            "created_at": now - datetime.timedelta(seconds=120),
            "broker_requested_at": now - datetime.timedelta(seconds=90),
            "execution_mode": "live",
        }
        # Intent created earlier but submitted only 15s ago -> should NOT flag
        newly_submitted_intent = {
            "id": uuid4(),
            "status": "submission_pending",
            "quantity": 10,
            "side": "buy",
            "intent_type": "entry",
            "limit_price": 100.0,
            "fyers_symbol": "NSE:TEST-EQ",
            "created_at": now - datetime.timedelta(seconds=300),
            "broker_requested_at": now - datetime.timedelta(seconds=15),
            "execution_mode": "live",
        }

        books = BrokerBooks(orders=[], trades=[], positions=[], holdings=[])

        with patch("app.services.reconciliation._load_live_intents", return_value=[aged_intent, newly_submitted_intent]), \
             patch("app.services.reconciliation._load_known_trade_ids", return_value=set()), \
             patch("app.services.reconciliation._load_open_positions", return_value=[]), \
             patch("app.services.reconciliation._insert_item", new_callable=AsyncMock) as mock_insert:

            summary = await _reconcile_books(db, recon_run_id=uuid4(), books=books)
            # Only the aged intent (broker_requested_at 90s > 60s) is flagged as critical unresolved item
            self.assertEqual(summary.get("submission_pending_unresolved", 0), 1)
            self.assertEqual(summary.get("critical_open", 0), 1)
            self.assertEqual(mock_insert.call_count, 1)
            self.assertEqual(mock_insert.call_args[1]["issue_type"], "submission_pending_unresolved")
            self.assertEqual(mock_insert.call_args[1]["local_record_id"], str(aged_intent["id"]))

    # --- REC-006: Redact broker snapshot ---
    def test_redact_broker_snapshot_filters_sensitive_or_noisy_fields(self) -> None:
        raw_broker_order = {
            "id": "123456",
            "symbol": "NSE:RELIANCE-EQ",
            "qty": 50,
            "status": 2,
            "type": 1,
            "side": 1,
            "client_internal_auth_signature": "SECRET_SIG_DO_NOT_LEAK",
            "internal_routing_metadata": {"node": "vps-1", "user_agent": "secret-bot"},
        }
        redacted = _redact_broker_snapshot(raw_broker_order)
        self.assertEqual(redacted["id"], "123456")
        self.assertEqual(redacted["symbol"], "NSE:RELIANCE-EQ")
        self.assertEqual(redacted["qty"], 50)
        self.assertNotIn("client_internal_auth_signature", redacted)
        self.assertNotIn("internal_routing_metadata", redacted)

    # --- SEC-005: Dedicated Token Encryption Key settings ---
    def test_token_encryption_key_precedence(self) -> None:
        s1 = Settings(fyers_secret_key="secret-123", token_encryption_key="", app_environment="development")
        self.assertEqual(s1.token_encryption_passphrase, "secret-123")

        s2 = Settings(fyers_secret_key="secret-123", token_encryption_key="custom-enc-key-999", app_environment="development")
        self.assertEqual(s2.token_encryption_passphrase, "custom-enc-key-999")

        # In production, missing token_encryption_key must fail at Settings construction
        with self.assertRaises(ValueError) as ctx:
            Settings(
                fyers_secret_key="secret-123",
                token_encryption_key="",
                app_environment="production",
                app_password="StrongProdPassword2026!",
            )
        self.assertIn("TOKEN_ENCRYPTION_KEY is required in production", str(ctx.exception))

        s4 = Settings(
            fyers_secret_key="secret-123",
            token_encryption_key="prod-enc-key-999",
            app_environment="production",
            app_password="StrongProdPassword2026!",
        )
        self.assertEqual(s4.token_encryption_passphrase, "prod-enc-key-999")

    # --- P10-006: Reduced-live 0.25x capital enforcement in entry_supervisor ---
    async def test_build_portfolio_state_scales_capital_in_reduced_live_stage(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(rows=[]),  # open positions
            FakeResult(scalar="Auto"),  # sector metadata
            FakeResult([{"stage": "reduced_live"}]),  # p10_rollout_state
            FakeResult(rows=[]),  # today loss fills
        ]

        policy = MagicMock()
        policy.deployable_capital_override = Decimal("1000000.00")
        policy.risk_per_trade_pct = Decimal("0.01")
        policy.max_total_open_risk_pct = Decimal("0.06")
        policy.max_open_positions = 5
        policy.daily_loss_limit_pct = Decimal("0.02")
        policy.max_single_name_notional_pct = Decimal("0.20")
        policy.max_sector_notional_pct = Decimal("0.30")
        policy.max_cluster_notional_pct = Decimal("0.30")

        # Broker funds are 100,000 (smaller than 1,000,000 override)
        # Sizing must be min(1,000,000, 100,000) * 0.25 = 25,000 (never 100,000!)
        snapshot = BrokerPreflightSnapshot(
            available_funds=Decimal("100000.00"),
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
            books=BrokerBooks(orders=[], trades=[], positions=[], holdings=[]),
        )

        portfolio_state = await load_portfolio_state_under_lock(
            db,
            policy=policy,
            candidate_symbol="NSE:TEST-EQ",
            broker_snapshot=snapshot,
        )

        # min(1,000,000, 100,000) * 0.25 = 25,000
        self.assertEqual(portfolio_state.deployable_capital, Decimal("25000.0000"))

    # --- INF-004: HTTP Security headers middleware ---
    def test_security_headers_present_on_responses(self) -> None:
        from main import app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=FakeResult(scalar=1))

        async def _override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = _override_get_db
        try:
            fake_redis = AsyncMock()
            fake_redis.ping = AsyncMock(return_value=True)
            app.state.redis = fake_redis
            client = TestClient(app)
            response = client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
            self.assertEqual(response.headers.get("x-frame-options"), "DENY")
            self.assertEqual(response.headers.get("referrer-policy"), "strict-origin-when-cross-origin")
        finally:
            app.dependency_overrides.pop(get_db, None)


if __name__ == "__main__":
    unittest.main()
