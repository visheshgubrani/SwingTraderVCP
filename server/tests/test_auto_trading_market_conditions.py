"""Comprehensive test suite for Auto-Trading under real market conditions.

Covers:
1. Intraday Entry Confirmation & 15-minute Exclusion Window
2. Gap Up on Entry (within chase vs above chase ceiling)
3. MPP Slippage Handling (Stop Tightening Corridor vs Risk Reduction Trim vs Invalid Fill Exit)
4. Staged Exits (Sequential T1/T2/T3/Runner, Break-Even Ratchet, 2xATR High-Water Trailing)
5. Multi-Target Gap-Up Exits (Consolidated single-order multi-target exits)
6. Gap Down / Flash Crash below Structural Stop Loss
7. Multi-Leg Scale-Ins (Hold + Base + EMA21 Trend Filter, Stop Ratchet, Expirations)
8. Extreme Market Conditions (Stale ticks, non-positive ticks, <= 10 OPS Rate Limiter)
"""

import asyncio
import datetime as dt
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.domain.p10_geometry import (
    calculate_chase_ceiling,
    compute_atr14,
    entry_vwap_invalidates_t1_rr,
    CandleData,
)
from app.domain.p10_sizing import (
    apportion_staged_exits,
    calculate_leg_sizing,
    solve_risk_reduction_exit,
    solve_stop_tightening,
)
from app.domain.p10_triggers import (
    DailySessionBar,
    FiveMinuteBar,
    calculate_relative_volume,
    evaluate_add_leg_gates,
    evaluate_intraday_trigger,
)
from app.services.execution_engine import RedisOrderRateLimiter
from app.services.staged_exit_manager import (
    StagedPositionState,
    allocate_cumulative_target_fill,
    evaluate_staged_position_tick,
)
from app.services.position_monitor import (
    MonitoredPosition,
    process_position_tick,
)


class TestAutoTradingMarketEntryConditions(unittest.TestCase):
    """Tests for intraday breakout triggers, 15m exclusion, and gap-ups."""

    def test_first_15_minutes_exclusion_window(self):
        """Bars occurring between 09:15 and 09:30 IST must be strictly rejected."""
        # 09:20 is inside exclusion window
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 20),
            open=Decimal("500.00"),
            high=Decimal("515.00"),
            low=Decimal("498.00"),
            close=Decimal("512.00"),
            volume=80_000,
            cumulative_volume=160_000,
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 25),
            open=Decimal("512.00"),
            high=Decimal("518.00"),
            low=Decimal("510.00"),
            close=Decimal("514.00"),
            volume=70_000,
            cumulative_volume=230_000,
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("505.00"),
            chase_ceiling=Decimal("520.00"),
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.05"),
            conf_expected_fraction=Decimal("0.07"),
            required_rvol=Decimal("2.00"),
            current_market_price=Decimal("514.00"),
        )
        self.assertFalse(res.is_triggered)
        self.assertIn("15-minute exclusion window", res.rejection_reason)

    def test_valid_two_bar_confirmation_with_high_rvol(self):
        """Breakout confirmed when both signal and confirmation bars close >= trigger with required RVOL."""
        # Post 09:30
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 35),
            open=Decimal("502.00"),
            high=Decimal("512.00"),
            low=Decimal("500.00"),
            close=Decimal("510.00"),  # > 505 trigger
            volume=100_000,
            cumulative_volume=200_000,  # 200k / (1M * 0.08 = 80k) = 2.5x >= 2.0x
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 40),
            open=Decimal("510.00"),
            high=Decimal("515.00"),
            low=Decimal("508.00"),
            close=Decimal("512.00"),  # > 505 trigger
            volume=90_000,
            cumulative_volume=290_000,  # 290k / (1M * 0.10 = 100k) = 2.9x >= 2.0x
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("505.00"),
            chase_ceiling=Decimal("520.00"),
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.08"),
            conf_expected_fraction=Decimal("0.10"),
            required_rvol=Decimal("2.00"),
            current_market_price=Decimal("512.00"),
        )
        self.assertTrue(res.is_triggered)
        self.assertTrue(res.signal_bar_valid)
        self.assertTrue(res.confirmation_bar_valid)
        self.assertTrue(res.chase_valid)

    def test_confirmation_fails_if_bar2_closes_below_trigger(self):
        """If confirmation bar closes back below trigger level, breakout fails."""
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 35),
            open=Decimal("502.00"),
            high=Decimal("512.00"),
            low=Decimal("500.00"),
            close=Decimal("508.00"),  # > 505
            volume=100_000,
            cumulative_volume=200_000,
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 40),
            open=Decimal("508.00"),
            high=Decimal("509.00"),
            low=Decimal("501.00"),
            close=Decimal("503.00"),  # <= 505 (failed breakout)
            volume=80_000,
            cumulative_volume=280_000,
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("505.00"),
            chase_ceiling=Decimal("520.00"),
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.08"),
            conf_expected_fraction=Decimal("0.10"),
            required_rvol=Decimal("2.00"),
            current_market_price=Decimal("503.00"),
        )
        self.assertFalse(res.is_triggered)
        self.assertFalse(res.confirmation_bar_valid)
        self.assertIn("Confirmation bar close", res.rejection_reason)

    def test_confirmation_fails_on_weak_relative_volume(self):
        """If volume does not meet template requirement (e.g. 2.0x), trigger is rejected."""
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 35),
            open=Decimal("502.00"),
            high=Decimal("512.00"),
            low=Decimal("500.00"),
            close=Decimal("508.00"),
            volume=50_000,
            cumulative_volume=100_000,  # 100k / (1M * 0.08 = 80k) = 1.25x < 2.0x
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 40),
            open=Decimal("508.00"),
            high=Decimal("512.00"),
            low=Decimal("506.00"),
            close=Decimal("510.00"),
            volume=40_000,
            cumulative_volume=140_000,  # 140k / (1M * 0.10 = 100k) = 1.40x < 2.0x
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("505.00"),
            chase_ceiling=Decimal("520.00"),
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.08"),
            conf_expected_fraction=Decimal("0.10"),
            required_rvol=Decimal("2.00"),
            current_market_price=Decimal("510.00"),
        )
        self.assertFalse(res.is_triggered)
        self.assertFalse(res.signal_bar_valid)
        self.assertIn("Signal bar rvol", res.rejection_reason)

    def test_gap_up_on_entry_within_chase_ceiling_allowed(self):
        """Gap up above trigger but below chase ceiling is valid and allowed."""
        # Entry = 500, Stop = 480. R = 20.
        # Chase ceiling = 500 + min(2% of 500 = 10, 0.5 * 20 = 10) = 510.00
        chase, r_dist = calculate_chase_ceiling(Decimal("500.00"), Decimal("480.00"))
        self.assertEqual(chase, Decimal("510.00"))
        self.assertEqual(r_dist, Decimal("20.00"))

        # Market opens/ticks at 506.00 (above 500 entry, but below 510 chase ceiling)
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 35),
            open=Decimal("505.00"),
            high=Decimal("508.00"),
            low=Decimal("504.00"),
            close=Decimal("506.00"),
            volume=120_000,
            cumulative_volume=240_000,
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 40),
            open=Decimal("506.00"),
            high=Decimal("509.00"),
            low=Decimal("505.00"),
            close=Decimal("507.00"),
            volume=100_000,
            cumulative_volume=340_000,
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("500.00"),
            chase_ceiling=chase,
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.08"),
            conf_expected_fraction=Decimal("0.10"),
            required_rvol=Decimal("2.00"),
            current_market_price=Decimal("507.00"),
        )
        self.assertTrue(res.is_triggered)
        self.assertTrue(res.chase_valid)

    def test_gap_up_on_entry_above_chase_ceiling_rejected(self):
        """Gap up exceeding the chase ceiling is blocked before order placement."""
        chase, _ = calculate_chase_ceiling(Decimal("500.00"), Decimal("480.00"))  # 510.00

        # Market price is 514.00 (> 510.00 chase ceiling)
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 35),
            open=Decimal("512.00"),
            high=Decimal("516.00"),
            low=Decimal("511.00"),
            close=Decimal("514.00"),
            volume=150_000,
            cumulative_volume=300_000,
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 25, 9, 40),
            open=Decimal("514.00"),
            high=Decimal("518.00"),
            low=Decimal("513.00"),
            close=Decimal("515.00"),
            volume=120_000,
            cumulative_volume=420_000,
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("500.00"),
            chase_ceiling=chase,
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.08"),
            conf_expected_fraction=Decimal("0.10"),
            required_rvol=Decimal("2.00"),
            current_market_price=Decimal("514.50"),
        )
        self.assertFalse(res.is_triggered)
        self.assertFalse(res.chase_valid)
        self.assertIn("exceeds chase ceiling", res.rejection_reason)


class TestAutoTradingSlippageAndCorrections(unittest.TestCase):
    """Tests for MPP slippage, corridor stop tightening, risk trims, and invalid fill exit."""

    def test_mpp_slippage_corridor_stop_tightening(self):
        """When fill VWAP slips higher (within chase band), software stop is tightened within corridor."""
        # Approved trade: 100 shares, planned entry 500, stop 480 (risk 2,000 budget)
        # Actual fill VWAP: 504.00 (slippage +4.00 -> uncorrected risk = 100 * (504 - 480) = 2,400 > 2,000)
        # Base low = 486.00 -> corridor max = 486.00 - 0.05 = 485.95
        # Required stop = 504 - (2000 / 100) = 484.00
        # Since 484.00 is > 480 and <= 485.95, can_tighten is True!
        tighten_res = solve_stop_tightening(
            position_shares=100,
            entry_vwap=Decimal("504.00"),
            current_stop=Decimal("480.00"),
            base_low=Decimal("486.00"),
            approved_max_risk=Decimal("2000.00"),
            tick_size=Decimal("0.05"),
        )
        self.assertTrue(tighten_res.can_tighten)
        self.assertEqual(tighten_res.new_stop, Decimal("484.00"))
        self.assertEqual(tighten_res.residual_risk, Decimal("2000.00"))

    def test_mpp_slippage_trim_when_corridor_insufficient(self):
        """When stop tightening is structurally invalid or insufficient, a minimal whole-lot trim exit is solved."""
        # Approved trade: 100 shares, planned entry 500, stop 480 (risk 2,000 budget, max notional 55,000)
        # Actual fill VWAP: 508.00 (slippage +8.00)
        # Base low is 481.00 -> corridor max = 480.95 -> cannot tighten stop to 488.00!
        tighten_res = solve_stop_tightening(
            position_shares=100,
            entry_vwap=Decimal("508.00"),
            current_stop=Decimal("480.00"),
            base_low=Decimal("481.00"),
            approved_max_risk=Decimal("2000.00"),
            tick_size=Decimal("0.05"),
        )
        self.assertFalse(tighten_res.can_tighten)

        # Risk reduction trim solver:
        # Per share risk = 508 - 480 = 28.00
        # Allowed remaining shares = 2000 / 28 = 71.42 -> floor = 71 shares
        # Trim exit shares = 100 - 71 = 29 shares
        # Remaining risk = 71 * 28 = 1988.00 <= 2000.00
        trim_res = solve_risk_reduction_exit(
            position_shares=100,
            entry_vwap=Decimal("508.00"),
            effective_stop=Decimal("480.00"),
            approved_max_risk=Decimal("2000.00"),
            max_notional_cap=Decimal("55000.00"),
            lot_size=1,
            current_price=Decimal("508.00"),
        )
        self.assertTrue(trim_res.is_successful)
        self.assertEqual(trim_res.exit_shares, 29)
        self.assertEqual(trim_res.remaining_shares, 71)
        self.assertLessEqual(trim_res.remaining_risk, Decimal("2000.00"))

    def test_extreme_slippage_triggers_invalid_fill_exit(self):
        """If fill VWAP makes T1 provide less than 1R from that VWAP, invalid_fill_exit is triggered."""
        # Planned: entry 500, stop 480 (R = 20). T1 = 540 (2R = 40).
        # If fill slipped to 525.00:
        # Effective stop = 480. Per-share risk = 525 - 480 = 45.
        # Distance to T1 = 540 - 525 = 15 < 45 (0.33R < 1.0R)!
        is_invalid = entry_vwap_invalidates_t1_rr(
            t1=Decimal("540.00"),
            entry_vwap=Decimal("525.00"),
            current_stop=Decimal("480.00"),
        )
        self.assertTrue(is_invalid)

    def test_fill_within_chase_keeps_valid_t1_rr(self):
        """Fill within acceptable chase keeps T1 >= 1R."""
        # Planned: entry 500, stop 480 (R = 20). T1 = 540 (2R).
        # Fill at 505:
        # Per-share risk = 505 - 480 = 25.
        # Distance to T1 = 540 - 505 = 35 >= 25 (1.4R >= 1.0R)!
        is_invalid = entry_vwap_invalidates_t1_rr(
            t1=Decimal("540.00"),
            entry_vwap=Decimal("505.00"),
            current_stop=Decimal("480.00"),
        )
        self.assertFalse(is_invalid)


class TestAutoTradingStagedExitsAndGaps(unittest.TestCase):
    """Tests for T1/T2/T3, break-even ratchet, high-water trailing, multi-target gaps, and gap-downs."""

    def setUp(self):
        self.pos = StagedPositionState(
            id=uuid4(),
            symbol="NSE:RELIANCE-EQ",
            side="long",
            state="open",
            open_quantity=100,
            weighted_entry_price=Decimal("1000.00"),
            current_stop=Decimal("950.00"),
            t1_target=Decimal("1060.00"),
            t2_target=Decimal("1120.00"),
            t3_target=Decimal("1180.00"),
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
            atr14=Decimal("30.00"),
            tick_size=Decimal("0.05"),
        )

    def test_t1_trigger_requests_25_shares(self):
        """LTP hitting T1 requests exactly 25% shares."""
        action = evaluate_staged_position_tick(self.pos, Decimal("1062.00"))
        self.assertEqual(action.action_type, "target_exit")
        self.assertEqual(action.exit_shares, 25)
        self.assertEqual(action.exit_purpose, "target_1")
        self.assertEqual(action.crossed_targets, (1,))

    def test_multi_target_gap_up_consolidates_into_single_exit(self):
        """Sudden overnight gap up crossing T1 (1060) and T2 (1120) produces a single 50% cumulative exit."""
        # Price gaps to 1130.00 (above both T1 and T2)
        action = evaluate_staged_position_tick(self.pos, Decimal("1130.00"))
        self.assertEqual(action.action_type, "target_exit")
        self.assertEqual(action.exit_shares, 50)  # 25 (T1) + 25 (T2)
        self.assertEqual(action.exit_purpose, "target_2")
        self.assertEqual(action.crossed_targets, (1, 2))

        # Test cumulative fill allocation: allocates 25 to T1 and 25 to T2
        allocation = allocate_cumulative_target_fill(
            self.pos,
            exit_purpose="target_2",
            fill_quantity=50,
        )
        self.assertEqual(allocation.t1, 25)
        self.assertEqual(allocation.t2, 25)
        self.assertEqual(allocation.t3, 0)

    def test_triple_target_gap_up_consolidates_into_75_percent_exit(self):
        """Massive gap up crossing T1, T2, and T3 at once produces a single 75% cumulative exit."""
        # Price gaps to 1200.00 (above T3 1180.00)
        action = evaluate_staged_position_tick(self.pos, Decimal("1200.00"))
        self.assertEqual(action.action_type, "target_exit")
        self.assertEqual(action.exit_shares, 75)  # 25 + 25 + 25
        self.assertEqual(action.exit_purpose, "target_3")
        self.assertEqual(action.crossed_targets, (1, 2, 3))

    def test_gap_down_below_structural_stop_loss(self):
        """Sudden gap down opening below structural stop immediately requests 100% full exit."""
        # Stop is 950.00, price opens / flash drops to 920.00
        action = evaluate_staged_position_tick(self.pos, Decimal("920.00"))
        self.assertEqual(action.action_type, "stop_loss")
        self.assertEqual(action.exit_shares, 100)
        self.assertEqual(action.exit_purpose, "stop_loss")

    def test_trailing_stop_progression_after_t2(self):
        """After T1 and T2 fill, position enters trailing_active with 2xATR high-water mark."""
        self.pos.state = "trailing_active"
        self.pos.open_quantity = 50
        self.pos.t1_filled_shares = 25
        self.pos.t2_filled_shares = 25
        # High water mark established at 1150.00
        # Trail distance = 2 * 30 = 60.00 -> Trailing stop = 1150 - 60 = 1090.00
        self.pos.high_water_mark = Decimal("1150.00")
        self.pos.trailing_stop = Decimal("1090.00")

        # Price advances to 1180.00 -> should update high water mark to 1180 and trail to 1120
        action_up = evaluate_staged_position_tick(self.pos, Decimal("1180.00"))
        # At 1180.00, T3 (1180.00) is hit!
        self.assertEqual(action_up.action_type, "target_exit")
        self.assertEqual(action_up.exit_shares, 25)
        self.assertEqual(action_up.exit_purpose, "target_3")

        # Now simulate T3 filled, only 25 runner shares remain:
        self.pos.open_quantity = 25
        self.pos.t3_filled_shares = 25
        self.pos.high_water_mark = Decimal("1200.00")
        self.pos.trailing_stop = Decimal("1140.00")  # 1200 - 60

        # Price pulls back to 1135.00 (< trailing stop 1140.00) -> Runner exit triggered!
        action_runner = evaluate_staged_position_tick(self.pos, Decimal("1135.00"))
        self.assertEqual(action_runner.action_type, "trailing_exit")
        self.assertEqual(action_runner.exit_shares, 25)
        self.assertEqual(action_runner.exit_purpose, "runner_trail")


class TestAutoTradingMultiLegScaleIns(unittest.TestCase):
    """Tests for multi-leg scale-ins (Hold, Base, EMA21, Base High trigger, Stop Ratchet)."""

    def test_add_leg_hold_and_base_lifecycle(self):
        """three_leg_front L2 requires Hold(1) then Base(2) with EMA21 trend qualification."""
        # Preceding leg trigger = 500, High = 515, ATR14 = 10
        # Base constraints: range <= 1.5 * 10 = 15, high <= 515 + 2.5 = 517.5, vol < sma_vol
        sessions = [
            # Day 1: Hold satisfaction (close >= 500)
            DailySessionBar(
                date=dt.date(2026, 8, 17),
                open=Decimal("505"),
                high=Decimal("520"),
                low=Decimal("502"),
                close=Decimal("512"),
                volume=1_200_000,
                ema21=Decimal("504"),
                ema21_5d_ago=Decimal("500"),
                sma_volume_20=1_000_000,
            ),
            # Day 2: Base Day 1 (range 514-506=8 <= 15, high 514 <= 517.5, vol 800k < 1M)
            DailySessionBar(
                date=dt.date(2026, 8, 18),
                open=Decimal("510"),
                high=Decimal("514"),
                low=Decimal("506"),
                close=Decimal("509"),
                volume=800_000,
                ema21=Decimal("506"),
                ema21_5d_ago=Decimal("500"),
                sma_volume_20=1_000_000,
            ),
            # Day 3: Base Day 2 (range 515-508=7 <= 15, high 515 <= 517.5, vol 750k < 1M)
            DailySessionBar(
                date=dt.date(2026, 8, 19),
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
        # Current stop = 480.00
        res = evaluate_add_leg_gates(
            template="three_leg_front",
            leg_index=2,
            completed_sessions_since_fill=sessions,
            preceding_leg_trigger=Decimal("500.00"),
            preceding_leg_high=Decimal("515.00"),
            current_stop=Decimal("480.00"),
            atr14=Decimal("10.00"),
            tick_size=Decimal("0.05"),
        )
        self.assertTrue(res.is_gate_open)
        self.assertTrue(res.is_hold_satisfied)
        self.assertTrue(res.is_base_satisfied)
        self.assertTrue(res.is_trend_satisfied)
        # Base high (max high of base days: max(514, 515) = 515.00) becomes trigger!
        self.assertEqual(res.base_high, Decimal("515.00"))
        # Stop ratchets to Base low (min low of base days: min(506, 508) = 506.00) - 0.25*10 = 503.50
        self.assertEqual(res.recommended_new_stop, Decimal("503.50"))

    def test_add_leg_fails_when_ema21_trend_broken(self):
        """Add leg fails if price is below EMA21 or EMA21 is declining."""
        sessions = [
            DailySessionBar(
                date=dt.date(2026, 8, 17),
                open=Decimal("505"),
                high=Decimal("520"),
                low=Decimal("502"),
                close=Decimal("512"),
                volume=1_200_000,
                ema21=Decimal("514"),
                ema21_5d_ago=Decimal("515"),
                sma_volume_20=1_000_000,
            ),
            DailySessionBar(
                date=dt.date(2026, 8, 18),
                open=Decimal("510"),
                high=Decimal("514"),
                low=Decimal("506"),
                close=Decimal("509"),
                volume=800_000,
                ema21=Decimal("514"),
                ema21_5d_ago=Decimal("515"),
                sma_volume_20=1_000_000,
            ),
            DailySessionBar(
                date=dt.date(2026, 8, 19),
                open=Decimal("509"),
                high=Decimal("515"),
                low=Decimal("508"),
                close=Decimal("511"),
                volume=750_000,
                ema21=Decimal("515"),  # close 511 < ema21 515
                ema21_5d_ago=Decimal("510"),
                sma_volume_20=1_000_000,
            ),
        ]
        res = evaluate_add_leg_gates(
            template="three_leg_front",
            leg_index=2,
            completed_sessions_since_fill=sessions,
            preceding_leg_trigger=Decimal("500.00"),
            preceding_leg_high=Decimal("515.00"),
            current_stop=Decimal("480.00"),
            atr14=Decimal("10.00"),
            tick_size=Decimal("0.05"),
        )
        self.assertFalse(res.is_gate_open)
        self.assertFalse(res.is_trend_satisfied)
        self.assertIn("Trend filter failed", res.rejection_reason)


class TestExtremeMarketConditionsAndRateLimiting(unittest.IsolatedAsyncioTestCase):
    """Tests for stale/invalid ticks and execution engine rate limiter under bursts."""

    async def test_position_monitor_drops_stale_and_non_positive_ticks(self):
        """Ticks with non-positive price or age > 10s must be dropped without processing."""
        from app.workers.position_monitor import PositionMonitorRuntime

        runtime = PositionMonitorRuntime()
        pos = MonitoredPosition(
            id=uuid4(),
            symbol="NSE:INFY-EQ",
            side="long",
            state="open",
            quantity=10,
            open_quantity=10,
            product_type="CNC",
            average_entry_price=Decimal("1500.00"),
            current_stop_loss=Decimal("1450.00"),
            current_target=Decimal("1600.00"),
            trailing_rule={"type": "none"},
            tick_size=Decimal("0.05"),
        )
        runtime.positions_by_symbol["NSE:INFY-EQ"] = [pos]

        redis = AsyncMock()

        # Non-positive tick (ltp = 0.0)
        with patch("app.workers.position_monitor.process_position_tick") as mock_process:
            await runtime.handle_tick(redis, {
                "symbol": "NSE:INFY-EQ",
                "ltp": 0.0,
                "received_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            })
            mock_process.assert_not_called()

        # Stale tick (age = 30 seconds)
        old_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)).isoformat()
        with patch("app.workers.position_monitor.process_position_tick") as mock_process:
            await runtime.handle_tick(redis, {
                "symbol": "NSE:INFY-EQ",
                "ltp": 1400.0,
                "received_at": old_time,
            })
            mock_process.assert_not_called()

    async def test_order_rate_limiter_throttles_bursts(self):
        """Rate limiter script enforces <= 10 OPS token bucket."""
        redis = AsyncMock()
        # Simulate Redis eval returning 0 (immediate token) then 50 (wait 50ms) then 0
        redis.eval.side_effect = [0, 50, 0]

        limiter = RedisOrderRateLimiter(redis, rate=10)
        
        # First call: immediate
        await limiter.acquire()
        self.assertEqual(redis.eval.await_count, 1)

        # Second call: waits 50ms then gets token on retry
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await limiter.acquire()
            mock_sleep.assert_awaited_once_with(0.05)
            self.assertEqual(redis.eval.await_count, 3)


if __name__ == "__main__":
    unittest.main()
