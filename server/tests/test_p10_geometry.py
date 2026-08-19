import unittest
from decimal import Decimal

from app.domain.p10_geometry import (
    CandleData,
    compute_atr14,
    snap_to_tick,
    calculate_structural_stop,
    calculate_chase_ceiling,
    validate_proposal_targets,
    construct_and_validate_proposal,
)


class TestP10Geometry(unittest.TestCase):
    def test_snap_to_tick(self):
        self.assertEqual(snap_to_tick(Decimal("100.02")), Decimal("100.00"))
        self.assertEqual(snap_to_tick(Decimal("100.03")), Decimal("100.05"))
        self.assertEqual(snap_to_tick(Decimal("100.07")), Decimal("100.05"))
        self.assertEqual(snap_to_tick(Decimal("100.08")), Decimal("100.10"))

    def test_compute_atr14(self):
        # Generate 20 candles
        candles = [
            CandleData(open=100 + i, high=105 + i, low=95 + i, close=102 + i, volume=1000)
            for i in range(25)
        ]
        atr = compute_atr14(candles)
        self.assertGreater(atr, Decimal("0"))
        # Range is 10 for each candle
        self.assertAlmostEqual(float(atr), 10.0, places=1)

    def test_calculate_structural_stop(self):
        final_low = Decimal("480.00")
        atr14 = Decimal("20.00")
        # stop = 480 - (0.25 * 20) = 480 - 5 = 475.00
        stop = calculate_structural_stop(final_low, atr14)
        self.assertEqual(stop, Decimal("475.00"))

    def test_calculate_chase_ceiling(self):
        pivot = Decimal("500.00")
        stop = Decimal("475.00")  # R = 25
        # Cap pct = 500 * 0.02 = 10.00
        # Cap R = 25 * 0.5 = 12.50
        # Min slippage = 10.00
        # Ceiling = 500 + 10.00 = 510.00
        ceiling, r = calculate_chase_ceiling(pivot, stop)
        self.assertEqual(ceiling, Decimal("510.00"))
        self.assertEqual(r, Decimal("25.00"))

    def test_calculate_chase_ceiling_tight_stop(self):
        pivot = Decimal("500.00")
        stop = Decimal("492.00")  # R = 8
        # Cap pct = 500 * 0.02 = 10.00
        # Cap R = 8 * 0.5 = 4.00
        # Min slippage = 4.00
        # Ceiling = 500 + 4.00 = 504.00
        ceiling, r = calculate_chase_ceiling(pivot, stop)
        self.assertEqual(ceiling, Decimal("504.00"))
        self.assertEqual(r, Decimal("8.00"))

    def test_validate_proposal_targets_valid(self):
        pivot = Decimal("500.00")
        stop = Decimal("475.00")  # R = 25, 5% stop (< 8%)
        ceiling = Decimal("510.00")
        # Worst-fill R = ceiling - stop = 35.
        t1 = Decimal("550.00")
        t2 = Decimal("585.00")
        t3 = Decimal("620.00")

        valid, reason = validate_proposal_targets(pivot, stop, ceiling, t1, t2, t3)
        self.assertTrue(valid)
        self.assertIsNone(reason)

    def test_validate_proposal_targets_rejects_insufficient_rr(self):
        pivot = Decimal("500.00")
        stop = Decimal("475.00")  # pivot R = 25; worst-fill R = ceiling - stop = 35
        ceiling = Decimal("510.00")
        t1 = Decimal("525.00")  # 15 above ceiling => 15/35 = 0.43R
        t2 = Decimal("570.00")
        t3 = Decimal("600.00")

        valid, reason = validate_proposal_targets(pivot, stop, ceiling, t1, t2, t3)
        self.assertFalse(valid)
        self.assertIsNotNone(reason)
        self.assertIn("T1", reason)
        self.assertIn("0.43R", reason)
        self.assertIn("requires >= 1.0R", reason)
        self.assertNotIn("15.00R", reason)

    def test_construct_and_validate_proposal_rejects_wide_stop(self):
        pivot = Decimal("500.00")
        final_low = Decimal("450.00")
        atr14 = Decimal("40.00")
        # stop = 450 - 10 = 440 (R = 60 => 12% > 8%)
        geom = construct_and_validate_proposal(
            pivot_price=pivot,
            final_contraction_low=final_low,
            t1=Decimal("580.00"),
            t2=Decimal("640.00"),
            t3=Decimal("700.00"),
            atr14=atr14,
        )
        self.assertFalse(geom.is_valid)
        self.assertIn("exceeds maximum 8.0%", geom.rejection_reason)


if __name__ == "__main__":
    unittest.main()
