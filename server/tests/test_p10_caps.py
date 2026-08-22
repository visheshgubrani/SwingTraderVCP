import datetime as dt
import unittest
from decimal import Decimal

from app.domain.p10_caps import (
    RiskPolicyConfig,
    PortfolioState,
    CompetingCandidate,
    evaluate_portfolio_caps,
    sort_competing_candidates,
)


class TestP10Caps(unittest.TestCase):
    def setUp(self):
        self.policy = RiskPolicyConfig(version=1, name="Balanced")

    def test_evaluate_portfolio_caps_normal(self):
        # 1,000,000 deployable capital, 10,000 current open risk (1% of 4% limit = 40,000)
        # Existing name = 0, sector = 50,000 (limit 300,000), cluster = 50,000 (limit 300,000)
        state = PortfolioState(
            deployable_capital=Decimal("1000000.00"),
            current_open_risk=Decimal("10000.00"),
            current_open_positions_count=2,
            daily_realized_losses=Decimal("0.00"),
            existing_name_notional=Decimal("0.00"),
            existing_sector_notional=Decimal("50000.00"),
            existing_cluster_notional=Decimal("50000.00"),
        )
        res = evaluate_portfolio_caps(self.policy, state, symbol="RELIANCE", is_new_position=True)
        self.assertFalse(res.is_blocked)
        # Allowed risk budget = min(10,000 [1% per trade], 30,000 [headroom]) = 10,000
        self.assertEqual(res.allowed_risk_budget, Decimal("10000.00"))
        # Allowed notional = min(150k name, 250k sector, 250k cluster) = 150,000
        self.assertEqual(res.allowed_notional_budget, Decimal("150000.00"))

    def test_evaluate_portfolio_caps_blocks_on_daily_loss(self):
        # 2% of 1M = 20,000. Realized losses = 21,000 -> Blocked!
        state = PortfolioState(
            deployable_capital=Decimal("1000000.00"),
            current_open_risk=Decimal("5000.00"),
            current_open_positions_count=1,
            daily_realized_losses=Decimal("21000.00"),
            existing_name_notional=Decimal("0.00"),
            existing_sector_notional=Decimal("0.00"),
            existing_cluster_notional=Decimal("0.00"),
        )
        res = evaluate_portfolio_caps(self.policy, state, symbol="TCS", is_new_position=True)
        self.assertTrue(res.is_blocked)
        self.assertIn("Daily realized loss", res.blocking_reason)

    def test_evaluate_portfolio_caps_blocks_on_max_positions(self):
        # Limit 8 positions. Current = 8 -> Blocked for new position!
        state = PortfolioState(
            deployable_capital=Decimal("1000000.00"),
            current_open_risk=Decimal("20000.00"),
            current_open_positions_count=8,
            daily_realized_losses=Decimal("0.00"),
            existing_name_notional=Decimal("0.00"),
            existing_sector_notional=Decimal("0.00"),
            existing_cluster_notional=Decimal("0.00"),
        )
        res = evaluate_portfolio_caps(self.policy, state, symbol="INFY", is_new_position=True)
        self.assertTrue(res.is_blocked)
        self.assertIn("Maximum open positions", res.blocking_reason)

    def test_sort_competing_candidates_2pt_score_bands(self):
        now = dt.datetime(2026, 8, 17, 10, 0)
        c1 = CompetingCandidate(
            candidate_id="c1",
            symbol="A",
            scanner_score=Decimal("95.0"),
            gemini_confidence=Decimal("0.70"),
            conservative_rr=Decimal("2.5"),
            trigger_timestamp=now,
            requested_risk=Decimal("5000"),
            requested_notional=Decimal("100000"),
        )
        c2 = CompetingCandidate(
            candidate_id="c2",
            symbol="B",
            scanner_score=Decimal("94.5"),  # Within 2 pts of 95.0 -> same band!
            gemini_confidence=Decimal("0.90"),  # Unused for ranking; R:R decides within band
            conservative_rr=Decimal("2.0"),
            trigger_timestamp=now,
            requested_risk=Decimal("5000"),
            requested_notional=Decimal("100000"),
        )
        c3 = CompetingCandidate(
            candidate_id="c3",
            symbol="C",
            scanner_score=Decimal("91.0"),  # Lower band (diff > 2)
            gemini_confidence=Decimal("0.95"),
            conservative_rr=Decimal("3.0"),
            trigger_timestamp=now,
            requested_risk=Decimal("5000"),
            requested_notional=Decimal("100000"),
        )

        res = sort_competing_candidates([c1, c2, c3])
        self.assertFalse(res.has_capacity_conflict)
        # A wins first (band 0, higher R:R than B), then B (band 0), then C (lower band)
        self.assertEqual([c.candidate_id for c in res.ranked_candidates], ["c1", "c2", "c3"])

    def test_sort_competing_candidates_detects_exact_tie_conflict(self):
        now = dt.datetime(2026, 8, 17, 10, 0)
        c1 = CompetingCandidate(
            candidate_id="c1",
            symbol="A",
            scanner_score=Decimal("95.0"),
            gemini_confidence=Decimal("0.85"),
            conservative_rr=Decimal("2.5"),
            trigger_timestamp=now,
            requested_risk=Decimal("5000"),
            requested_notional=Decimal("100000"),
        )
        c2 = CompetingCandidate(
            candidate_id="c2",
            symbol="B",
            scanner_score=Decimal("95.0"),  # Identical score
            gemini_confidence=Decimal("0.85"),  # Unused; exact R:R + timestamp still tie
            conservative_rr=Decimal("2.5"),   # Identical R:R
            trigger_timestamp=now,             # Identical timestamp
            requested_risk=Decimal("5000"),
            requested_notional=Decimal("100000"),
        )

        res = sort_competing_candidates([c1, c2])
        self.assertTrue(res.has_capacity_conflict)
        self.assertEqual(set(res.conflict_candidate_ids), {"c1", "c2"})


if __name__ == "__main__":
    unittest.main()
