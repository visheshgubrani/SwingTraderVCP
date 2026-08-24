import datetime as dt
import random
import unittest
from decimal import Decimal

from app.domain.p10_geometry import (
    CandleData,
    ValidatedPatternAnchor,
    VcpContractionWave,
    adjust_chase_ceiling_for_t1_rr,
    assign_frozen_targets,
    build_resistance_zones,
    compute_atr14,
    construct_and_validate_proposal,
    construct_python_owned_levels,
    depths_non_increasing,
    entry_vwap_invalidates_t1_rr,
    ground_pivot_to_resistance_zones,
    resolve_surviving_contractions,
    snap_to_tick,
    calculate_structural_stop,
    calculate_chase_ceiling,
    SurvivingContraction,
    validate_proposal_targets,
)
from app.schemas.proposals import GeminiCandidateAssessment, GeminiExtraWindow


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

    def test_validate_proposal_targets_rejects_t1_below_one_r(self):
        pivot = Decimal("500.00")
        stop = Decimal("475.00")
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

    def test_adjust_chase_ceiling_shrinks_to_preserve_one_r(self):
        pivot = Decimal("100.00")
        stop = Decimal("94.00")
        t1 = Decimal("108.00")
        base_ceiling = Decimal("102.00")
        final = adjust_chase_ceiling_for_t1_rr(pivot, stop, t1, base_ceiling)
        self.assertEqual(final, Decimal("101.00"))

    def test_construct_shrinks_chase_when_t1_is_one_r_at_pivot_not_base(self):
        # stop = 98 - 0.25*16 = 94; base ceiling = 100 + min(2, 3) = 102
        geom = construct_and_validate_proposal(
            pivot_price=Decimal("100.00"),
            final_contraction_low=Decimal("98.00"),
            t1=Decimal("108.00"),
            t2=Decimal("113.20"),
            t3=Decimal("119.40"),
            atr14=Decimal("16.00"),
        )
        self.assertTrue(geom.is_valid, geom.rejection_reason)
        self.assertEqual(geom.base_chase_ceiling, Decimal("102.00"))
        self.assertEqual(geom.chase_ceiling, Decimal("101.00"))
        self.assertEqual(geom.rr_adjusted_chase_ceiling, Decimal("101.00"))
        self.assertEqual(geom.t1_r, Decimal("1"))
        self.assertTrue(geom.t2_below_2r)
        self.assertTrue(geom.t3_below_3r)
        self.assertLess(geom.t2_r, Decimal("2"))
        self.assertLess(geom.t3_r, Decimal("3"))

    def test_construct_rejects_when_t1_fails_one_r_even_at_pivot(self):
        geom = construct_and_validate_proposal(
            pivot_price=Decimal("100.00"),
            final_contraction_low=Decimal("98.00"),
            t1=Decimal("105.00"),
            t2=Decimal("110.00"),
            t3=Decimal("115.00"),
            atr14=Decimal("16.00"),
        )
        self.assertFalse(geom.is_valid)
        self.assertIn("requires >= 1.0R", geom.rejection_reason)
        self.assertEqual(geom.chase_ceiling, Decimal("100.00"))
        self.assertLess(geom.r_at_pivot, Decimal("1"))

    def test_construct_accepts_t2_t3_below_hard_rr_with_flags(self):
        geom = construct_and_validate_proposal(
            pivot_price=Decimal("100.00"),
            final_contraction_low=Decimal("98.00"),
            t1=Decimal("108.00"),
            t2=Decimal("113.20"),
            t3=Decimal("119.40"),
            atr14=Decimal("16.00"),
        )
        self.assertTrue(geom.is_valid, geom.rejection_reason)
        self.assertEqual(geom.t1_r, Decimal("1"))
        self.assertAlmostEqual(float(geom.t2_r), 1.74, places=2)
        self.assertAlmostEqual(float(geom.t3_r), 2.63, places=2)
        self.assertTrue(geom.t2_below_2r)
        self.assertTrue(geom.t3_below_3r)

    def test_construct_rejects_unordered_targets(self):
        geom = construct_and_validate_proposal(
            pivot_price=Decimal("100.00"),
            final_contraction_low=Decimal("98.00"),
            t1=Decimal("108.00"),
            t2=Decimal("107.00"),
            t3=Decimal("119.40"),
            atr14=Decimal("16.00"),
        )
        self.assertFalse(geom.is_valid)
        self.assertIn("strictly ordered", geom.rejection_reason)

    def test_construct_allows_chase_equal_to_pivot_when_one_r_forbids_chase(self):
        # T1 = 2*pivot - stop = 106 is exactly 1R at pivot.
        geom = construct_and_validate_proposal(
            pivot_price=Decimal("100.00"),
            final_contraction_low=Decimal("98.00"),
            t1=Decimal("106.00"),
            t2=Decimal("110.00"),
            t3=Decimal("114.00"),
            atr14=Decimal("16.00"),
        )
        self.assertTrue(geom.is_valid, geom.rejection_reason)
        self.assertEqual(geom.chase_ceiling, Decimal("100.00"))
        self.assertEqual(geom.base_chase_ceiling, Decimal("102.00"))
        self.assertEqual(geom.t1_r, Decimal("1"))

    def test_entry_vwap_invalidates_only_t1_one_r(self):
        t1 = Decimal("108.00")
        stop = Decimal("94.00")
        self.assertTrue(
            entry_vwap_invalidates_t1_rr(t1, Decimal("102.00"), stop)
        )
        self.assertFalse(
            entry_vwap_invalidates_t1_rr(t1, Decimal("101.00"), stop)
        )
        self.assertFalse(
            entry_vwap_invalidates_t1_rr(t1, Decimal("94.00"), stop)
        )


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


    def test_detect_vcp_swings_and_contractions_acmesolar(self) -> None:
        raw_data = [
            ("2026-07-01", 380.9, 396.25, 378.25, 390.8, 3091827),
            ("2026-07-02", 392.3, 395.0, 379.3, 393.25, 1452755),
            ("2026-07-03", 395.0, 398.15, 380.0, 383.25, 1207098),
            ("2026-07-06", 384.0, 388.9, 379.0, 383.7, 987442),
            ("2026-07-07", 384.0, 386.6, 364.7, 367.15, 1505840),
            ("2026-07-08", 365.0, 374.1, 361.5, 365.25, 1121342),
            ("2026-07-09", 366.0, 372.0, 365.55, 367.8, 1075278),
            ("2026-07-10", 370.0, 379.45, 367.75, 377.0, 1485585),
            ("2026-07-13", 375.0, 384.4, 365.55, 378.8, 1374154),
            ("2026-07-14", 376.95, 398.65, 373.05, 384.6, 4243979),
            ("2026-07-15", 387.4, 393.4, 383.5, 392.15, 1967707),
            ("2026-07-16", 396.0, 397.5, 387.1, 394.0, 1611990),
            ("2026-07-17", 397.0, 397.5, 378.15, 385.25, 3080054),
            ("2026-07-20", 383.0, 393.9, 375.25, 377.55, 1381083),
            ("2026-07-21", 378.95, 381.75, 371.15, 373.1, 752502),
            ("2026-07-22", 373.1, 376.25, 363.6, 369.6, 678714),
            ("2026-07-23", 367.3, 371.05, 360.1, 363.05, 2293479),
            ("2026-07-24", 361.4, 374.9, 361.4, 365.85, 2104262),
            ("2026-07-27", 370.0, 371.0, 358.4, 362.4, 2262264),
            ("2026-07-28", 364.0, 367.6, 344.7, 355.5, 2219580),
            ("2026-07-29", 358.0, 368.7, 350.0, 366.65, 1880822),
            ("2026-07-30", 361.0, 366.8, 347.8, 360.7, 5921663),
            ("2026-07-31", 363.95, 367.5, 359.1, 362.6, 1446698),
            ("2026-08-03", 364.7, 376.35, 362.6, 371.1, 3058650),
            ("2026-08-04", 370.1, 386.4, 369.25, 381.65, 2929270),
            ("2026-08-05", 384.8, 391.05, 378.0, 382.25, 2758030),
            ("2026-08-06", 380.7, 380.7, 373.0, 375.45, 1423424),
            ("2026-08-07", 372.0, 380.95, 362.75, 368.6, 1278670),
            ("2026-08-10", 369.0, 369.6, 362.75, 365.8, 564448),
            ("2026-08-11", 364.0, 370.7, 362.95, 368.45, 667352),
            ("2026-08-12", 369.0, 375.0, 367.5, 372.6, 647813),
            ("2026-08-13", 374.8, 377.1, 369.2, 374.5, 680001),
            ("2026-08-14", 374.55, 379.0, 372.15, 374.1, 440108),
            ("2026-08-17", 373.55, 373.55, 363.8, 364.9, 1019177),
            ("2026-08-18", 363.7, 372.95, 361.8, 371.75, 609731),
        ]
        needed = 126 - len(raw_data)
        first_date = dt.date.fromisoformat(raw_data[0][0])
        first_bar = raw_data[0]
        prefix = []
        cursor = first_date - dt.timedelta(days=1)
        while len(prefix) < needed:
            if cursor.weekday() < 5:
                prefix.append(
                    (cursor.isoformat(), first_bar[1], first_bar[2], first_bar[3], first_bar[4], first_bar[5])
                )
            cursor -= dt.timedelta(days=1)
        prefix.reverse()
        candles = [
            CandleData(open=o, high=h, low=l, close=c, volume=v, date=d)
            for d, o, h, l, c, v in prefix + raw_data
        ]
        from app.domain.p10_geometry import derive_chart_geometry

        geom = derive_chart_geometry(candles)
        self.assertEqual(len(geom.contractions), 3)
        self.assertEqual(geom.contractions[0].high_price, Decimal("398.65"))
        self.assertEqual(geom.contractions[0].low_price, Decimal("344.70"))
        self.assertEqual(geom.contractions[1].high_price, Decimal("391.05"))
        self.assertEqual(geom.contractions[1].low_price, Decimal("362.75"))
        self.assertEqual(geom.contractions[2].high_price, Decimal("379.00"))
        self.assertEqual(geom.contractions[2].low_price, Decimal("361.80"))
        self.assertEqual(geom.cheat_pivot, Decimal("379.00"))
        self.assertEqual(geom.resistance, Decimal("398.65"))


def _wave(
    index: int,
    high_date: str,
    high: str,
    low_date: str,
    low: str,
    vol20: str = "1.00",
) -> VcpContractionWave:
    high_p = Decimal(high)
    low_p = Decimal(low)
    return VcpContractionWave(
        index=index,
        high_date=high_date,
        high_price=high_p,
        low_date=low_date,
        low_price=low_p,
        depth_pct=((high_p - low_p) / high_p) * Decimal("100"),
        vol_adv20=Decimal(vol20),
        vol_adv50=Decimal("1.00"),
    )


def _assess(*rows: tuple) -> list[GeminiCandidateAssessment]:
    out: list[GeminiCandidateAssessment] = []
    for row in rows:
        index, action = row[0], row[1]
        merge = row[2] if len(row) > 2 else None
        out.append(
            GeminiCandidateAssessment(
                index=index,
                action=action,
                merge_with_index=merge,
                note=f"{action} {index}",
            )
        )
    return out


class TestSurvivorResolution(unittest.TestCase):
    def setUp(self) -> None:
        start = dt.date(2026, 1, 5)
        self.candles = [
            CandleData(
                open=100 + i * 0.1,
                high=101 + i * 0.1,
                low=99 + i * 0.1,
                close=100.5 + i * 0.1,
                volume=100000,
                date=(start + dt.timedelta(days=i)).isoformat(),
            )
            for i in range(252)
        ]
        self.c1 = _wave(1, self.candles[-80].date, "120.00", self.candles[-70].date, "100.00", "1.20")
        self.c2 = _wave(2, self.candles[-40].date, "118.00", self.candles[-30].date, "108.00", "0.90")
        self.c3 = _wave(3, self.candles[-15].date, "124.00", self.candles[-8].date, "116.00", "0.60")

    def test_merge_group_counts_as_one_survivor(self) -> None:
        resolution = resolve_surviving_contractions(
            candidates=(self.c1, self.c2, self.c3),
            assessments=_assess((1, "confirm"), (2, "merge", 3), (3, "confirm")),
            extra_windows=(),
            candles=self.candles,
        )
        self.assertTrue(resolution.is_valid, resolution.rejection_reason)
        self.assertEqual(resolution.python_count, 3)
        self.assertEqual(resolution.llm_count, 2)
        sources = {item.source for item in resolution.survivors}
        self.assertIn("merge", sources)
        self.assertIn("confirm", sources)

    def test_reject_last_candidate_shifts_final_pivot_window(self) -> None:
        resolution = resolve_surviving_contractions(
            candidates=(self.c1, self.c2, self.c3),
            assessments=_assess((1, "confirm"), (2, "confirm"), (3, "reject")),
            extra_windows=(),
            candles=self.candles,
        )
        self.assertTrue(resolution.is_valid, resolution.rejection_reason)
        self.assertEqual(resolution.llm_count, 2)
        final = resolution.final_contraction
        self.assertIsNotNone(final)
        self.assertEqual(final.high_price, Decimal("118.00"))
        self.assertNotEqual(final.high_price, self.c3.high_price)

    def test_merge_into_reject_is_invalid(self) -> None:
        resolution = resolve_surviving_contractions(
            candidates=(self.c1, self.c2),
            assessments=_assess((1, "merge", 2), (2, "reject")),
            extra_windows=(),
            candles=self.candles,
        )
        self.assertFalse(resolution.is_valid)
        self.assertIn("reject", resolution.rejection_reason)

    def test_extra_window_can_satisfy_two_survivor_gate(self) -> None:
        high_candle = self.candles[-40]
        low_candle = self.candles[-39]
        extra = GeminiExtraWindow(
            high_start=dt.date.fromisoformat(high_candle.date),
            high_end=dt.date.fromisoformat(high_candle.date),
            low_start=dt.date.fromisoformat(low_candle.date),
            low_end=dt.date.fromisoformat(low_candle.date),
            note="Missed second pullback in the mid window.",
        )
        resolution = resolve_surviving_contractions(
            candidates=(self.c1,),
            assessments=_assess((1, "confirm")),
            extra_windows=(extra,),
            candles=self.candles,
        )
        self.assertTrue(resolution.is_valid, resolution.rejection_reason)
        self.assertEqual(resolution.python_count, 1)
        self.assertEqual(resolution.llm_count, 2)
        self.assertEqual(resolution.survivors[-1].source, "extra")

    def test_depth_tolerance_allows_half_point_regression(self) -> None:
        first = SurvivingContraction(
            index=1,
            high_date="2026-06-01",
            high_price=Decimal("100"),
            low_date="2026-06-10",
            low_price=Decimal("90"),
            depth_pct=Decimal("10.0"),
            vol_adv20=Decimal("1.2"),
            vol_adv50=Decimal("1.0"),
            source="confirm",
            member_indices=(1,),
        )
        second = SurvivingContraction(
            index=2,
            high_date="2026-07-01",
            high_price=Decimal("100"),
            low_date="2026-07-08",
            low_price=Decimal("89.60"),
            depth_pct=Decimal("10.4"),
            vol_adv20=Decimal("0.8"),
            vol_adv50=Decimal("0.9"),
            source="confirm",
            member_indices=(2,),
        )
        self.assertTrue(depths_non_increasing((first, second)))
        too_deep = SurvivingContraction(
            index=2,
            high_date="2026-07-01",
            high_price=Decimal("100"),
            low_date="2026-07-08",
            low_price=Decimal("89.40"),
            depth_pct=Decimal("10.6"),
            vol_adv20=Decimal("0.8"),
            vol_adv50=Decimal("0.9"),
            source="confirm",
            member_indices=(2,),
        )
        self.assertFalse(depths_non_increasing((first, too_deep)))


class TestPythonOwnedLevels(unittest.TestCase):
    def test_targets_lock_at_planned_entry_not_chase(self) -> None:
        planned_entry = Decimal("100.20")
        stop = Decimal("94.00")
        assigned = assign_frozen_targets(
            planned_entry=planned_entry,
            initial_stop=stop,
            pivot=Decimal("100.00"),
            base_high=Decimal("120.00"),
            deepest_low=Decimal("90.00"),
        )
        self.assertIsNotNone(assigned)
        t1, t2, t3, slots = assigned
        self.assertEqual(slots[0], "floor")
        self.assertLess(t1, t2)
        self.assertLess(t2, t3)
        risk = planned_entry - stop
        self.assertGreaterEqual(t1, planned_entry + (Decimal("2") * risk) - Decimal("0.05"))

    def test_construct_python_owned_levels_from_last_survivor(self) -> None:
        start = dt.date(2026, 1, 5)
        candles = [
            CandleData(
                open=100,
                high=101,
                low=99,
                close=100.5,
                volume=100000,
                date=(start + dt.timedelta(days=i)).isoformat(),
            )
            for i in range(252)
        ]
        candles[-80] = CandleData(100, 120, 99, 110, 100000, candles[-80].date)
        candles[-70] = CandleData(100, 101, 100, 100.5, 80000, candles[-70].date)
        candles[-20] = CandleData(110, 124, 110, 120, 90000, candles[-20].date)
        candles[-10] = CandleData(118, 119, 116, 117, 50000, candles[-10].date)
        survivors = (
            SurvivingContraction(
                index=1,
                high_date=candles[-80].date,
                high_price=Decimal("120.00"),
                low_date=candles[-70].date,
                low_price=Decimal("100.00"),
                depth_pct=Decimal("16.67"),
                vol_adv20=Decimal("1.20"),
                vol_adv50=Decimal("1.10"),
                source="confirm",
                member_indices=(1,),
            ),
            SurvivingContraction(
                index=2,
                high_date=candles[-20].date,
                high_price=Decimal("124.00"),
                low_date=candles[-10].date,
                low_price=Decimal("116.00"),
                depth_pct=Decimal("6.45"),
                vol_adv20=Decimal("0.70"),
                vol_adv50=Decimal("0.80"),
                source="confirm",
                member_indices=(2,),
            ),
        )
        atr14 = compute_atr14(candles)
        geom = construct_python_owned_levels(
            survivors=survivors,
            candles=candles,
            atr14=atr14,
        )
        self.assertTrue(geom.is_valid, geom.rejection_reason)
        self.assertEqual(geom.pivot_price, Decimal("124.00"))
        self.assertIsNotNone(geom.planned_entry)
        self.assertGreater(geom.planned_entry, geom.pivot_price)
        self.assertEqual(geom.chase_ceiling, calculate_chase_ceiling(geom.planned_entry, geom.initial_stop)[0])
        self.assertEqual(len(geom.target_slots), 3)
        self.assertLess(geom.t1, geom.t2)
        self.assertLess(geom.t2, geom.t3)


if __name__ == "__main__":
    unittest.main()

