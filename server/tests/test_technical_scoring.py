import unittest

import numpy as np
import pandas as pd
from pydantic import ValidationError

from app.services.screening_config import (
    SAAS_MINERVINI_STANDARD_CONFIG,
    TechnicalScreeningConfig,
    merge_template_config,
)
from app.services.technical_scoring import (
    evaluate_technical_setup,
    linear_score,
    pocket_pivot_points,
    signed_linear_score,
)


def scoring_frame(**overrides: float) -> pd.DataFrame:
    values = {
        "close": 200.0,
        "sma_50": 180.0,
        "sma_150": 160.0,
        "sma_200": 150.0,
        "sma_200_prev_22": 145.0,
        "high_52w": 220.0,
        "low_52w": 140.0,
        "adtv_crore": 20.0,
        "atr_ratio": 1.0,
        "atr_ratio_3m_low": 1.0,
        "bb_width": 0.05,
        "bb_width_percentile": 0.10,
        "volume_dry_up_ratio": 0.60,
        "up_down_volume_ratio": 1.50,
        "rs_line_pct_off_high": 1.0,
        "pocket_pivot_age": 0.0,
    }
    values.update(overrides)
    return pd.DataFrame([values])


class TechnicalScoringV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = TechnicalScreeningConfig.for_version("vcp_score_v2")

    def evaluate(self, frame: pd.DataFrame, rs_rating: int = 90):
        return evaluate_technical_setup(
            frame,
            rs_rating=rs_rating,
            history_days=252,
            config=self.config,
        )

    def test_perfect_setup_scores_100(self) -> None:
        result = self.evaluate(scoring_frame())

        self.assertTrue(result["eligible"])
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["grade"], "A")
        self.assertAlmostEqual(
            sum(item["points"] for item in result["components"].values()),
            100.0,
        )
        self.assertIn("atr_contraction", result["components"])
        self.assertIn("bollinger_contraction", result["components"])

    def test_four_of_five_core_checks_is_eligible(self) -> None:
        result = self.evaluate(scoring_frame(close=179.0, high_52w=200.0))

        self.assertTrue(result["eligible"])
        self.assertEqual(result["raw_inputs"]["core_checks_passed"], 4)
        self.assertFalse(result["core_checks"]["price_above_50_sma"])

    def test_three_of_five_core_checks_is_not_eligible(self) -> None:
        result = self.evaluate(
            scoring_frame(
                close=179.0,
                sma_50=155.0,
                sma_150=149.0,
                sma_200_prev_22=151.0,
                high_52w=200.0,
            )
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(result["raw_inputs"]["core_checks_passed"], 3)
        self.assertFalse(result["eligibility"]["stage2_core_checks"])

    def test_close_above_200_is_mandatory(self) -> None:
        result = self.evaluate(scoring_frame(close=149.0, high_52w=170.0))

        self.assertFalse(result["eligible"])
        self.assertFalse(result["eligibility"]["close_above_200_sma"])

    def test_liquidity_history_and_high_guardrails(self) -> None:
        illiquid = self.evaluate(scoring_frame(adtv_crore=10.0))
        too_far = self.evaluate(scoring_frame(close=160.0, high_52w=220.0))
        short_history = evaluate_technical_setup(
            scoring_frame(),
            rs_rating=90,
            history_days=251,
            config=self.config,
        )

        self.assertFalse(illiquid["eligibility"]["adtv_above_minimum"])
        self.assertFalse(too_far["eligibility"]["within_52w_high_guardrail"])
        self.assertFalse(short_history["eligibility"]["minimum_history"])

    def test_invalid_inputs_are_rejected_without_a_score(self) -> None:
        result = self.evaluate(scoring_frame(bb_width_percentile=np.nan))

        self.assertFalse(result["eligible"])
        self.assertIsNone(result["score"])
        self.assertFalse(result["eligibility"]["valid_indicator_inputs"])

    def test_rs_near_miss_is_continuous(self) -> None:
        score_69 = linear_score(
            69,
            full_at=self.config.rs_score_full,
            zero_at=self.config.rs_score_zero,
            points=self.config.rs_weight,
            lower_is_better=False,
        )
        score_70 = linear_score(
            70,
            full_at=self.config.rs_score_full,
            zero_at=self.config.rs_score_zero,
            points=self.config.rs_weight,
            lower_is_better=False,
        )

        self.assertEqual(score_69, 9.5)
        self.assertEqual(score_70, 10.0)

    def test_atr_near_miss_is_continuous(self) -> None:
        score_109 = linear_score(
            1.09,
            full_at=self.config.atr_proximity_full,
            zero_at=self.config.atr_proximity_zero,
            points=self.config.atr_contraction_weight,
        )
        score_111 = linear_score(
            1.11,
            full_at=self.config.atr_proximity_full,
            zero_at=self.config.atr_proximity_zero,
            points=self.config.atr_contraction_weight,
        )

        self.assertAlmostEqual(score_109, 11.625)
        self.assertAlmostEqual(score_111, 10.875)

    def test_bollinger_near_miss_is_continuous(self) -> None:
        score_19 = linear_score(
            0.19,
            full_at=self.config.bb_percentile_full,
            zero_at=self.config.bb_percentile_zero,
            points=self.config.bb_contraction_weight,
        )
        score_21 = linear_score(
            0.21,
            full_at=self.config.bb_percentile_full,
            zero_at=self.config.bb_percentile_zero,
            points=self.config.bb_contraction_weight,
        )

        self.assertAlmostEqual(score_19, 12.3)
        self.assertAlmostEqual(score_21, 11.7)

    def test_volume_near_miss_is_continuous(self) -> None:
        score_079 = linear_score(
            0.79,
            full_at=self.config.volume_ratio_full,
            zero_at=self.config.volume_ratio_zero,
            points=self.config.volume_dry_up_weight,
        )
        score_081 = linear_score(
            0.81,
            full_at=self.config.volume_ratio_full,
            zero_at=self.config.volume_ratio_zero,
            points=self.config.volume_dry_up_weight,
        )

        self.assertAlmostEqual(score_079, 6.833333333333333)
        self.assertAlmostEqual(score_081, 6.5)

    def test_curve_endpoints_are_clamped(self) -> None:
        self.assertEqual(
            linear_score(0.5, full_at=1.0, zero_at=1.4, points=15),
            15,
        )
        self.assertEqual(
            linear_score(2.0, full_at=1.0, zero_at=1.4, points=15),
            0,
        )

    def test_v2_policy_is_fully_snapshotted_and_validated(self) -> None:
        payload = self.config.model_dump()

        self.assertEqual(payload["pipeline_version"], "vcp_score_v2")
        self.assertEqual(payload["shortlist_limit"], 500)
        self.assertEqual(payload["fundamental_limit"], 20)
        self.assertIn("bb_percentile_zero", payload)
        with self.assertRaises(ValidationError):
            TechnicalScreeningConfig(rs_weight=21)

    def test_optional_paid_scanner_gates_are_opt_in(self) -> None:
        baseline = self.evaluate(scoring_frame(), rs_rating=70)
        self.assertTrue(baseline["eligible"])
        self.assertTrue(baseline["eligibility"]["rs_above_minimum"])

        strict = self.config.model_copy(
            update={
                "min_rs_rating": 80,
                "max_atr_proximity_factor": 1.0,
                "max_bb_width_percentile": 0.2,
                "max_volume_dry_up_ratio": 0.7,
                "minimum_technical_score": 80,
            }
        )
        rejected = evaluate_technical_setup(
            scoring_frame(atr_ratio=1.1),
            rs_rating=70,
            history_days=252,
            config=strict,
        )

        self.assertFalse(rejected["eligible"])
        self.assertFalse(rejected["eligibility"]["rs_above_minimum"])
        self.assertFalse(rejected["eligibility"]["atr_contraction_within_limit"])
        self.assertTrue(rejected["eligibility"]["bollinger_contraction_within_limit"])
        self.assertTrue(rejected["eligibility"]["volume_dry_up_within_limit"])
        self.assertTrue(rejected["eligibility"]["technical_score_above_minimum"])


class TechnicalScoringV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = TechnicalScreeningConfig.for_version("vcp_score_v3")

    def evaluate(self, frame: pd.DataFrame, rs_rating: int = 90):
        return evaluate_technical_setup(
            frame,
            rs_rating=rs_rating,
            history_days=252,
            config=self.config,
        )

    def test_v3_preset_remains_reproducible(self) -> None:
        default = TechnicalScreeningConfig()

        self.assertEqual(default.pipeline_version, "vcp_score_v3")
        self.assertEqual(self.config.pipeline_version, "vcp_score_v3")
        self.assertEqual(self.config.stage2_weight, 20.0)
        self.assertEqual(self.config.contraction_weight, 20.0)

    def test_saas_v2_template_remains_reproducible(self) -> None:
        self.assertEqual(
            SAAS_MINERVINI_STANDARD_CONFIG.pipeline_version,
            "vcp_score_v2",
        )

        merged = merge_template_config({"pipeline_version": "vcp_score_v2"})

        self.assertEqual(merged.pipeline_version, "vcp_score_v2")
        self.assertEqual(merged.stage2_weight, 25.0)
        self.assertEqual(merged.contraction_weight, 0.0)
        with self.assertRaises(ValueError):
            merge_template_config({"pipeline_version": "vcp_score_v4"})

    def test_perfect_setup_scores_100(self) -> None:
        result = self.evaluate(scoring_frame())

        self.assertTrue(result["eligible"])
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["grade"], "A")
        self.assertIn("volatility_contraction", result["components"])
        self.assertIn("rs_line_high", result["components"])
        self.assertIn("up_down_volume", result["components"])
        self.assertIn("pocket_pivot", result["components"])
        self.assertNotIn("atr_contraction", result["components"])

    def test_contraction_averages_atr_and_bb_units(self) -> None:
        # ATR at midpoint of [1.0, 1.4] => 0.5 unit; BB perfect => 1.0
        # average 0.75 * 20 = 15
        result = self.evaluate(scoring_frame(atr_ratio=1.2, atr_ratio_3m_low=1.0))
        contraction = result["components"]["volatility_contraction"]

        self.assertAlmostEqual(contraction["raw_value"]["atr_unit"], 0.5)
        self.assertAlmostEqual(contraction["raw_value"]["bb_unit"], 1.0)
        self.assertAlmostEqual(contraction["points"], 15.0)
        self.assertEqual(contraction["max_points"], 20.0)

    def test_up_down_volume_can_go_negative(self) -> None:
        at_zero = signed_linear_score(
            0.8,
            full_at=1.5,
            zero_at=0.8,
            points=10,
            negative_floor=-2,
        )
        below = signed_linear_score(
            0.66,
            full_at=1.5,
            zero_at=0.8,
            points=10,
            negative_floor=-2,
        )
        floored = signed_linear_score(
            0.1,
            full_at=1.5,
            zero_at=0.8,
            points=10,
            negative_floor=-2,
        )

        self.assertEqual(at_zero, 0.0)
        self.assertAlmostEqual(below, -2.0, places=5)
        self.assertEqual(floored, -2.0)

        result = self.evaluate(scoring_frame(up_down_volume_ratio=0.5))
        self.assertEqual(result["components"]["up_down_volume"]["points"], -2.0)
        self.assertGreaterEqual(result["score"], 0.0)

    def test_pocket_pivot_recency_curve(self) -> None:
        self.assertEqual(pocket_pivot_points(0, self.config), 5.0)
        self.assertEqual(pocket_pivot_points(1, self.config), 5.0)
        self.assertEqual(pocket_pivot_points(7, self.config), 0.0)
        self.assertEqual(pocket_pivot_points(None, self.config), 0.0)
        mid = pocket_pivot_points(4, self.config)
        self.assertAlmostEqual(mid, 2.5)

    def test_rs_line_proximity_ramp(self) -> None:
        full = self.evaluate(scoring_frame(rs_line_pct_off_high=2.0))
        zero = self.evaluate(scoring_frame(rs_line_pct_off_high=12.0))
        mid = self.evaluate(scoring_frame(rs_line_pct_off_high=7.0))

        self.assertEqual(full["components"]["rs_line_high"]["points"], 10.0)
        self.assertEqual(zero["components"]["rs_line_high"]["points"], 0.0)
        self.assertAlmostEqual(mid["components"]["rs_line_high"]["points"], 5.0)

    def test_v3_weights_total_100(self) -> None:
        payload = self.config.model_dump()
        self.assertEqual(payload["pipeline_version"], "vcp_score_v3")
        self.assertEqual(payload["stage2_weight"], 20.0)
        self.assertEqual(payload["contraction_weight"], 20.0)
        with self.assertRaises(ValidationError):
            TechnicalScreeningConfig(
                pipeline_version="vcp_score_v3",
                stage2_weight=20.0,
                stage2_core_check_points=3.0,
                stage2_52w_low_points=5.0,
                rs_weight=20.0,
                rs_line_high_weight=10.0,
                high_proximity_weight=10.0,
                atr_contraction_weight=0.0,
                bb_contraction_weight=0.0,
                contraction_weight=20.0,
                volume_dry_up_weight=10.0,
                up_down_volume_weight=10.0,
                pocket_pivot_weight=5.0,
            )


if __name__ == "__main__":
    unittest.main()
