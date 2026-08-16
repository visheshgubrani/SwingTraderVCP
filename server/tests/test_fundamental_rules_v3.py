import unittest

from app.services.fundamental_rules import (
    apply_filing_risk_adjustments,
    score_minervini_inspired,
)


def facts_fixture(*, financial: bool = False, complete: bool = True) -> dict:
    annual = [
        {"period": "Mar 2026", "value": 150},
        {"period": "Mar 2025", "value": 120},
        {"period": "Mar 2024", "value": 95},
    ] if complete else []
    evidence = {
        "growth.annual_eps_cagr": {"value": 25},
        "growth.latest_annual_eps_yoy": {"value": 30},
        "growth.annual_net_profit_cagr": {"value": 22},
        "growth.annual_revenue_cagr": {"value": 20},
        "growth.latest_annual_revenue_yoy": {"value": 18},
        "ratios.roe": {"value": {"company": 20}},
        "ratios.roce": {"value": {"company": 22}},
        "margins.latest_annual_yoy_change": {"value": {"change_percentage_points": 1}},
        "quality.cash_from_operations_to_pat_3y": {"value": 1.0},
        "ownership.promoters_change": {"value": {"change_percentage_points": 0}},
        "ownership.fii_change": {"value": {"change_percentage_points": 1}},
    }
    return {
        "schema_version": "fundamental_facts_v3",
        "company": {"is_financial_sector": financial},
        "histories": {
            "annual": {
                "revenue": annual,
                "net_profit": annual,
                "basic_eps": annual,
            },
            "quarterly": {"revenue": [], "net_profit": [], "basic_eps": None},
        },
        "ratios": {
            "roe": {"company": 20},
            "roce": {"company": 22},
        },
        "evidence": evidence,
        "provider_limitations": ["quarterly_eps_yoy"],
    }


class FundamentalRulesV3Tests(unittest.TestCase):
    def test_complete_data_gets_a_score_and_missing_quarterly_metric_reduces_coverage(self):
        assessment = score_minervini_inspired(facts_fixture())
        self.assertEqual(assessment["grade"], "A")
        self.assertIsNotNone(assessment["score"])
        self.assertLess(assessment["coverage_pct"], 100)
        self.assertIn("quarterly_eps_yoy", assessment["provider_limitations"])

    def test_insufficient_history_has_no_public_score(self):
        assessment = score_minervini_inspired(facts_fixture(complete=False))
        self.assertEqual(assessment["grade"], "insufficient")
        self.assertIsNone(assessment["score"])
        self.assertGreater(assessment["coverage_pct"], 0.0)

    def test_financial_company_excludes_cash_conversion_from_denominator(self):
        assessment = score_minervini_inspired(facts_fixture(financial=True))
        self.assertEqual(assessment["max_points"], 90.0)
        cash = next(component for component in assessment["components"] if component["name"] == "cash_conversion")
        self.assertEqual(cash["max_points"], 0.0)

    def test_material_deterioration_is_a_red_flag(self):
        facts = facts_fixture()
        facts["evidence"]["growth.annual_eps_cagr"]["value"] = -1
        facts["evidence"]["margins.latest_annual_yoy_change"]["value"] = {"change_percentage_points": -3}
        assessment = score_minervini_inspired(facts)
        self.assertIn("non_positive_eps_cagr", assessment["red_flags"])
        self.assertIn("annual_margin_compression", assessment["red_flags"])
        earnings = next(
            component
            for component in assessment["components"]
            if component["name"] == "earnings"
        )
        eps_cagr = next(
            metric
            for metric in earnings["metrics"]
            if metric["key"] == "annual_eps_cagr"
        )
        profitability = next(
            component
            for component in assessment["components"]
            if component["name"] == "profitability"
        )
        margin = next(
            metric
            for metric in profitability["metrics"]
            if metric["key"] == "annual_margin_change"
        )
        self.assertEqual(eps_cagr["status"], "negative")
        self.assertEqual(margin["status"], "negative")

    def test_known_filing_risks_reduce_score_and_recompute_grade(self):
        scorecard = {
            "score": 82.0,
            "grade": "A",
            "red_flags": [],
        }
        adjusted = apply_filing_risk_adjustments(
            scorecard,
            {
                "promoter_pledge": {
                    "status": "red",
                    "score_impact": -8,
                    "source": "nse_shareholding_xbrl",
                },
                "leverage": {
                    "status": "warning",
                    "score_impact": -2,
                    "source": "nse_integrated_xbrl",
                },
            },
        )

        self.assertEqual(scorecard["score"], 82.0)
        self.assertEqual(adjusted["base_score"], 82.0)
        self.assertEqual(adjusted["risk_score_impact"], -10.0)
        self.assertEqual(adjusted["score"], 72.0)
        self.assertEqual(adjusted["grade"], "B")
        self.assertEqual(
            adjusted["red_flags"],
            ["promoter_pledge_red", "leverage_warning"],
        )

    def test_unknown_or_invalid_risk_does_not_receive_a_penalty(self):
        adjusted = apply_filing_risk_adjustments(
            {"score": 80.0, "grade": "A", "red_flags": []},
            {
                "promoter_pledge": {"status": "unknown", "score_impact": -15},
                "leverage": {"status": "red", "score_impact": 5},
            },
        )
        self.assertEqual(adjusted["score"], 80.0)
        self.assertEqual(adjusted["risk_score_impact"], 0.0)
        self.assertEqual(adjusted["risk_adjustments"], [])


if __name__ == "__main__":
    unittest.main()
