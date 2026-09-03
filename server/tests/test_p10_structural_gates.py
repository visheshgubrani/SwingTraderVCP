"""Structural gates, schema-consistency, and provider-taxonomy tests for the
P10 audit reliability v2 machinery (p10_structural_gates_v1 / schema v7)."""

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
        flat_waves = [
            wave(1, 145, 113.0, 150, 107.0),
            wave(2, 160, 111.0, 165, 105.4),  # 5.05%
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
