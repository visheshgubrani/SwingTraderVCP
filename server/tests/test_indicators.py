import unittest

import numpy as np
import pandas as pd

from app.services.indicators import (
    compute_relative_strength_ratings,
    compute_technical_indicators,
    evaluate_minervini_criteria,
    evaluate_vcp_shortlist_criteria,
)
from app.services.screening_config import TechnicalScreeningConfig
from app.services.screening_ranker import (
    fundamental_selection_status,
    rank_and_cap_shortlist,
)


def make_tightening_stock() -> pd.DataFrame:
    trending_days = 300
    base_days = 20
    trending_close = np.linspace(100.0, 190.0, trending_days)
    base_close = 190.0 + np.sin(np.linspace(0, 2 * np.pi, base_days)) * 0.05
    close = np.concatenate([trending_close, base_close])

    spread = np.concatenate(
        [np.full(trending_days, 2.0), np.full(base_days, 0.10)]
    )
    volume = np.concatenate(
        [np.full(trending_days + 10, 1_000_000), np.full(10, 400_000)]
    )
    return pd.DataFrame(
        {
            "high": close + spread / 2,
            "low": close - spread / 2,
            "close": close,
            "volume": volume,
        }
    )


class IndicatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = TechnicalScreeningConfig()
        self.indicators = compute_technical_indicators(
            make_tightening_stock(),
            self.config,
        )

    def test_tight_liquid_stock_passes_full_shortlist(self) -> None:
        stage_2_passed, _ = evaluate_minervini_criteria(
            self.indicators,
            rs_rating=85,
        )
        shortlist_passed, metrics = evaluate_vcp_shortlist_criteria(
            self.indicators,
            self.config,
        )

        self.assertTrue(stage_2_passed)
        self.assertTrue(shortlist_passed)
        self.assertGreater(metrics["adtv_crore"], 10)
        self.assertLessEqual(metrics["volume_dry_up_ratio"], 0.8)
        self.assertTrue(metrics["criteria_matches"]["squeeze_combo"])

    def test_bollinger_percentile_matches_trailing_window_rank(self) -> None:
        latest = self.indicators.iloc[-1]
        trailing = self.indicators["bb_width"].dropna().iloc[
            -self.config.bb_percentile_lookback_days:
        ]
        expected = float((trailing <= trailing.iloc[-1]).mean())

        self.assertAlmostEqual(latest["bb_width_percentile"], expected)

    def test_rs_rating_is_cross_sectional(self) -> None:
        ratings = compute_relative_strength_ratings(
            [
                {"instrument_id": "weak", "perf_score": -0.1},
                {"instrument_id": "middle", "perf_score": 0.2},
                {"instrument_id": "strong", "perf_score": 0.5},
            ]
        )

        self.assertLess(ratings["weak"], ratings["middle"])
        self.assertLess(ratings["middle"], ratings["strong"])
        self.assertEqual(ratings["strong"], 99)

    def test_shortlist_is_rs_descending_capped_and_deterministic(self) -> None:
        candidates = [
            {
                "symbol": f"STOCK{index}",
                "technical_score": 100 - index,
                "rs_rating": 99 - index,
                "pct_from_52w_high": 0.10,
            }
            for index in range(25)
        ]
        candidates.append(
            {
                "symbol": "TIE_NEARER",
                "technical_score": 100,
                "rs_rating": 99,
                "pct_from_52w_high": 0.05,
            }
        )

        ranked = rank_and_cap_shortlist(candidates, limit=20)

        self.assertEqual(len(ranked), 20)
        self.assertEqual(ranked[0]["symbol"], "TIE_NEARER")
        self.assertEqual(ranked[1]["symbol"], "STOCK0")
        self.assertEqual([row["result_rank"] for row in ranked], list(range(1, 21)))

    def test_v2_shortlist_retains_at_most_50_scored_setups(self) -> None:
        candidates = [
            {
                "symbol": f"STOCK{index:02d}",
                "technical_score": 100 - index,
                "rs_rating": 99 - index,
                "pct_from_52w_high": index / 1000,
            }
            for index in range(60)
        ]

        ranked = rank_and_cap_shortlist(candidates, limit=50)

        self.assertEqual(len(ranked), 50)
        self.assertEqual(ranked[0]["symbol"], "STOCK00")
        self.assertEqual(ranked[-1]["symbol"], "STOCK49")

    def test_only_top_20_are_selected_for_fundamentals(self) -> None:
        self.assertEqual(
            fundamental_selection_status(20, limit=20, enabled=True),
            (True, "queued"),
        )
        self.assertEqual(
            fundamental_selection_status(21, limit=20, enabled=True),
            (False, "not_requested"),
        )
        self.assertEqual(
            fundamental_selection_status(1, limit=20, enabled=False),
            (False, "not_requested"),
        )


if __name__ == "__main__":
    unittest.main()
