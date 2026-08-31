import unittest
from decimal import Decimal

from app.domain.p10_sizing import (
    EntryTemplate,
    calculate_leg_sizing,
    apportion_staged_exits,
    solve_stop_tightening,
    solve_risk_reduction_exit,
    TEMPLATE_CONFIG,
)


class TestP10Sizing(unittest.TestCase):
    def test_template_configs(self):
        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.SINGLE]["leg_allocations"], [Decimal("1.00")])
        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.SINGLE]["breakout_bar_rvol_threshold"], Decimal("1.75"))

        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.TWO_LEG]["leg_allocations"], [Decimal("0.60"), Decimal("0.40")])
        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.TWO_LEG]["breakout_bar_rvol_threshold"], Decimal("1.50"))

        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.TWO_LEG_STAGED]["leg_allocations"], [Decimal("0.50"), Decimal("0.50")])
        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.TWO_LEG_STAGED]["breakout_bar_rvol_threshold"], Decimal("1.50"))

        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.THREE_LEG_FRONT]["leg_allocations"], [Decimal("0.50"), Decimal("0.30"), Decimal("0.20")])
        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.THREE_LEG_FRONT]["breakout_bar_rvol_threshold"], Decimal("1.50"))

        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.THREE_LEG_BALANCED]["leg_allocations"], [Decimal("0.40"), Decimal("0.30"), Decimal("0.30")])
        self.assertEqual(TEMPLATE_CONFIG[EntryTemplate.THREE_LEG_BALANCED]["breakout_bar_rvol_threshold"], Decimal("1.50"))

    def test_calculate_leg_sizing_normal(self):
        # 10,000 risk budget, entry 500, stop 480 (R=20 per share), max notional 150,000
        # risk_shares = 10000 / 20 = 500
        # notional_shares = 150000 / 500 = 300
        # shares = min(500, 300) = 300
        res = calculate_leg_sizing(
            leg_risk_budget=Decimal("10000.00"),
            entry_price=Decimal("500.00"),
            stop_price=Decimal("480.00"),
            max_notional_cap=Decimal("150000.00"),
        )
        self.assertTrue(res.is_viable)
        self.assertEqual(res.shares, 300)
        self.assertEqual(res.allocated_risk, Decimal("6000.00"))
        self.assertEqual(res.allocated_notional, Decimal("150000.00"))

    def test_calculate_leg_sizing_rejects_sub_viability(self):
        # 10,000 risk budget, but max notional only allows 4,000 risk (< 5,000 threshold = 50%)
        res = calculate_leg_sizing(
            leg_risk_budget=Decimal("10000.00"),
            entry_price=Decimal("500.00"),
            stop_price=Decimal("480.00"),
            max_notional_cap=Decimal("50000.00"),  # Allows 100 shares -> 2,000 risk (< 50%)
        )
        self.assertFalse(res.is_viable)
        self.assertIn("50% viability threshold", res.rejection_reason)

    def test_apportion_staged_exits(self):
        # Divisible by 4: 100 shares -> 25 / 25 / 25 / 25
        app100 = apportion_staged_exits(100)
        self.assertEqual(app100.t1_shares, 25)
        self.assertEqual(app100.t2_shares, 25)
        self.assertEqual(app100.t3_shares, 25)
        self.assertEqual(app100.runner_shares, 25)
        self.assertEqual(app100.total_shares, 100)

        # Remainder 1: 101 shares -> 26 / 25 / 25 / 25
        app101 = apportion_staged_exits(101)
        self.assertEqual(app101.t1_shares, 26)
        self.assertEqual(app101.t2_shares, 25)
        self.assertEqual(app101.t3_shares, 25)
        self.assertEqual(app101.runner_shares, 25)

        # Remainder 2: 102 shares -> 26 / 26 / 25 / 25
        app102 = apportion_staged_exits(102)
        self.assertEqual(app102.t1_shares, 26)
        self.assertEqual(app102.t2_shares, 26)
        self.assertEqual(app102.t3_shares, 25)
        self.assertEqual(app102.runner_shares, 25)

        # Remainder 3: 103 shares -> 26 / 26 / 26 / 25
        app103 = apportion_staged_exits(103)
        self.assertEqual(app103.t1_shares, 26)
        self.assertEqual(app103.t2_shares, 26)
        self.assertEqual(app103.t3_shares, 26)
        self.assertEqual(app103.runner_shares, 25)

    def test_solve_stop_tightening_success(self):
        # 100 shares bought at 505 (slippage from pivot 500), stop 475
        # Current risk = 100 * (505 - 475) = 3000 (approved budget was 2500)
        # Base low = 482.00, corridor max = 481.95
        # Required stop = 505 - (2500 / 100) = 505 - 25 = 480.00
        res = solve_stop_tightening(
            position_shares=100,
            entry_vwap=Decimal("505.00"),
            current_stop=Decimal("475.00"),
            base_low=Decimal("482.00"),
            approved_max_risk=Decimal("2500.00"),
        )
        self.assertTrue(res.can_tighten)
        self.assertEqual(res.new_stop, Decimal("480.00"))
        self.assertLessEqual(res.residual_risk, Decimal("2500.00"))

    def test_solve_risk_reduction_exit_trims_correctly(self):
        # 200 shares bought at 500, stop 475 (R=25)
        # Total risk = 200 * 25 = 5000. Approved budget = 3500.
        # Allowed remaining shares = floor(3500 / 25) = 140 shares.
        # Exit shares = 200 - 140 = 60 shares.
        res = solve_risk_reduction_exit(
            position_shares=200,
            entry_vwap=Decimal("500.00"),
            effective_stop=Decimal("475.00"),
            approved_max_risk=Decimal("3500.00"),
            max_notional_cap=Decimal("150000.00"),
        )
        self.assertTrue(res.is_successful)
        self.assertEqual(res.exit_shares, 60)
        self.assertEqual(res.remaining_shares, 140)
        self.assertEqual(res.remaining_risk, Decimal("3500.00"))
        self.assertEqual(res.rounding_residual, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
