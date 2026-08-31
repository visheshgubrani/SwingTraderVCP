import datetime as dt
import unittest
from decimal import Decimal

from app.domain.p10_triggers import (
    BALANCED_BREAKOUT_POLICY_V3,
    BREAKOUT_BAR_SIGNAL_POLICY_V2,
    FiveMinuteBar,
    DailySessionBar,
    calculate_bar_relative_volume,
    evaluate_balanced_breakout_signal,
    evaluate_intraday_trigger,
    evaluate_add_leg_gates,
    calculate_relative_volume,
)
from app.domain.p10_sizing import EntryTemplate


class TestP10Triggers(unittest.TestCase):
    def test_relative_volume_calculation(self):
        # ADV20 = 1,000,000, expected fraction = 0.20 -> expected cum volume = 200,000
        # Actual cumulative volume = 400,000 -> rvol = 2.0x
        rvol = calculate_relative_volume(400_000, 1_000_000, Decimal("0.20"))
        self.assertEqual(rvol, Decimal("2.0000"))

    def test_breakout_bar_relative_volume_calculation(self):
        rvol = calculate_bar_relative_volume(
            120_000,
            1_000_000,
            Decimal("0.03"),
        )
        self.assertEqual(rvol, Decimal("4.0000"))

    def test_evaluate_intraday_trigger_skips_first_15m(self):
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 9, 20),  # 09:20 is within first 15 mins
            open=Decimal("500"),
            high=Decimal("512"),
            low=Decimal("499"),
            close=Decimal("510"),
            volume=50_000,
            cumulative_volume=100_000,
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 9, 25),
            open=Decimal("510"),
            high=Decimal("515"),
            low=Decimal("508"),
            close=Decimal("512"),
            volume=40_000,
            cumulative_volume=140_000,
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("505.00"),
            chase_ceiling=Decimal("515.00"),
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.05"),
            conf_expected_fraction=Decimal("0.07"),
            required_rvol=Decimal("2.00"),
            current_market_price=Decimal("512.00"),
        )
        self.assertFalse(res.is_triggered)
        self.assertIn("15-minute exclusion window", res.rejection_reason)

    def test_evaluate_intraday_trigger_valid_two_bar(self):
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 9, 35),  # Post 09:30
            open=Decimal("500"),
            high=Decimal("512"),
            low=Decimal("499"),
            close=Decimal("508"),
            volume=100_000,
            cumulative_volume=200_000,  # rvol: 200k / (1M * 0.08 = 80k) = 2.5x >= 2.0x
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 9, 40),
            open=Decimal("508"),
            high=Decimal("514"),
            low=Decimal("506"),
            close=Decimal("510"),
            volume=80_000,
            cumulative_volume=280_000,  # rvol: 280k / (1M * 0.10 = 100k) = 2.8x >= 2.0x
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("505.00"),
            chase_ceiling=Decimal("515.00"),
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.08"),
            conf_expected_fraction=Decimal("0.10"),
            required_rvol=Decimal("2.00"),
            current_market_price=Decimal("511.00"),
        )
        self.assertTrue(res.is_triggered)
        self.assertTrue(res.signal_bar_valid)
        self.assertTrue(res.confirmation_bar_valid)
        self.assertTrue(res.chase_valid)

    def test_v2_uses_breakout_bar_rvol_and_price_only_confirmation(self):
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 10, 5),
            open=Decimal("500"),
            high=Decimal("512"),
            low=Decimal("499"),
            close=Decimal("508"),
            volume=120_000,
            cumulative_volume=300_000,
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 10, 10),
            open=Decimal("508"),
            high=Decimal("511"),
            low=Decimal("506"),
            close=Decimal("509"),
            volume=10_000,
            cumulative_volume=310_000,
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("505"),
            chase_ceiling=Decimal("507"),
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.30"),
            conf_expected_fraction=Decimal("0.33"),
            signal_expected_bar_fraction=Decimal("0.03"),
            conf_expected_bar_fraction=Decimal("0.03"),
            required_rvol=Decimal("2"),
            current_market_price=Decimal("509"),
            policy_version=BREAKOUT_BAR_SIGNAL_POLICY_V2,
        )
        self.assertTrue(res.is_triggered)
        self.assertEqual(res.signal_rvol, Decimal("4.0000"))
        self.assertEqual(res.signal_session_rvol, Decimal("1.0000"))
        self.assertEqual(res.confirmation_rvol, Decimal("0.3333"))
        self.assertFalse(res.chase_valid)

    def test_v2_rejects_weak_signal_even_when_session_rvol_is_high(self):
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 10, 5),
            open=Decimal("500"),
            high=Decimal("512"),
            low=Decimal("499"),
            close=Decimal("508"),
            volume=30_000,
            cumulative_volume=600_000,
        )
        conf_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 10, 10),
            open=Decimal("508"),
            high=Decimal("511"),
            low=Decimal("506"),
            close=Decimal("509"),
            volume=60_000,
            cumulative_volume=660_000,
        )
        res = evaluate_intraday_trigger(
            signal_bar=sig_bar,
            confirmation_bar=conf_bar,
            trigger_price=Decimal("505"),
            chase_ceiling=Decimal("515"),
            adv20_robust=1_000_000,
            signal_expected_fraction=Decimal("0.30"),
            conf_expected_fraction=Decimal("0.33"),
            signal_expected_bar_fraction=Decimal("0.03"),
            conf_expected_bar_fraction=Decimal("0.03"),
            required_rvol=Decimal("2"),
            current_market_price=Decimal("509"),
            policy_version=BREAKOUT_BAR_SIGNAL_POLICY_V2,
        )
        self.assertFalse(res.is_triggered)
        self.assertEqual(res.signal_session_rvol, Decimal("2.0000"))
        self.assertEqual(res.signal_rvol, Decimal("1.0000"))

    def test_evaluate_add_leg_gates_hold_and_base(self):
        # three_leg_front L2 requires Hold(1), then Base(2)
        # Preceding leg trigger = 500, High = 515, ATR14 = 10
        # Base constraints: range <= 15, high <= 515 + 2.5 = 517.5, vol < sma_vol
        sessions = [
            # Day 1: Hold satisfaction (close >= 500)
            DailySessionBar(
                date=dt.date(2026, 8, 17),
                open=Decimal("505"),
                high=Decimal("520"),
                low=Decimal("502"),
                close=Decimal("518"),
                volume=800_000,
                ema21=Decimal("495"),
                ema21_5d_ago=Decimal("490"),
                sma_volume_20=1_000_000,
            ),
            # Day 2: Base session 1
            DailySessionBar(
                date=dt.date(2026, 8, 18),
                open=Decimal("515"),
                high=Decimal("517"),
                low=Decimal("508"),
                close=Decimal("512"),
                volume=400_000,
                ema21=Decimal("497"),
                ema21_5d_ago=Decimal("491"),
                sma_volume_20=1_000_000,
            ),
            # Day 3: Base session 2
            DailySessionBar(
                date=dt.date(2026, 8, 19),
                open=Decimal("512"),
                high=Decimal("516"),
                low=Decimal("510"),
                close=Decimal("515"),
                volume=350_000,
                ema21=Decimal("500"),
                ema21_5d_ago=Decimal("492"),
                sma_volume_20=1_000_000,
            ),
        ]

        state = evaluate_add_leg_gates(
            template=EntryTemplate.THREE_LEG_FRONT,
            leg_index=2,
            preceding_leg_trigger=Decimal("500.00"),
            preceding_leg_high=Decimal("515.00"),
            atr14=Decimal("10.00"),
            completed_sessions_since_fill=sessions,
            current_stop=Decimal("475.00"),
        )
        self.assertTrue(state.is_gate_open)
        self.assertTrue(state.is_hold_satisfied)
        self.assertTrue(state.is_base_satisfied)
        self.assertTrue(state.is_trend_satisfied)
        self.assertEqual(state.base_low, Decimal("508.00"))
        self.assertEqual(state.base_high, Decimal("517.00"))
        # Recommended stop = 508 - (0.25 * 10) = 508 - 2.50 = 505.50
        self.assertEqual(state.recommended_new_stop, Decimal("505.50"))

    def test_v3_balanced_breakout_valid(self):
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 10, 5),
            open=Decimal("500"),
            high=Decimal("512"),
            low=Decimal("499"),
            close=Decimal("508"),
            volume=60_000,
            cumulative_volume=300_000,
        )
        res = evaluate_balanced_breakout_signal(
            signal_bar=sig_bar,
            trigger_price=Decimal("505.00"),
            adv20_robust=1_000_000,
            expected_bar_fraction=Decimal("0.03"),
            expected_cumulative_fraction=Decimal("0.30"),
            required_rvol=Decimal("1.50"),
        )
        self.assertTrue(res.is_triggered)
        self.assertTrue(res.signal_bar_valid)
        self.assertEqual(res.signal_rvol, Decimal("2.0000"))
        self.assertEqual(res.signal_session_rvol, Decimal("1.0000"))
        self.assertIsNone(res.rejection_reason)

    def test_v3_balanced_breakout_skips_first_15m(self):
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 9, 20),
            open=Decimal("500"),
            high=Decimal("512"),
            low=Decimal("499"),
            close=Decimal("508"),
            volume=60_000,
            cumulative_volume=100_000,
        )
        res = evaluate_balanced_breakout_signal(
            signal_bar=sig_bar,
            trigger_price=Decimal("505.00"),
            adv20_robust=1_000_000,
            expected_bar_fraction=Decimal("0.03"),
            expected_cumulative_fraction=Decimal("0.10"),
            required_rvol=Decimal("1.50"),
        )
        self.assertFalse(res.is_triggered)
        self.assertIn("15-minute exclusion window", res.rejection_reason)

    def test_v3_balanced_breakout_volume_rejected_but_session_rvol_computed(self):
        sig_bar = FiveMinuteBar(
            bar_time=dt.datetime(2026, 8, 17, 10, 5),
            open=Decimal("500"),
            high=Decimal("512"),
            low=Decimal("499"),
            close=Decimal("508"),
            volume=30_000,
            cumulative_volume=600_000,
        )
        res = evaluate_balanced_breakout_signal(
            signal_bar=sig_bar,
            trigger_price=Decimal("505.00"),
            adv20_robust=1_000_000,
            expected_bar_fraction=Decimal("0.03"),
            expected_cumulative_fraction=Decimal("0.30"),
            required_rvol=Decimal("1.50"),
        )
        self.assertFalse(res.is_triggered)
        self.assertEqual(res.signal_rvol, Decimal("1.0000"))
        self.assertEqual(res.signal_session_rvol, Decimal("2.0000"))
        self.assertIn("Signal bar rvol", res.rejection_reason)


if __name__ == "__main__":
    unittest.main()
