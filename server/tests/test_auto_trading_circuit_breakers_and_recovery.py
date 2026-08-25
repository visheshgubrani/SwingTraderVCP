"""Tests for Auto-Trading Safety Controls, Risk Policies, Circuit Breakers, and Crash Recovery.

Covers:
1. Global Kill Switch (blocks entries/adds, blocks exits, safe disengagement)
2. 2% Daily Realized Loss Limit (blocks new allocations while preserving exits)
3. 3-Consecutive-Stop Streak Circuit Breaker (increments, resets, trips new_entries_paused)
4. Portfolio Allocation Caps & Concurrency (Anti-double-spend, correlation cluster caps, viability thresholds)
5. Durable State Crash Recovery & Exit Re-Arming
"""

import datetime as dt
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.domain.p10_caps import (
    CapCheckResult,
    CandidatePriorityResult,
    CompetingCandidate,
    PortfolioState,
    RiskPolicyConfig,
    correlation_cluster_members,
    evaluate_portfolio_caps,
    sort_competing_candidates,
)
from app.domain.p10_sizing import calculate_leg_sizing
from app.services.execution_engine import (
    ExecutionBlockedError,
    ensure_orders_allowed,
    restore_rejected_exit_position,
)
from app.services.position_monitor import (
    MonitoredPosition,
    load_monitored_positions,
    process_position_tick,
)
from app.services.risk_stop_streak import (
    advance_stop_streak,
    classify_stop_closure,
)


class FakeDbResult:
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


class TestGlobalKillSwitch(unittest.IsolatedAsyncioTestCase):
    """Tests for global kill switch enforcement across entry supervisor and position monitor."""

    async def test_kill_switch_engaged_blocks_execution_engine(self):
        """When kill switch is engaged, ensure_orders_allowed raises ExecutionBlockedError."""
        db = AsyncMock()
        db.execute.return_value = FakeDbResult({"enabled": True, "reason": "Market anomaly detected"})

        with self.assertRaisesRegex(ExecutionBlockedError, "Market anomaly detected"):
            await ensure_orders_allowed(db)

    async def test_kill_switch_fails_closed_when_state_missing(self):
        """If system_controls row is missing, execution engine fails closed."""
        db = AsyncMock()
        db.execute.return_value = FakeDbResult(None)

        with self.assertRaisesRegex(ExecutionBlockedError, "fails closed"):
            await ensure_orders_allowed(db)

    async def test_kill_switch_engaged_blocks_position_monitor_exits(self):
        """When kill switch is engaged, position monitor does not fire exit intents."""
        pos = MonitoredPosition(
            id=uuid4(),
            symbol="NSE:TCS-EQ",
            side="long",
            state="open",
            quantity=10,
            open_quantity=10,
            product_type="CNC",
            average_entry_price=Decimal("4000.00"),
            current_stop_loss=Decimal("3800.00"),
            current_target=Decimal("4400.00"),
            trailing_rule={"type": "none"},
            tick_size=Decimal("0.05"),
        )
        db = AsyncMock()

        # Price drops below stop (3750 < 3800), but kill_switch_engaged is True
        result = await process_position_tick(
            db,
            position=pos,
            ltp=Decimal("3750.00"),
            kill_switch_engaged=True,
        )
        self.assertIsNone(result)
        db.execute.assert_not_called()

    @patch("app.services.position_monitor.create_exit_intent", new_callable=AsyncMock)
    @patch("app.services.position_monitor.settings.execution_mode", "paper")
    async def test_kill_switch_disengaged_allows_normal_exits(self, create_exit_intent: AsyncMock):
        """When kill switch is False, position monitor processes exit intents normally."""
        intent_id = uuid4()
        create_exit_intent.return_value = type(
            "Ref", (), {"id": intent_id, "idempotency_key": "k", "execution_mode": "paper"}
        )()
        pos = MonitoredPosition(
            id=uuid4(),
            symbol="NSE:TCS-EQ",
            side="long",
            state="open",
            quantity=10,
            open_quantity=10,
            product_type="CNC",
            average_entry_price=Decimal("4000.00"),
            current_stop_loss=Decimal("3800.00"),
            current_target=Decimal("4400.00"),
            trailing_rule={"type": "none"},
            tick_size=Decimal("0.05"),
        )
        db = AsyncMock()

        result = await process_position_tick(
            db,
            position=pos,
            ltp=Decimal("3750.00"),
            kill_switch_engaged=False,
        )
        self.assertEqual(result, intent_id)
        create_exit_intent.assert_awaited_once()


class TestDailyRealizedLossLimit(unittest.TestCase):
    """Tests for 2% daily loss limit blocking new allocations while preserving exits."""

    def setUp(self):
        self.policy = RiskPolicyConfig(
            version=1,
            name="Balanced",
            risk_per_trade_pct=Decimal("0.01"),  # 1%
            max_total_open_risk_pct=Decimal("0.04"),  # 4%
            max_single_name_notional_pct=Decimal("0.15"),  # 15%
            max_sector_notional_pct=Decimal("0.30"),  # 30%
            max_cluster_notional_pct=Decimal("0.30"),  # 30%
            daily_loss_limit_pct=Decimal("0.02"),  # 2%
            max_open_positions=8,
        )

    def test_daily_loss_below_2_pct_allows_allocation(self):
        """Realized loss of 1,000 on 100k capital (1% < 2%) allows new allocations."""
        state = PortfolioState(
            deployable_capital=Decimal("100000.00"),
            current_open_risk=Decimal("1000.00"),
            current_open_positions_count=1,
            daily_realized_losses=Decimal("1000.00"),  # 1%
            existing_name_notional=Decimal("0.00"),
            existing_sector_notional=Decimal("0.00"),
            existing_cluster_notional=Decimal("0.00"),
        )
        res = evaluate_portfolio_caps(
            policy=self.policy,
            state=state,
            symbol="NSE:INFY-EQ",
            is_new_position=True,
        )
        self.assertFalse(res.is_blocked)
        self.assertGreater(res.allowed_risk_budget, Decimal("0"))

    def test_daily_loss_at_or_above_2_pct_blocks_new_allocations(self):
        """Realized loss of 2,000 on 100k capital (2%) blocks new allocations."""
        state = PortfolioState(
            deployable_capital=Decimal("100000.00"),
            current_open_risk=Decimal("1000.00"),
            current_open_positions_count=1,
            daily_realized_losses=Decimal("2000.00"),  # 2% limit reached!
            existing_name_notional=Decimal("0.00"),
            existing_sector_notional=Decimal("0.00"),
            existing_cluster_notional=Decimal("0.00"),
        )
        res = evaluate_portfolio_caps(
            policy=self.policy,
            state=state,
            symbol="NSE:INFY-EQ",
            is_new_position=True,
        )
        self.assertTrue(res.is_blocked)
        self.assertEqual(res.allowed_risk_budget, Decimal("0"))
        self.assertIn("Daily realized loss", res.blocking_reason)


class TestThreeStopStreakCircuitBreaker(unittest.TestCase):
    """Tests for consecutive stop loss circuit breaker."""

    def test_pure_stop_loss_closure_increments_streak(self):
        """Stop-loss exit with negative net P&L produces 'increment'."""
        classification = classify_stop_closure(exit_purposes={"stop_loss"}, net_pnl=Decimal("-1500.00"))
        self.assertEqual(classification, "increment")

    def test_target_or_profitable_closure_resets_streak(self):
        """Target exit or profitable closure produces 'reset'."""
        class_t1 = classify_stop_closure(exit_purposes={"target_1"}, net_pnl=Decimal("1200.00"))
        self.assertEqual(class_t1, "reset")

        class_trail = classify_stop_closure(exit_purposes={"runner_trail"}, net_pnl=Decimal("3000.00"))
        self.assertEqual(class_trail, "reset")

    def test_manual_or_invalid_fill_closure_is_ignored(self):
        """Manual trades, external broker trades, or invalid fill exits are ignored from streak."""
        class_inv = classify_stop_closure(exit_purposes={"invalid_fill"}, net_pnl=Decimal("-200.00"))
        self.assertEqual(class_inv, "ignored")

    def test_advance_streak_trips_on_third_consecutive_stop(self):
        """Sequence of 3 stops: 1st (cnt=1), 2nd (cnt=2), 3rd (cnt=3, trips breaker)."""
        limit = 3

        # 1st stop
        cnt1, tripped1, newly1 = advance_stop_streak(count=0, tripped=False, classification="increment", limit=limit)
        self.assertEqual((cnt1, tripped1, newly1), (1, False, False))

        # 2nd stop
        cnt2, tripped2, newly2 = advance_stop_streak(count=cnt1, tripped=tripped1, classification="increment", limit=limit)
        self.assertEqual((cnt2, tripped2, newly2), (2, False, False))

        # 3rd stop -> TRIPS!
        cnt3, tripped3, newly3 = advance_stop_streak(count=cnt2, tripped=tripped2, classification="increment", limit=limit)
        self.assertEqual((cnt3, tripped3, newly3), (3, True, True))

        # 4th event while already tripped -> stays tripped
        cnt4, tripped4, newly4 = advance_stop_streak(count=cnt3, tripped=tripped3, classification="increment", limit=limit)
        self.assertEqual((cnt4, tripped4, newly4), (3, True, False))


class TestAllocationCapsAndConcurrency(unittest.TestCase):
    """Tests for risk caps, correlation cluster anti-double-spend, priority bands, and viability."""

    def setUp(self):
        self.policy = RiskPolicyConfig(
            version=1,
            name="Balanced",
            risk_per_trade_pct=Decimal("0.01"),  # 1% = 1,000 on 100k
            max_total_open_risk_pct=Decimal("0.04"),  # 4% = 4,000 on 100k
            max_single_name_notional_pct=Decimal("0.15"),  # 15% = 15,000 on 100k
            max_sector_notional_pct=Decimal("0.30"),  # 30% = 30,000 on 100k
            max_cluster_notional_pct=Decimal("0.30"),  # 30% = 30,000 on 100k
            daily_loss_limit_pct=Decimal("0.02"),
            max_open_positions=8,
        )

    def test_max_open_positions_cap_blocks_ninth_position(self):
        """When 8 open positions already exist, is_new_position=True is blocked."""
        state = PortfolioState(
            deployable_capital=Decimal("100000.00"),
            current_open_risk=Decimal("2000.00"),
            current_open_positions_count=8,  # Max 8 reached!
            daily_realized_losses=Decimal("0.00"),
            existing_name_notional=Decimal("0.00"),
            existing_sector_notional=Decimal("0.00"),
            existing_cluster_notional=Decimal("0.00"),
        )
        res = evaluate_portfolio_caps(
            policy=self.policy,
            state=state,
            symbol="NSE:WIPRO-EQ",
            is_new_position=True,
        )
        self.assertTrue(res.is_blocked)
        self.assertIn("Maximum open positions (8) reached", res.blocking_reason)

    def test_total_open_risk_cap_blocks_further_allocation(self):
        """When total open risk is at 4,000 (4%), new risk headroom is 0."""
        state = PortfolioState(
            deployable_capital=Decimal("100000.00"),
            current_open_risk=Decimal("4000.00"),  # 4% reached
            current_open_positions_count=4,
            daily_realized_losses=Decimal("0.00"),
            existing_name_notional=Decimal("0.00"),
            existing_sector_notional=Decimal("0.00"),
            existing_cluster_notional=Decimal("0.00"),
        )
        res = evaluate_portfolio_caps(
            policy=self.policy,
            state=state,
            symbol="NSE:WIPRO-EQ",
            is_new_position=True,
        )
        self.assertTrue(res.is_blocked)
        self.assertIn("Total open risk (4000.00) reached", res.blocking_reason)

    def test_correlation_cluster_anti_double_spend(self):
        """Instruments sharing a correlation cluster (rho >= 0.80) are constrained by the 30% cluster cap."""
        # Existing cluster notional is 28,000 on 100k capital. Max cluster is 30,000.
        # Headroom = 30,000 - 28,000 = 2,000.
        state = PortfolioState(
            deployable_capital=Decimal("100000.00"),
            current_open_risk=Decimal("1000.00"),
            current_open_positions_count=2,
            daily_realized_losses=Decimal("0.00"),
            existing_name_notional=Decimal("0.00"),
            existing_sector_notional=Decimal("10000.00"),
            existing_cluster_notional=Decimal("28000.00"),
        )
        res = evaluate_portfolio_caps(
            policy=self.policy,
            state=state,
            symbol="NSE:HDFCBANK-EQ",
            is_new_position=True,
        )
        self.assertFalse(res.is_blocked)
        self.assertEqual(res.allowed_notional_budget, Decimal("2000.00"))

    def test_competing_candidates_priority_and_conflict_resolution(self):
        """Candidates are ordered by 2-point score bands, then conservative R:R, then timestamp."""
        now = dt.datetime(2026, 8, 25, 9, 35, tzinfo=dt.timezone.utc)
        earlier = now - dt.timedelta(minutes=5)

        c1 = CompetingCandidate(
            candidate_id="c1",
            symbol="STOCK_A",
            scanner_score=Decimal("88.00"),
            gemini_confidence=Decimal("85.00"),
            conservative_rr=Decimal("2.50"),
            trigger_timestamp=now,
            requested_risk=Decimal("1000.00"),
            requested_notional=Decimal("10000.00"),
        )
        c2 = CompetingCandidate(
            candidate_id="c2",
            symbol="STOCK_B",
            scanner_score=Decimal("87.50"),  # Same 2-point band (88-86)
            gemini_confidence=Decimal("90.00"),
            conservative_rr=Decimal("3.00"),  # Better R:R!
            trigger_timestamp=now,
            requested_risk=Decimal("1000.00"),
            requested_notional=Decimal("10000.00"),
        )
        c3 = CompetingCandidate(
            candidate_id="c3",
            symbol="STOCK_C",
            scanner_score=Decimal("82.00"),  # Lower band
            gemini_confidence=Decimal("95.00"),
            conservative_rr=Decimal("4.00"),
            trigger_timestamp=earlier,
            requested_risk=Decimal("1000.00"),
            requested_notional=Decimal("10000.00"),
        )

        res = sort_competing_candidates([c1, c2, c3])
        self.assertFalse(res.has_capacity_conflict)
        # c2 wins first (better R:R within top band), then c1, then c3 (lower band)
        self.assertEqual([c.candidate_id for c in res.ranked_candidates], ["c2", "c1", "c3"])

    def test_sizing_minimum_viability_check(self):
        """Calculated size must achieve >= 50% approved leg budget and >= 4 shares."""
        # Approved budget: 1,000. Per share risk = 500 - 480 = 20.
        # If available headroom is only 400 (40% < 50% threshold) -> Rejected as non-viable!
        sizing = calculate_leg_sizing(
            leg_risk_budget=Decimal("400.00"),
            entry_price=Decimal("500.00"),
            stop_price=Decimal("480.00"),
            max_notional_cap=Decimal("20000.00"),
            min_shares_for_position=4,
            min_viability_pct=Decimal("0.50"),
            lot_size=1,
            approved_leg_risk_budget=Decimal("1000.00"),
        )
        self.assertFalse(sizing.is_viable)
        self.assertIn("50% viability threshold", sizing.rejection_reason)


class TestDurableStateCrashRecovery(unittest.IsolatedAsyncioTestCase):
    """Tests for crash recovery, state reconstruction, and position re-arming."""

    async def test_restore_rejected_exit_position_rearms_state(self):
        """When an exit intent is rejected by the broker, position is restored to its prior state."""
        position_id = uuid4()
        order_intent_id = uuid4()
        db = AsyncMock()

        db.execute.side_effect = [
            # 1. SELECT position
            FakeDbResult({
                "id": position_id,
                "state": "exit_pending",
                "open_quantity": 50,
                "trailing_stop": None,
                "t2_filled_shares": 0,
                "t2_shares": 0,
            }),
            # 2. SELECT prior from_state
            FakeDbResult({"from_state": "open"}),
            # 3. UPDATE positions SET state = 'open'
            FakeDbResult(),
            # 4. INSERT position_events
            FakeDbResult(),
            # 5. INSERT system_events
            FakeDbResult(),
        ]

        restored_state = await restore_rejected_exit_position(
            db,
            position_id=position_id,
            order_intent_id=order_intent_id,
            trade_instruction_id=None,
            reason="Broker exchange connectivity drop",
            details={"code": "EXCHANGE_OFFLINE"},
        )
        self.assertEqual(restored_state, "open")
        self.assertEqual(db.execute.await_count, 5)


if __name__ == "__main__":
    unittest.main()
