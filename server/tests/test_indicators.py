import unittest

import numpy as np
import pandas as pd

from app.services.indicators import (
    attach_rs_line_metrics,
    build_equal_weight_index_closes,
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

    def test_up_down_volume_ratio_trailing_window(self) -> None:
        latest = self.indicators.iloc[-1]
        self.assertIn("up_down_volume_ratio", self.indicators.columns)
        self.assertTrue(np.isfinite(latest["up_down_volume_ratio"]))

    def test_pocket_pivot_empty_down_window_passes_volume_condition(self) -> None:
        days = 80
        # Rise then flat base so SMA50 proximity stays within 3%.
        close = np.concatenate(
            [np.linspace(100.0, 110.0, days - 15), np.full(15, 110.0)]
        )
        # Tiny uptick on the last bar keeps it an up-day without extending.
        close = close.copy()
        close[-1] = 110.2
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=days, freq="B"),
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": np.full(days, 1_000_000.0),
            }
        )
        indicators = compute_technical_indicators(frame, self.config)
        extension = (
            indicators.iloc[-1]["close"] / indicators.iloc[-1]["sma_50"] - 1.0
        ) * 100
        self.assertLessEqual(extension, self.config.pocket_pivot_max_extension_pct)
        self.assertTrue(bool(indicators.iloc[-1]["pocket_pivot"]))
        self.assertEqual(indicators.iloc[-1]["pocket_pivot_age"], 0.0)

    def test_pocket_pivot_rejects_extended_move(self) -> None:
        days = 80
        close = np.concatenate(
            [np.linspace(100.0, 110.0, days - 1), np.array([150.0])]
        )
        volume = np.concatenate([np.full(days - 1, 500_000.0), np.array([2_000_000.0])])
        # Insert a down day so the volume comparison is defined.
        close[-5] = close[-6] - 1.0
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=days, freq="B"),
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": volume,
            }
        )
        indicators = compute_technical_indicators(frame, self.config)
        extension = (
            indicators.iloc[-1]["close"] / indicators.iloc[-1]["sma_50"] - 1.0
        ) * 100
        self.assertGreater(extension, self.config.pocket_pivot_max_extension_pct)
        self.assertFalse(bool(indicators.iloc[-1]["pocket_pivot"]))

    def test_rs_line_inner_joins_on_date(self) -> None:
        stock = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
                ),
                "close": [100.0, 102.0, 101.0, 105.0],
                "high": [101.0, 103.0, 102.0, 106.0],
                "low": [99.0, 101.0, 100.0, 104.0],
                "volume": [1_000_000] * 4,
            }
        )
        # Index missing 2024-01-04 on purpose.
        index_closes = pd.Series(
            [1000.0, 1005.0, 1010.0],
            index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-05"]),
        )
        with_rs = attach_rs_line_metrics(stock, index_closes, lookback_days=252)

        self.assertTrue(np.isnan(with_rs.loc[2, "rs_line"]))
        self.assertFalse(np.isnan(with_rs.loc[0, "rs_line"]))
        self.assertFalse(np.isnan(with_rs.loc[3, "rs_line"]))
        self.assertAlmostEqual(with_rs.loc[0, "rs_line"], 0.1)
        self.assertAlmostEqual(with_rs.loc[3, "rs_line_pct_off_high"], 0.0)

    def test_equal_weight_synthetic_index(self) -> None:
        frames = [
            pd.DataFrame(
                {
                    "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                    "close": [100.0, 110.0],
                }
            ),
            pd.DataFrame(
                {
                    "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                    "close": [50.0, 50.0],
                }
            ),
        ]
        level = build_equal_weight_index_closes(frames)
        # Day1: (1.0 + 1.0)/2 = 1.0; Day2: (1.1 + 1.0)/2 = 1.05
        self.assertAlmostEqual(float(level.iloc[0]), 1.0)
        self.assertAlmostEqual(float(level.iloc[1]), 1.05)


if __name__ == "__main__":
    unittest.main()
