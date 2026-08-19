import datetime as dt
import random
import unittest
from decimal import Decimal

from app.domain.p10_geometry import (
    CandleData,
    ValidatedPatternAnchor,
    build_resistance_zones,
    compute_atr14,
    ground_pivot_to_resistance_zones,
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


class TestPivotResistanceZones(unittest.TestCase):
    def setUp(self) -> None:
        start = dt.date(2026, 1, 1)
        self.sessions = [start + dt.timedelta(days=index) for index in range(126)]

    def anchor(
        self,
        session_index: int,
        price: str,
        anchor_type: str = "contraction_high",
    ) -> ValidatedPatternAnchor:
        return ValidatedPatternAnchor(
            date=self.sessions[session_index],
            price=Decimal(price),
            anchor_type=anchor_type,
        )

    def test_complete_link_clusters_all_evidence_before_any_cap(self) -> None:
        anchors = [
            self.anchor(80 + index, str(4800 + index * 10))
            for index in range(9)
        ]
        anchors.append(self.anchor(100, "4905"))

        zones = build_resistance_zones(anchors, tolerance=Decimal("90"))

        self.assertEqual(len(zones), 2)
        self.assertEqual(len(zones[0].members), 9)
        self.assertEqual(zones[0].low, Decimal("4800"))
        self.assertEqual(zones[0].high, Decimal("4880"))
        self.assertEqual(zones[0].median, Decimal("4840"))
        self.assertEqual(zones[1].low, Decimal("4905"))

    def test_supported_older_boundary_is_exact_and_reproducible(self) -> None:
        supported_boundary = self.anchor(20, "4800", "resistance")
        unsupported_boundary = self.anchor(21, "4400", "resistance")
        anchors = [
            supported_boundary,
            unsupported_boundary,
            self.anchor(30, "4300", "contraction_low"),
            self.anchor(50, "4500", "contraction_low"),
            self.anchor(80, "4830"),
        ]

        grounding = ground_pivot_to_resistance_zones(
            pivot=Decimal("4810"),
            anchors=anchors,
            session_dates=self.sessions,
            frozen_atr14=Decimal("100"),
        )

        self.assertTrue(grounding.is_grounded)
        self.assertEqual(grounding.older_boundary_dates, (supported_boundary.date,))
        self.assertIn(supported_boundary, grounding.eligible_anchors)
        self.assertNotIn(unsupported_boundary, grounding.eligible_anchors)
        self.assertEqual(grounding.tolerance, Decimal("50.00"))

    def test_nearest_zone_tie_prefers_higher_and_ignores_input_order(self) -> None:
        anchors = [
            self.anchor(90, "100"),
            self.anchor(91, "110"),
        ]
        shuffled = list(anchors)
        random.Random(7).shuffle(shuffled)

        first = ground_pivot_to_resistance_zones(
            pivot=Decimal("105"),
            anchors=anchors,
            session_dates=self.sessions,
            frozen_atr14=Decimal("4"),
        )
        second = ground_pivot_to_resistance_zones(
            pivot=Decimal("105"),
            anchors=shuffled,
            session_dates=reversed(self.sessions),
            frozen_atr14=Decimal("4"),
        )

        self.assertEqual(first.selected_zone, second.selected_zone)
        self.assertEqual(first.selected_zone.low, Decimal("110"))
        self.assertEqual(first.subreason, "outside_resistance_zone_tolerance")

    def test_repeated_recent_higher_zone_is_audit_only(self) -> None:
        anchors = [
            self.anchor(90, "4490"),
            self.anchor(91, "4510"),
            self.anchor(100, "4800"),
            self.anchor(101, "4820"),
        ]

        grounding = ground_pivot_to_resistance_zones(
            pivot=Decimal("4500"),
            anchors=anchors,
            session_dates=self.sessions,
            frozen_atr14=Decimal("100"),
        )

        self.assertTrue(grounding.is_grounded)
        self.assertEqual(grounding.selected_zone.low, Decimal("4490"))
        self.assertEqual(len(grounding.higher_zones), 1)
        self.assertEqual(grounding.next_higher_distance, Decimal("300"))
        self.assertEqual(grounding.next_higher_distance_atr, Decimal("3"))
        self.assertIn("pivot_below_material_overhead_zone", grounding.audit_flags)

    def test_old_unsupported_highs_do_not_ground_random_pivot(self) -> None:
        grounding = ground_pivot_to_resistance_zones(
            pivot=Decimal("4800"),
            anchors=[
                self.anchor(10, "4800", "resistance"),
                self.anchor(20, "4400", "contraction_low"),
            ],
            session_dates=self.sessions,
            frozen_atr14=Decimal("100"),
        )

        self.assertFalse(grounding.is_grounded)
        self.assertEqual(grounding.subreason, "no_eligible_resistance_evidence")
        self.assertEqual(grounding.zones, ())


if __name__ == "__main__":
    unittest.main()
