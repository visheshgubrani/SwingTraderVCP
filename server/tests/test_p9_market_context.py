from decimal import Decimal
import csv
from pathlib import Path
import unittest

from app.domain.p9_market_context import (
    classify_breadth,
    classify_distribution_count,
    classify_index_trend,
    classify_market_light,
    blended_sector_score,
    contextual_selection_order,
    exposure_multiplier,
    is_distribution_session,
    rank_sector_strength,
    typical_turnover,
)
from app.domain.p9_sector_taxonomy import INDUSTRY_TO_SECTOR, SECTORS


class P9MarketContextTests(unittest.TestCase):
    def test_market_thresholds_and_majority(self) -> None:
        self.assertEqual(classify_breadth(Decimal("50")), "yellow")
        self.assertEqual(classify_breadth(Decimal("50.01")), "green")
        self.assertEqual(classify_breadth(Decimal("24.99")), "red")
        self.assertEqual(classify_distribution_count(3), "green")
        self.assertEqual(classify_distribution_count(4), "yellow")
        self.assertEqual(classify_distribution_count(6), "red")
        self.assertEqual(
            classify_market_light(trend="green", breadth="red", distribution="yellow"),
            "yellow",
        )
        self.assertEqual(
            classify_market_light(trend="green", breadth="green", distribution="red"),
            "green",
        )
        self.assertEqual(exposure_multiplier("yellow"), Decimal("0.50"))

    def test_index_trend_and_unavailable(self) -> None:
        self.assertEqual(
            classify_index_trend(
                close=Decimal("110"), sma50=Decimal("105"), sma200=Decimal("100"),
                sma200_20_sessions_ago=Decimal("99"),
            ),
            "green",
        )
        self.assertEqual(
            classify_index_trend(
                close=Decimal("90"), sma50=Decimal("95"), sma200=Decimal("100"),
                sma200_20_sessions_ago=Decimal("101"),
            ),
            "red",
        )
        self.assertEqual(
            classify_index_trend(
                close=None, sma50=Decimal("95"), sma200=Decimal("100"),
                sma200_20_sessions_ago=Decimal("101"),
            ),
            "unavailable",
        )

    def test_distribution_uses_constituent_turnover(self) -> None:
        turnover = typical_turnover(
            high=Decimal("102"), low=Decimal("98"), close=Decimal("100"), volume=10
        )
        self.assertEqual(turnover, Decimal("1000"))
        self.assertTrue(
            is_distribution_session(
                current_close=Decimal("99.4"), previous_close=Decimal("100"),
                current_turnover=Decimal("1001"), previous_turnover=Decimal("1000"),
            )
        )
        self.assertFalse(
            is_distribution_session(
                current_close=Decimal("99.4"), previous_close=Decimal("100"),
                current_turnover=Decimal("999"), previous_turnover=Decimal("1000"),
            )
        )

    def test_sixteen_sector_ordinals_and_boundary_ties(self) -> None:
        scores = {f"s{i:02d}": Decimal(20 - i) for i in range(1, 17)}
        ranked = rank_sector_strength(scores)
        self.assertEqual(sum(item.raw_tier == "leading" for item in ranked), 5)
        self.assertEqual(sum(item.raw_tier == "lagging" for item in ranked), 5)

        tied = dict(scores)
        tied["s05"] = tied["s06"]
        tied_ranked = {item.sector_code: item for item in rank_sector_strength(tied)}
        self.assertEqual(tied_ranked["s05"].raw_tier, "neutral")
        self.assertEqual(tied_ranked["s06"].raw_tier, "neutral")

    def test_sector_formula_champion_and_challengers(self) -> None:
        short = Decimal("0.10")
        long = Decimal("0.02")
        self.assertEqual(
            blended_sector_score(excess_short=short, excess_long=long),
            Decimal("0.068"),
        )
        self.assertEqual(
            blended_sector_score(
                excess_short=short, excess_long=long, short_weight=Decimal("0.50")
            ),
            Decimal("0.060"),
        )

    def test_checked_in_taxonomy_covers_current_csv_industries(self) -> None:
        csv_path = Path(__file__).resolve().parents[1] / "ind_nifty500list.csv"
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            industries = {row["Industry"].strip() for row in csv.DictReader(handle)}
        self.assertEqual(set(INDUSTRY_TO_SECTOR), industries)
        self.assertEqual(len(SECTORS), 16)

    def test_lagging_requires_two_snapshots_and_releases_immediately(self) -> None:
        scores = {f"s{i:02d}": Decimal(20 - i) for i in range(1, 17)}
        first = {item.sector_code: item for item in rank_sector_strength(scores)}
        self.assertEqual(first["s16"].raw_tier, "lagging")
        self.assertEqual(first["s16"].gate_tier, "neutral")
        second = {
            item.sector_code: item
            for item in rank_sector_strength(
                scores,
                previous_raw_tiers={code: value.raw_tier for code, value in first.items()},
            )
        }
        self.assertEqual(second["s16"].gate_tier, "lagging")
        recovered = dict(scores)
        recovered["s16"] = Decimal("30")
        third = {
            item.sector_code: item
            for item in rank_sector_strength(
                recovered,
                previous_raw_tiers={code: value.raw_tier for code, value in second.items()},
            )
        }
        self.assertEqual(third["s16"].gate_tier, "leading")

    def test_contextual_order_never_changes_technical_rank(self) -> None:
        source = [
            {"symbol": "A", "technical_score": 90, "result_rank": 1, "sector_tier": "lagging", "sector_rs_rating": 10},
            {"symbol": "B", "technical_score": 89, "result_rank": 2, "sector_tier": "leading", "sector_rs_rating": 90},
            {"symbol": "C", "technical_score": 87, "result_rank": 3, "sector_tier": "leading", "sector_rs_rating": 99},
        ]
        ordered = contextual_selection_order(source)
        self.assertEqual([row["symbol"] for row in ordered], ["B", "A", "C"])
        ranks = {row["symbol"]: row["result_rank"] for row in ordered}
        self.assertEqual(ranks, {"A": 1, "B": 2, "C": 3})


if __name__ == "__main__":
    unittest.main()
