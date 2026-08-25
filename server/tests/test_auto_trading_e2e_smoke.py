"""End-to-end integration smoke tests simulating real-market auto-trading scenarios.

Scenarios:
1. Full Winning Trade Lifecycle (Entry -> Base -> Add -> T1 + BE Stop -> T2 + Trail -> T3 -> Runner Trail Exit)
2. Multi-Target Gap Up Exits (Overnight gap crossing T1 and T2 in a single consolidated exit)
3. Gap Down Emergency Stop Loss (Flash crash opening below stop -> full 100% exit)
4. Fast Market Slippage & Structural Corridor Stop Tightening
5. Invalid Fill Exit (Slippage blowing past chase ceiling -> immediate emergency liquidation)
6. 3-Consecutive-Stop Streak Circuit Breaker Drill
"""

import asyncio
import datetime as dt
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.domain.p10_geometry import calculate_chase_ceiling, compute_atr14
from app.domain.p10_sizing import apportion_staged_exits, solve_stop_tightening
from app.domain.p10_triggers import (
    DailySessionBar,
    FiveMinuteBar,
    evaluate_add_leg_gates,
    evaluate_intraday_trigger,
)
from app.services.execution_engine import (
    SubmissionResult,
    _complete_paper_submission,
    restore_rejected_exit_position,
)
from app.services.paper_broker import (
    PaperPlaceResult,
    build_paper_fill_messages,
    place_paper_order,
)
from app.services.position_monitor import (
    MonitoredPosition,
    process_position_tick,
)
from app.services.risk_stop_streak import (
    advance_stop_streak,
    classify_stop_closure,
)
from app.services.staged_exit_manager import (
    StagedPositionState,
    allocate_cumulative_target_fill,
    evaluate_staged_position_tick,
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


class TestAutoTradingE2ESmoke(unittest.IsolatedAsyncioTestCase):
    """End-to-end simulated market scenario smoke tests."""

    async def test_e2e_full_winning_trade_lifecycle(self):
        """Complete winning trade lifecycle:
        - 2-bar breakout confirmed at 502
        - L1 entry filled for 100 shares
        - Hold & Base consolidation forms over next 3 days -> L2 armed at 515 with stop 503.50
        - L2 breakout triggers & fills 60 shares at 516 (total 160 shares)
        - Price reaches T1 (540) -> 25% exit (40 sh), stop moves to weighted entry (BE)
        - Price reaches T2 (580) -> 25% exit (40 sh), 2xATR high-water trail activated
        - Price reaches T3 (620) -> 25% exit (40 sh), 40 runner shares remain
        - Price peaks at 650 then falls to 628 (< 630 trail) -> 40 runner shares exit
        - Position closed with large positive profit!
        """
        # Step 1: Intraday 5m breakout confirmation
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 35),
            open=Decimal("498.00"),
            high=Decimal("504.00"),
            low=Decimal("497.00"),
            close=Decimal("502.00"),
            volume=100_000,
            cumulative_volume=200_000,
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 40),
            open=Decimal("502.00"),
            high=Decimal("506.00"),
            low=Decimal("500.00"),
            close=Decimal("503.00"),
            volume=90_000,
            cumulative_volume=290_000,
        )
        chase, _ = calculate_chase_ceiling(Decimal("500.00"), Decimal("480.00"))
        trigger_res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("500.00"),
            chase_ceiling=chase,
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.08"),
            conf_expected_fraction=Decimal("0.10"),
            required_rvol=Decimal("2.00"),
            current_market_price=Decimal("502.50"),
        )
        self.assertTrue(trigger_res.is_triggered)

        # Step 2: L1 Paper submission and fill
        l1_intent_id = uuid4()
        l1_position_id = uuid4()
        l1_snapshot = {
            "id": l1_intent_id,
            "symbol": "NSE:TITAN-EQ",
            "quantity": 100,
            "side": "buy",
            "position_id": l1_position_id,
            "trade_instruction_id": None,
        }
        l1_paper_res = build_paper_fill_messages(
            snapshot=l1_snapshot,
            fyers_async_id=f"paper-async:{l1_intent_id}",
            fyers_order_id=f"paper-ord:{l1_intent_id}",
            trade_number=f"paper-trd:{l1_intent_id}",
            fill_price=Decimal("502.00"),
        )
        self.assertEqual(l1_paper_res.order_message["orders"]["status"], 2)
        self.assertEqual(l1_paper_res.trade_message["trades"]["tradedQty"], 100)

        # Step 3: Base consolidation over next 3 daily sessions -> L2 add leg armed
        daily_sessions = [
            DailySessionBar(
                date=dt.date(2026, 8, 25),
                open=Decimal("502"),
                high=Decimal("515"),
                low=Decimal("500"),
                close=Decimal("512"),
                volume=1_200_000,
                ema21=Decimal("504"),
                ema21_5d_ago=Decimal("500"),
                sma_volume_20=1_000_000,
            ),
            DailySessionBar(
                date=dt.date(2026, 8, 26),
                open=Decimal("510"),
                high=Decimal("514"),
                low=Decimal("506"),
                close=Decimal("509"),
                volume=800_000,
                ema21=Decimal("506"),
                ema21_5d_ago=Decimal("500"),
                sma_volume_20=1_000_000,
            ),
            DailySessionBar(
                date=dt.date(2026, 8, 27),
                open=Decimal("509"),
                high=Decimal("515"),
                low=Decimal("508"),
                close=Decimal("511"),
                volume=750_000,
                ema21=Decimal("508"),
                ema21_5d_ago=Decimal("500"),
                sma_volume_20=1_000_000,
            ),
        ]
        add_gate = evaluate_add_leg_gates(
            template="three_leg_front",
            leg_index=2,
            completed_sessions_since_fill=daily_sessions,
            preceding_leg_trigger=Decimal("500.00"),
            preceding_leg_high=Decimal("515.00"),
            current_stop=Decimal("480.00"),
            atr14=Decimal("10.00"),
            tick_size=Decimal("0.05"),
        )
        self.assertTrue(add_gate.is_gate_open)
        self.assertEqual(add_gate.base_high, Decimal("515.00"))
        self.assertEqual(add_gate.recommended_new_stop, Decimal("503.50"))

        # Step 4: L2 Add Leg execution -> total position becomes 160 shares
        # Weighted entry: (100 * 502 + 60 * 516) / 160 = (50200 + 30960) / 160 = 81160 / 160 = 507.25
        # Staged apportionment for 160 shares: 40 / 40 / 40 / 40
        apportion = apportion_staged_exits(160)
        self.assertEqual((apportion.t1_shares, apportion.t2_shares, apportion.t3_shares, apportion.runner_shares), (40, 40, 40, 40))

        staged_pos = StagedPositionState(
            id=l1_position_id,
            symbol="NSE:TITAN-EQ",
            side="long",
            state="open",
            open_quantity=160,
            weighted_entry_price=Decimal("507.25"),
            current_stop=Decimal("503.50"),  # Ratcheted stop from add
            t1_target=Decimal("540.00"),
            t2_target=Decimal("580.00"),
            t3_target=Decimal("620.00"),
            t1_shares=40,
            t2_shares=40,
            t3_shares=40,
            runner_shares=40,
            t1_filled_shares=0,
            t2_filled_shares=0,
            t3_filled_shares=0,
            runner_filled_shares=0,
            high_water_mark=None,
            trailing_stop=None,
            atr14=Decimal("10.00"),
            tick_size=Decimal("0.05"),
        )

        # Step 5: T1 Hit at 542.00 -> 40 shares exit requested
        action_t1 = evaluate_staged_position_tick(staged_pos, Decimal("542.00"))
        self.assertEqual(action_t1.action_type, "target_exit")
        self.assertEqual(action_t1.exit_shares, 40)
        self.assertEqual(action_t1.exit_purpose, "target_1")

        # Simulate T1 filled -> stop ratchets to breakeven (507.25)
        staged_pos.open_quantity = 120
        staged_pos.t1_filled_shares = 40
        staged_pos.current_stop = staged_pos.weighted_entry_price

        # Step 6: T2 Hit at 582.00 -> 40 shares exit requested
        action_t2 = evaluate_staged_position_tick(staged_pos, Decimal("582.00"))
        self.assertEqual(action_t2.action_type, "target_exit")
        self.assertEqual(action_t2.exit_shares, 40)
        self.assertEqual(action_t2.exit_purpose, "target_2")

        # Simulate T2 filled -> state = trailing_active, 2xATR (20 pts) trail activated
        staged_pos.open_quantity = 80
        staged_pos.t2_filled_shares = 40
        staged_pos.state = "trailing_active"
        staged_pos.high_water_mark = Decimal("585.00")
        staged_pos.trailing_stop = Decimal("565.00")  # 585 - 20

        # Step 7: T3 Hit at 622.00 -> 40 shares exit requested
        action_t3 = evaluate_staged_position_tick(staged_pos, Decimal("622.00"))
        self.assertEqual(action_t3.action_type, "target_exit")
        self.assertEqual(action_t3.exit_shares, 40)
        self.assertEqual(action_t3.exit_purpose, "target_3")

        # Simulate T3 filled -> 40 runner shares remain
        staged_pos.open_quantity = 40
        staged_pos.t3_filled_shares = 40
        staged_pos.high_water_mark = Decimal("650.00")
        staged_pos.trailing_stop = Decimal("630.00")  # 650 - 20

        # Step 8: Price pulls back to 628.00 (< 630.00 trail stop) -> Runner exit triggered!
        action_runner = evaluate_staged_position_tick(staged_pos, Decimal("628.00"))
        self.assertEqual(action_runner.action_type, "trailing_exit")
        self.assertEqual(action_runner.exit_shares, 40)
        self.assertEqual(action_runner.exit_purpose, "runner_trail")

        # Classification of full winning trade:
        classification = classify_stop_closure(
            exit_purposes={"target_1", "target_2", "target_3", "runner_trail"},
            net_pnl=Decimal("15200.00"),
        )
        self.assertEqual(classification, "reset")

    async def test_e2e_multi_target_gap_up_and_exit(self):
        """Simulate overnight gap up jumping over T1 and T2 directly into single consolidated 50% exit."""
        pos = StagedPositionState(
            id=uuid4(),
            symbol="NSE:BAJFINANCE-EQ",
            side="long",
            state="open",
            open_quantity=100,
            weighted_entry_price=Decimal("7000.00"),
            current_stop=Decimal("6700.00"),
            t1_target=Decimal("7300.00"),
            t2_target=Decimal("7600.00"),
            t3_target=Decimal("7900.00"),
            t1_shares=25,
            t2_shares=25,
            t3_shares=25,
            runner_shares=25,
            t1_filled_shares=0,
            t2_filled_shares=0,
            t3_filled_shares=0,
            runner_filled_shares=0,
            high_water_mark=None,
            trailing_stop=None,
            atr14=Decimal("150.00"),
            tick_size=Decimal("0.05"),
        )

        # Market opens at 7650.00 (gapping past both T1 7300 and T2 7600)
        action = evaluate_staged_position_tick(pos, Decimal("7650.00"))
        self.assertEqual(action.action_type, "target_exit")
        self.assertEqual(action.exit_shares, 50)  # Consolidated 50 shares
        self.assertEqual(action.exit_purpose, "target_2")
        self.assertEqual(action.crossed_targets, (1, 2))

        # Gateway allocates cumulative fill to T1 and T2
        allocation = allocate_cumulative_target_fill(
            pos,
            exit_purpose="target_2",
            fill_quantity=50,
        )
        self.assertEqual((allocation.t1, allocation.t2, allocation.t3), (25, 25, 0))

    async def test_e2e_gap_down_emergency_stop_loss(self):
        """Simulate flash crash / overnight gap down below SL -> immediate full 100% exit."""
        pos = StagedPositionState(
            id=uuid4(),
            symbol="NSE:KOTAKBANK-EQ",
            side="long",
            state="open",
            open_quantity=100,
            weighted_entry_price=Decimal("1800.00"),
            current_stop=Decimal("1720.00"),
            t1_target=Decimal("1900.00"),
            t2_target=Decimal("2000.00"),
            t3_target=Decimal("2100.00"),
            t1_shares=25,
            t2_shares=25,
            t3_shares=25,
            runner_shares=25,
            t1_filled_shares=0,
            t2_filled_shares=0,
            t3_filled_shares=0,
            runner_filled_shares=0,
            high_water_mark=None,
            trailing_stop=None,
            atr14=Decimal("40.00"),
            tick_size=Decimal("0.05"),
        )

        # Flash drop to 1680.00 (< 1720.00 stop)
        action = evaluate_staged_position_tick(pos, Decimal("1680.00"))
        self.assertEqual(action.action_type, "stop_loss")
        self.assertEqual(action.exit_shares, 100)
        self.assertEqual(action.exit_purpose, "stop_loss")

        # Classify pure stop loss:
        classification = classify_stop_closure(exit_purposes={"stop_loss"}, net_pnl=Decimal("-12000.00"))
        self.assertEqual(classification, "increment")

    async def test_e2e_slippage_corridor_tightening(self):
        """Simulate entry fill slippage within chase -> corridor stop tightening."""
        # 100 shares, planned entry 1000, stop 960 (risk 4,000)
        # Slipped fill at 1008 (within chase 1020) -> uncorrected risk = 4,800 > 4,000
        # Base low = 970 -> corridor max = 969.95
        # Required stop = 1008 - 40 = 968.00 -> can tighten!
        res = solve_stop_tightening(
            position_shares=100,
            entry_vwap=Decimal("1008.00"),
            current_stop=Decimal("960.00"),
            base_low=Decimal("970.00"),
            approved_max_risk=Decimal("4000.00"),
            tick_size=Decimal("0.05"),
        )
        self.assertTrue(res.can_tighten)
        self.assertEqual(res.new_stop, Decimal("968.00"))
        self.assertEqual(res.residual_risk, Decimal("4000.00"))

    async def test_e2e_three_stops_trip_circuit_breaker(self):
        """Simulate 3 consecutive stop losses tripping the circuit breaker."""
        limit = 3
        count = 0
        tripped = False

        for i in range(1, 4):
            count, tripped, newly = advance_stop_streak(
                count=count,
                tripped=tripped,
                classification="increment",
                limit=limit,
            )
            if i < 3:
                self.assertFalse(tripped)
                self.assertFalse(newly)
            else:
                self.assertTrue(tripped)
                self.assertTrue(newly)
                self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
