"""Structural gates, schema-consistency, and provider-taxonomy tests for the
P10 audit reliability machinery (p10_structural_gates_v3 / schema v9)."""

import datetime as dt
import unittest
from decimal import Decimal

from pydantic import ValidationError

from app.domain.p10_geometry import (
    CandleData,
    VcpContractionWave,
    compute_structural_facts,
    evaluate_structural_gates,
    format_candidate_summary,
    structural_facts_to_dict,
)
from app.schemas.proposals import GeminiVcpProposalOutput
from app.services.proposal_generator import (
    PROPOSAL_PROVIDER_RATE_LIMITED,
    PROPOSAL_PROVIDER_TIMEOUT,
    PROPOSAL_PROVIDER_UPSTREAM_ERROR,
    PROPOSAL_SCHEMA_INCONSISTENT,
    ProposalProviderError,
    _classify_provider_failure,
    parse_proposal_openrouter_response,
)

BASE = dt.date(2026, 1, 1)


def synth_candles(
    n: int = 170,
    step: float = 0.15,
    vol: int = 120_000,
    start: float = 100.0,
    overrides: dict[int, tuple[float, float, float, float, int]] | None = None,
) -> list[CandleData]:
    """Monotonic trend candles with optional per-index OHLCV overrides.

    Override keys are candle indices; values are (open, high, low, close,
    volume). Candle dates are consecutive days from BASE.
    """
    overrides = overrides or {}
    candles: list[CandleData] = []
    prev_close = start
    for i in range(n):
        c = prev_close + step
        o = prev_close
        if i in overrides:
            o, h, l, c, v = overrides[i]
        else:
            h = c + 0.5
            l = c - 0.5
            v = vol
        candles.append(
            CandleData(
                open=o,
                high=h,
                low=l,
                close=c,
                volume=v,
                date=(BASE + dt.timedelta(days=i)).isoformat(),
            )
        )
        prev_close = candles[i].close
    return candles


def wave(
    index: int,
    high_idx: int,
    high_price: float,
    low_idx: int,
    low_price: float,
) -> VcpContractionWave:
    return VcpContractionWave(
        index=index,
        high_date=(BASE + dt.timedelta(days=high_idx)).isoformat(),
        high_price=Decimal(str(high_price)),
        low_date=(BASE + dt.timedelta(days=low_idx)).isoformat(),
        low_price=Decimal(str(low_price)),
        depth_pct=Decimal(str(round((high_price - low_price) / high_price * 100, 2))),
    )


def annotate_waves(candles: list[CandleData], waves: list[VcpContractionWave]) -> list[VcpContractionWave]:
    """Attach dates/prices consistent with the candle list (manual fixtures)."""
    return waves


class StructuralGateTest(unittest.TestCase):
    def _facts(self, candles, waves, tick="0.05"):
        return compute_structural_facts(candles, tuple(waves), tick_size=Decimal(tick))

    # ---- HONASA-like: final low undercuts the prior pullback floor ----
    def test_undercut_lower_low_is_invalid(self):
        # Prior pullback floor at index 150 (~111.5), final low at 165 below it.
        candles = synth_candles(
            overrides={
                150: (116.0, 118.0, 111.5, 116.0, 90_000),
                160: (117.5, 119.0, 116.0, 118.0, 70_000),
                165: (116.5, 117.0, 110.0, 113.5, 80_000),
            }
        )
        waves = [
            wave(1, 130, 120.0, 150, 111.5),
            wave(2, 160, 119.0, 165, 110.0),
        ]
        verdict = evaluate_structural_gates(self._facts(candles, waves))
        self.assertEqual(verdict.disposition, "invalid")
        self.assertIn("structural_undercut_lower_low", verdict.codes)

    def test_shallow_noise_undercut_within_tolerance_is_not_invalid(self):
        candles = synth_candles(
            overrides={
                150: (116.0, 118.0, 113.0, 116.0, 90_000),
                165: (115.0, 116.0, 112.95, 114.0, 80_000),
            }
        )
        waves = [
            wave(1, 130, 120.0, 150, 113.0),
            wave(2, 160, 116.0, 165, 112.95),
        ]
        verdict = evaluate_structural_gates(self._facts(candles, waves))
        # 0.05 breach is below the max(tick, 0.10*ATR) tolerance.
        self.assertNotIn("structural_undercut_lower_low", verdict.codes)

    # ---- FLUOROCHEM-like: mature structure but immature base ----
    def test_immature_base_is_forming_not_invalid(self):
        # Freeze ends 11 sessions after the first pullback high (index 155).
        candles = synth_candles(n=167)
        first_high = 155
        waves = [
            wave(1, first_high, 120.0, first_high + 3, 113.0),
            wave(2, first_high + 7, 118.0, first_high + 10, 115.0),
        ]
        facts = self._facts(candles, waves)
        self.assertLess(facts.base_age_sessions, 15)
        verdict = evaluate_structural_gates(facts)
        self.assertEqual(verdict.disposition, "forming")
        self.assertIn("structural_base_immature", verdict.codes)
        # Forming is a watch state, never a permanent rejection code family.
        self.assertNotIn("structural_undercut_lower_low", verdict.codes)

    # ---- NEULANDLAB-like: near-equal depths are a flat shelf ----
    def test_flat_shelf_does_not_tighten(self):
        candles = synth_candles(
            overrides={
                150: (114.0, 115.0, 107.0, 108.0, 80_000),  # C1 low ~107 (5.3% vs ~113 high)
                165: (110.0, 111.0, 105.0, 106.5, 70_000),  # C2 low ~105 (4.9%)
            }
        )
        waves = [
            wave(1, 145, 113.0, 150, 107.0),  # 5.31%
            wave(2, 160, 110.0, 165, 105.0),  # 4.55% -> step 0.76pp, ratio 0.86
        ]
        # 5.31 -> 4.55: ratio 0.86 <= 0.90 so tightening passes; build a
        # genuinely flat case instead: 5.31 -> 5.05 (ratio 0.95, step 0.26pp).
        # Lows must rise (107 -> 107.2) so the flat-shelf rule, not the
        # undercut rule, is what rejects it: 5.31% -> 5.13% (ratio 0.97).
        candles = synth_candles(
            overrides={
                150: (114.0, 115.0, 107.0, 108.0, 80_000),
                165: (110.0, 111.0, 107.2, 108.5, 70_000),
            }
        )
        flat_waves = [
            wave(1, 145, 113.0, 150, 107.0),
            wave(2, 160, 113.0, 165, 107.2),
        ]
        verdict = evaluate_structural_gates(self._facts(candles, flat_waves))
        self.assertEqual(verdict.disposition, "invalid")
        self.assertIn("structural_flat_shelf_not_tightening", verdict.codes)

    # ---- TORNTPHARM-like: mature, tightening, dry final pullback -> ok ----
    def test_healthy_mature_pattern_passes(self):
        n = 200
        candles = synth_candles(
            n=n,
            overrides={
                170: (150.0, 152.0, 139.0, 140.0, 250_000),  # C1 low (8.3%)
                182: (144.0, 146.0, 141.5, 145.0, 130_000),  # C2 low (3.8%)
                190: (150.0, 152.0, 151.0, 152.0, 200_000),  # C3 pivot day
                192: (150.0, 151.0, 147.6, 148.5, 60_000),   # final low (dry pullback)
            },
            step=0.05,
        )
        waves = [
            wave(1, 160, 152.0, 170, 139.0),   # 8.6%
            wave(2, 178, 147.0, 182, 141.5),   # 3.7%
            wave(3, 190, 152.0, 192, 147.6),   # 2.9%
        ]
        facts = self._facts(candles, waves)
        self.assertGreater(facts.base_age_sessions, 15)
        verdict = evaluate_structural_gates(facts)
        self.assertEqual(verdict.disposition, "ok", verdict.details)

    # ---- LALPATHLAB-like: final pullback prints 1.9x ADV -> invalid ----
    def test_final_pullback_distribution_invalidates(self):
        candles = synth_candles(
            n=200,
            step=0.05,
            overrides={
                170: (150.0, 152.0, 139.0, 140.0, 250_000),
                182: (144.0, 146.0, 141.5, 145.0, 130_000),
                190: (150.0, 152.0, 151.0, 152.0, 200_000),
                192: (150.0, 151.0, 147.6, 147.0, 320_000),  # heavy down day
            },
        )
        waves = [
            wave(1, 160, 152.0, 170, 139.0),
            wave(2, 178, 147.0, 182, 141.5),
            wave(3, 190, 152.0, 192, 147.6),
        ]
        verdict = evaluate_structural_gates(self._facts(candles, waves))
        self.assertEqual(verdict.disposition, "invalid")
        self.assertIn("structural_final_pullback_distribution", verdict.codes)

    # ---- climax-fade: elevated pivot volume alone never rejects ----
    def test_healthy_pivot_volume_alone_is_not_a_climax(self):
        candles = synth_candles(
            n=200,
            step=0.05,
            overrides={
                170: (150.0, 152.0, 139.0, 140.0, 250_000),
                182: (144.0, 146.0, 141.5, 145.0, 130_000),
                190: (150.0, 152.0, 151.0, 152.0, 900_000),  # high-volume up day
                191: (151.5, 152.0, 150.0, 151.6, 80_000),   # holds near pivot
                192: (151.6, 152.0, 148.0, 151.7, 60_000),   # quiet, no fade
            },
        )
        waves = [
            wave(1, 160, 152.0, 170, 139.0),
            wave(2, 178, 147.0, 182, 141.5),
            wave(3, 190, 152.0, 192, 148.0),
        ]
        verdict = evaluate_structural_gates(self._facts(candles, waves))
        self.assertNotIn("structural_pivot_climax_fade", verdict.codes)
        self.assertEqual(verdict.disposition, "ok", verdict.details)

    def test_climax_plus_fade_invalidates(self):
        n = 200
        candles = synth_candles(
            n=n,
            step=0.05,
            overrides={
                170: (150.0, 152.0, 139.0, 140.0, 250_000),
                182: (144.0, 146.0, 141.5, 145.0, 130_000),
                190: (149.0, 153.5, 149.0, 149.2, 900_000),  # upper wick + climax
                191: (149.0, 149.5, 144.0, 144.5, 90_000),
                192: (144.0, 145.0, 140.5, 141.0, 70_000),   # fade below pivot close
            },
        )
        waves = [
            wave(1, 160, 152.0, 170, 139.0),
            wave(2, 178, 147.0, 182, 141.5),
            wave(3, 190, 153.5, 192, 140.5),
        ]
        verdict = evaluate_structural_gates(self._facts(candles, waves))
        self.assertIn("structural_pivot_climax_fade", verdict.codes)
        self.assertEqual(verdict.disposition, "invalid")

    # ---- no-lookahead discipline ----
    def test_facts_do_not_use_sessions_after_as_of_or_after_segment(self):
        n = 200
        candles = synth_candles(
            n=n,
            step=0.05,
            overrides={
                170: (150.0, 152.0, 139.0, 140.0, 250_000),
                182: (144.0, 146.0, 141.5, 145.0, 130_000),
                190: (150.0, 152.0, 151.0, 152.0, 200_000),
                192: (150.0, 151.0, 147.6, 151.7, 60_000),
            },
        )
        waves = [
            wave(1, 160, 152.0, 170, 139.0),
            wave(2, 178, 147.0, 182, 141.5),
            wave(3, 190, 152.0, 192, 147.6),
        ]
        # Junk AFTER the final low but inside the freeze (indexes 193-194,
        # same as-of index 194) must not leak into pullback-segment facts or
        # pivot baselines; only later-close fade measures may legitimately
        # see those sessions.
        quiet = candles[:195]
        noisy = synth_candles(
            n=195,
            step=0.05,
            overrides={
                170: (150.0, 152.0, 139.0, 140.0, 250_000),
                182: (144.0, 146.0, 141.5, 145.0, 130_000),
                190: (150.0, 152.0, 151.0, 152.0, 200_000),
                192: (150.0, 151.0, 147.6, 151.7, 60_000),
                193: (151.7, 152.0, 150.0, 151.0, 5_000_000),
                194: (151.0, 151.5, 146.5, 147.0, 6_000_000),
            },
        )
        self.assertEqual(quiet[-1].date, noisy[-1].date)
        facts_quiet = compute_structural_facts(
            quiet, tuple(waves), tick_size=Decimal("0.05")
        )
        facts_noisy = compute_structural_facts(
            noisy, tuple(waves), tick_size=Decimal("0.05")
        )
        for attr in (
            "seg_max_down_vs_adv_ratio",
            "seg_mean_vs_prev_ratio",
            "seg_mean_vs_adv_ratio",
            "segment_days",
        ):
            self.assertEqual(
                getattr(facts_quiet.final_pullback, attr),
                getattr(facts_noisy.final_pullback, attr),
            )
        self.assertEqual(
            facts_quiet.pivot_day.vol_ratio, facts_noisy.pivot_day.vol_ratio
        )
        # And the extreme days ARE visible only where intended (later-close
        # fade window), proving the measure boundaries are deliberate.
        self.assertLess(facts_quiet.pivot_day.fade_atr, Decimal("1"))
        self.assertGreaterEqual(facts_noisy.pivot_day.fade_atr, Decimal("1"))

    def test_candidate_summary_contains_no_absolute_prices(self):
        candles = synth_candles(n=200, step=0.05, overrides={
            170: (150.0, 152.0, 139.0, 140.0, 250_000),
            182: (144.0, 146.0, 141.5, 145.0, 130_000),
            190: (150.0, 152.0, 151.0, 152.0, 200_000),
            192: (150.0, 151.0, 147.6, 148.5, 60_000),
        })
        waves = [
            wave(1, 160, 152.0, 170, 139.0),
            wave(2, 178, 147.0, 182, 141.5),
            wave(3, 190, 152.0, 192, 147.6),
        ]
        facts = compute_structural_facts(candles, tuple(waves), tick_size=Decimal("0.05"))
        text = format_candidate_summary(waves, facts=facts)
        self.assertIn("Deterministic structure", text)
        self.assertIn("Base age:", text)
        self.assertNotIn("₹", text)
        for token in ("152.0", "147.6"):
            self.assertNotIn(token, text)


class SchemaConsistencyTest(unittest.TestCase):
    def _valid_payload(self) -> dict:
        return {
            "classification": "valid",
            "progressive_tightening": "yes",
            "volume_dry_up": "clearly",
            "base_quality": {
                "price_action": "orderly",
                "climax_or_gap_violation": "no",
                "stage2_context": "yes",
            },
            "pattern_type": "vcp",
            "primary_reason": "mature_vcp",
            "candidate_assessments": [
                {"index": 1, "action": "confirm", "note": "C1."},
                {"index": 2, "action": "confirm", "note": "C2."},
            ],
            "extra_windows": [],
            "confidence": 80,
            "red_flags": [],
            "evidence_summary": "Consistent textbook VCP.",
        }

    def test_valid_requires_vcp_pattern_type(self):
        payload = self._valid_payload()
        payload["pattern_type"] = "high_tight_shelf"
        with self.assertRaises(ValidationError):
            GeminiVcpProposalOutput.model_validate(payload)

    def test_valid_requires_mature_vcp_reason(self):
        payload = self._valid_payload()
        payload["primary_reason"] = "immature_base"
        with self.assertRaises(ValidationError):
            GeminiVcpProposalOutput.model_validate(payload)

    def test_valid_requires_tightening_and_stage2(self):
        for key, value in (
            ("progressive_tightening", "no"),
        ):
            payload = self._valid_payload()
            payload[key] = value
            with self.assertRaises(ValidationError):
                GeminiVcpProposalOutput.model_validate(payload)
        payload = self._valid_payload()
        payload["base_quality"]["stage2_context"] = "no"
        with self.assertRaises(ValidationError):
            GeminiVcpProposalOutput.model_validate(payload)

    def test_not_vcp_cannot_claim_mature_vcp(self):
        payload = self._valid_payload()
        payload.update(
            classification="not_vcp",
            pattern_type="flat_base",
            primary_reason="flat_or_high_tight_shelf",
        )
        GeminiVcpProposalOutput.model_validate(payload)  # consistent
        payload["primary_reason"] = "mature_vcp"
        with self.assertRaises(ValidationError):
            GeminiVcpProposalOutput.model_validate(payload)

    def test_not_vcp_cannot_claim_immature_base(self):
        payload = self._valid_payload()
        payload.update(
            classification="not_vcp",
            pattern_type="vcp",
            primary_reason="immature_base",
        )
        # immature bases are forming, never a hard not_vcp
        with self.assertRaises(ValidationError):
            GeminiVcpProposalOutput.model_validate(payload)
        payload["primary_reason"] = "lower_low_or_breakdown"
        GeminiVcpProposalOutput.model_validate(payload)


class ProviderTaxonomyTest(unittest.TestCase):
    def test_classifier(self):
        self.assertEqual(_classify_provider_failure(504, "timeout", "aborted"), PROPOSAL_PROVIDER_TIMEOUT)
        self.assertEqual(_classify_provider_failure(None, "504", "gateway timeout"), PROPOSAL_PROVIDER_TIMEOUT)
        self.assertEqual(_classify_provider_failure(429, None, None), PROPOSAL_PROVIDER_RATE_LIMITED)
        self.assertEqual(_classify_provider_failure(502, None, None), PROPOSAL_PROVIDER_UPSTREAM_ERROR)
        self.assertEqual(_classify_provider_failure(None, "upstream_error", "x"), PROPOSAL_PROVIDER_UPSTREAM_ERROR)

    def _wrap(self, content: dict) -> dict:
        return {
            "id": "x",
            "usage": {},
            "choices": [{"message": {"content": content}}],
        }

    def test_contradictory_valid_payload_is_schema_inconsistent(self):
        payload = SchemaConsistencyTest()._valid_payload()
        payload["climax_or_gap_violation"] = "yes"
        with self.assertRaises(ProposalProviderError) as ctx:
            parse_proposal_openrouter_response(self._wrap(payload))
        self.assertEqual(ctx.exception.error_type, PROPOSAL_SCHEMA_INCONSISTENT)

    def test_forming_without_state_is_schema_inconsistent(self):
        payload = SchemaConsistencyTest()._valid_payload()
        payload.update(
            classification="forming",
            pattern_type="vcp",
            primary_reason="immature_base",
        )
        payload.pop("forming_state", None)
        with self.assertRaises(ProposalProviderError) as ctx:
            parse_proposal_openrouter_response(self._wrap(payload))
        self.assertEqual(ctx.exception.error_type, PROPOSAL_SCHEMA_INCONSISTENT)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Gates v2: real detector on noisy synthetic bases (folding, staircase,
# retention-based climax). These run derive_chart_geometry end to end.
# ---------------------------------------------------------------------------

from app.domain.p10_geometry import (  # noqa: E402
    MAX_GEMINI_CANDIDATES,
    derive_chart_geometry,
    fold_contraction_candidates,
    significant_staircase,
)

PATH_BASE = dt.date(2025, 1, 1)


def path_to_candles(points, *, wick=0.4, vol=100_000, vols=None):
    """Piecewise-linear closes: points = [(start, 0), (target, sessions), ...]."""
    closes = [float(points[0][0])]
    for target, sessions in points[1:]:
        start = closes[-1]
        closes.extend(start + (target - start) * k / sessions for k in range(1, sessions + 1))
    candles, prev = [], closes[0]
    for i, close in enumerate(closes):
        candles.append(
            CandleData(
                open=prev,
                high=max(prev, close) + wick,
                low=min(prev, close) - wick,
                close=close,
                volume=(vols or {}).get(i, vol),
                date=(PATH_BASE + dt.timedelta(days=i)).isoformat(),
            )
        )
        prev = close
    return candles


def real_detector(candles):
    geometry = derive_chart_geometry(candles, tick_size=Decimal("0.05"))
    facts = compute_structural_facts(candles, geometry.contractions, tick_size=Decimal("0.05"))
    return geometry, facts, evaluate_structural_gates(facts)


NOISY_TWO_CONTRACTION = [
    (60, 0), (100, 170), (90, 6), (94, 3), (85, 5), (92, 5), (90.5, 2), (97, 5), (92.5, 5), (95.8, 4)
]
# Pivot (final contraction high) sits at index 170 + 8 + 9 + 1 = 188.
PIVOT_INDEX = 188


class RealDetectorGateTest(unittest.TestCase):
    def test_noisy_two_contraction_vcp_passes(self):
        geometry, facts, verdict = real_detector(path_to_candles(NOISY_TWO_CONTRACTION))
        raw_depths = [w.depth_pct for w in geometry.contractions]
        # Raw zigzag noise is present: an expanding raw step (2.49 -> 5.44).
        self.assertTrue(any(b > a for a, b in zip(raw_depths, raw_depths[1:])))
        self.assertLessEqual(facts.candidate_count, MAX_GEMINI_CANDIDATES)
        self.assertEqual(verdict.disposition, "ok", verdict.codes)
        self.assertEqual(verdict.codes, ())

    def test_high_volume_pivot_then_quiet_pullback_is_not_climax(self):
        points = [(60, 0), (100, 170), (88, 8), (97, 9), (99, 1), (98.2, 1), (96, 3)]
        n = len(path_to_candles(points))
        vols = {PIVOT_INDEX: 300_000, **{i: 60_000 for i in range(PIVOT_INDEX + 1, n)}}
        _, facts, verdict = real_detector(path_to_candles(points, vols=vols))
        self.assertGreater(facts.pivot_day.vol_ratio, Decimal("1.75"))
        # The retired as-of drawdown measure would have fired here.
        self.assertGreaterEqual(facts.pivot_day.fade_atr, Decimal("1.0"))
        self.assertNotIn("structural_pivot_climax_fade", verdict.codes)
        self.assertEqual(verdict.disposition, "ok", verdict.codes)

    def test_climax_reversal_through_pivot_low_is_invalid(self):
        points = [(60, 0), (100, 170), (88, 8), (97, 9), (99, 1), (95, 1), (94.5, 3)]
        _, facts, verdict = real_detector(path_to_candles(points, vols={PIVOT_INDEX: 300_000}))
        self.assertTrue(facts.pivot_day.retention_failed)
        self.assertIn("structural_pivot_climax_fade", verdict.codes)

    def test_retention_ignores_sessions_after_pivot_plus_k(self):
        points = [(60, 0), (100, 170), (88, 8), (97, 9), (99, 1), (98.2, 1), (96, 3)]
        base = path_to_candles(points, vols={PIVOT_INDEX: 300_000})
        changed = list(base)
        # Crash a session two days after the pivot: drawdown changes, the
        # one-session retention verdict and the pivot ADV baseline do not.
        c = changed[PIVOT_INDEX + 2]
        changed[PIVOT_INDEX + 2] = CandleData(
            open=c.open, high=c.high, low=80.0, close=81.0, volume=c.volume, date=c.date
        )
        geometry, a, va = real_detector(base)
        # Same frozen geometry; only a session after pivot+K differs.
        b = compute_structural_facts(changed, geometry.contractions, tick_size=Decimal("0.05"))
        vb = evaluate_structural_gates(b)
        self.assertEqual(a.pivot_day.retention_failed, b.pivot_day.retention_failed)
        self.assertEqual(a.pivot_day.adv20_before_pivot, b.pivot_day.adv20_before_pivot)
        self.assertNotEqual(a.pivot_day.fade_atr, b.pivot_day.fade_atr)
        self.assertNotIn("structural_pivot_climax_fade", vb.codes)

    def test_flat_shelf_with_rising_lows_is_invalid(self):
        points = [(60, 0), (100, 170), (94.5, 4), (99.9, 4), (94.55, 4), (99.85, 4),
                  (94.6, 4), (99.8, 4), (94.65, 4), (97, 3)]
        _, facts, verdict = real_detector(path_to_candles(points))
        self.assertGreaterEqual(facts.candidate_count, 3)
        self.assertEqual(verdict.disposition, "invalid")
        self.assertIn("structural_flat_shelf_not_tightening", verdict.codes)

    def test_folding_cannot_hide_an_undercut(self):
        points = [(60, 0), (100, 170), (85, 6), (95, 5), (91, 3), (93, 3), (84, 4), (88, 3)]
        _, facts, verdict = real_detector(path_to_candles(points))
        self.assertEqual(facts.candidate_count, 1)  # folded into one leg
        self.assertIn("structural_undercut_lower_low", verdict.codes)

    def test_summary_has_statuses_not_drawdown_and_at_most_six_candidates(self):
        _, facts, _ = real_detector(path_to_candles(NOISY_TWO_CONTRACTION))
        summary = format_candidate_summary(facts.candidates, facts=facts)
        self.assertNotIn("faded", summary)
        self.assertIn("Significant contractions (tightening check: passed)", summary)
        self.assertIn("next session held", summary)
        self.assertLessEqual(sum(line.startswith("C") for line in summary.splitlines()), 6)


class FoldAndStaircaseTest(unittest.TestCase):
    ATR = Decimal("2")

    def test_bounce_inside_decline_merges_and_records_members(self):
        folded, members = fold_contraction_candidates(
            [wave(1, 10, 100.0, 15, 90.0), wave(2, 18, 94.0, 22, 85.0)], atr14=self.ATR
        )
        self.assertEqual(len(folded), 1)
        self.assertEqual((folded[0].high_price, folded[0].low_price), (Decimal("100.0"), Decimal("85.0")))
        self.assertEqual(members, ((1, 2),))

    def test_lower_low_within_tolerance_is_not_merged(self):
        folded, _ = fold_contraction_candidates(
            [wave(1, 10, 100.0, 15, 90.0), wave(2, 18, 96.0, 22, 89.9)], atr14=self.ATR
        )
        self.assertEqual(len(folded), 2)

    def test_non_final_noise_dropped_final_noise_kept(self):
        folded, _ = fold_contraction_candidates(
            [wave(1, 10, 100.0, 15, 90.0), wave(2, 18, 95.0, 19, 94.5),
             wave(3, 25, 97.0, 28, 93.0), wave(4, 30, 96.0, 31, 95.5)],
            atr14=self.ATR,
        )
        self.assertEqual([w.index for w in folded], [1, 2, 3])
        self.assertEqual(folded[-1].low_price, Decimal("95.5"))

    def test_cap_merges_oldest_and_input_order_is_irrelevant(self):
        waves = [wave(i, 10 * i, 100.0 - i * 0.1, 10 * i + 4, 95.0 + i * 0.3) for i in range(1, 9)]
        folded, members = fold_contraction_candidates(list(reversed(waves)), atr14=self.ATR)
        self.assertEqual(len(folded), MAX_GEMINI_CANDIDATES)
        self.assertEqual(members[0], (1, 2, 3))

    def test_staircase_skips_superseded_dips_and_checks_expanding_final(self):
        def wv(i, depth):
            return VcpContractionWave(i, f"2025-01-{i:02d}", Decimal("100"), f"2025-01-{i:02d}",
                                      Decimal("100") - Decimal(str(depth)), Decimal(str(depth)))

        self.assertEqual([w.index for w in significant_staircase([wv(1, 15), wv(2, 6), wv(3, 9), wv(4, 4)])], [1, 3, 4])
        self.assertEqual([w.index for w in significant_staircase([wv(1, 8.0), wv(2, 3), wv(3, 8.05)])], [1, 3])


# ---------------------------------------------------------------------------
# Gates v3: advance-dip folding, base context, and the base-position gate.
# ---------------------------------------------------------------------------

# GESHIP-shaped: prior advance into a climax top, a ~30% first leg with a
# heavy-volume down day, a V-shaped recovery whose dips have rising highs,
# then a small wiggle at ~50% of the base range, ~15% under the base high.
V_RECOVERY_LOW_IN_BASE = [
    (110, 0), (112, 57), (108, 15), (150, 50), (145, 6), (179.8, 20),
    (150, 15), (160, 8), (126.1, 25),
    (133, 6), (129, 4), (142, 8), (138, 4), (158.3, 12),
    (146, 8), (153.06, 6), (148, 5), (149.66, 2),
]
V_TOP_INDEX = 148
V_DISTRIBUTION_INDEX = 185


def v_recovery_candles(vols=None):
    return path_to_candles(
        V_RECOVERY_LOW_IN_BASE,
        vols={V_TOP_INDEX: 900_000, V_DISTRIBUTION_INDEX: 1_350_000, **(vols or {})},
    )


class BasePositionGateTest(unittest.TestCase):
    def test_v_recovery_low_in_base_is_forming_not_valid(self):
        _, facts, verdict = real_detector(v_recovery_candles())
        # Rally dips are dropped: C1 (the 30% leg) + two right-side legs.
        self.assertEqual(facts.candidate_count, 3)
        self.assertEqual(verdict.disposition, "forming")
        self.assertEqual(verdict.codes, ("structural_pivot_low_in_base",))
        bc = facts.base_context
        self.assertGreater(bc.base_depth_pct, Decimal("28"))
        self.assertTrue(Decimal("0.45") < bc.pivot_position_in_base < Decimal("0.55"))
        self.assertLess(bc.pivot_vs_base_high_pct, Decimal("-8"))
        top = bc.volume_anomalies[0]
        self.assertEqual(top.date, v_recovery_candles()[V_DISTRIBUTION_INDEX].date)
        self.assertEqual(top.direction, "down")
        self.assertEqual(bc.heavy_down_days, 1)

    def test_summary_scopes_checks_and_carries_base_context_without_prices(self):
        candles = v_recovery_candles()
        _, facts, _ = real_detector(candles)
        summary = format_candidate_summary(facts.candidates, facts=facts)
        self.assertIn("Final pullback only", summary)
        self.assertIn("Final pivot day only", summary)
        self.assertNotIn("(distribution check:", summary)
        self.assertIn("Pivot position (base-position check: FAILED)", summary)
        self.assertIn(candles[V_DISTRIBUTION_INDEX].date, summary)
        self.assertIn("Not checked by Python", summary)
        self.assertIn("SMA50 > SMA150 > SMA200", summary)
        self.assertNotIn("₹", summary)
        for token in ("180.2", "179.8", "153.46", "153.06", "125.7", "126.1"):
            self.assertNotIn(token, summary)
        self.assertIn("base_context", structural_facts_to_dict(facts))

    def test_advance_dip_rule_is_what_drops_the_rally_dips(self):
        candles = v_recovery_candles()
        geometry, facts, _ = real_detector(candles)
        without = compute_structural_facts(
            candles,
            geometry.contractions,
            tick_size=Decimal("0.05"),
            advance_dip_tolerance_atr=None,
        )
        self.assertGreater(without.candidate_count, facts.candidate_count)
        # Low progression is read from raw swings either way.
        self.assertEqual(without.undercut_gap, facts.undercut_gap)

    def test_volume_baseline_uses_only_earlier_sessions(self):
        base = v_recovery_candles()
        later = v_recovery_candles(vols={V_DISTRIBUTION_INDEX + 1: 5_000_000})
        own = v_recovery_candles(vols={V_DISTRIBUTION_INDEX: 2_700_000})

        def ratio(candles):
            geometry = derive_chart_geometry(candles, tick_size=Decimal("0.05"))
            facts = compute_structural_facts(candles, geometry.contractions, tick_size=Decimal("0.05"))
            date = candles[V_DISTRIBUTION_INDEX].date
            return next(a.vol_vs_adv50 for a in facts.base_context.volume_anomalies if a.date == date)

        self.assertEqual(ratio(base), ratio(later))
        self.assertEqual(ratio(own), ratio(base) * 2)

    def test_pivot_near_base_high_passes_base_position(self):
        _, facts, verdict = real_detector(path_to_candles(NOISY_TWO_CONTRACTION))
        self.assertGreaterEqual(facts.base_context.pivot_position_in_base, Decimal("0.67"))
        self.assertNotIn("structural_pivot_low_in_base", verdict.codes)

    def test_shallow_base_never_trips_base_position(self):
        # Low in a shallow base is not overhead supply: both conditions must hold.
        facts = compute_structural_facts(
            synth_candles(n=200, step=0.05, overrides={
                170: (150.0, 152.0, 147.0, 148.0, 120_000),
                182: (149.0, 149.6, 148.2, 149.0, 120_000),
            }),
            (wave(1, 160, 152.0, 170, 147.0), wave(2, 178, 149.6, 182, 148.2)),
            tick_size=Decimal("0.05"),
        )
        self.assertLess(facts.base_context.pivot_position_in_base, Decimal("0.67"))
        self.assertNotIn("structural_pivot_low_in_base", evaluate_structural_gates(facts).codes)


class AdvanceDipFoldTest(unittest.TestCase):
    ATR = Decimal("2")

    def test_rally_dip_dropped_flat_high_kept(self):
        folded, members = fold_contraction_candidates(
            [wave(1, 10, 100.0, 15, 85.0), wave(2, 18, 92.0, 20, 88.0),
             wave(3, 25, 97.0, 28, 90.0), wave(4, 31, 96.0, 33, 93.0)],
            atr14=self.ATR,
        )
        # 92 is cleared by 97 (a dip inside that rally); 97 vs 96 is a lower high.
        self.assertEqual(members, ((1,), (3,), (4,)))
        flat, _ = fold_contraction_candidates(
            [wave(1, 10, 100.0, 15, 90.0), wave(2, 18, 100.8, 20, 94.0)],
            atr14=self.ATR,
        )
        self.assertEqual(len(flat), 2)  # within 0.5 x ATR: flat highs stay separate
