import datetime
import unittest

from app.services.nse_fundamental_risk import (
    leverage_risk,
    parse_integrated_xbrl,
    parse_shareholding_xbrl,
    promoter_pledge_risk,
    select_latest_filing,
)


def integrated_xml(*facts: str, taxonomy: str = "2026-01-31") -> str:
    return f"""<?xml version="1.0"?>
    <xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:link="http://www.xbrl.org/2003/linkbase"
      xmlns:xlink="http://www.w3.org/1999/xlink"
      xmlns:in-capmkt="http://www.sebi.gov.in/xbrl/{taxonomy}/in-capmkt">
      <link:schemaRef xlink:type="simple" xlink:href="in-capmkt-ent-{taxonomy}.xsd"/>
      <xbrli:context id="InstantCurrent"><xbrli:entity><xbrli:identifier>ENTITY</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:context id="QuarterCurrent"><xbrli:entity><xbrli:identifier>ENTITY</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="YtdCurrent"><xbrli:entity><xbrli:identifier>ENTITY</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="PriorYear"><xbrli:entity><xbrli:identifier>ENTITY</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate></xbrli:period></xbrli:context>
      {''.join(facts)}
    </xbrli:xbrl>"""


def fact(name: str, context: str, value: float) -> str:
    return f'<in-capmkt:{name} contextRef="{context}" unitRef="pure">{value}</in-capmkt:{name}>'


def pledge_xml(*, promoter: float, pledged: float, reported_fraction: float, taxonomy: str = "2025-10-31") -> str:
    return f"""<?xml version="1.0"?>
    <xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:link="http://www.xbrl.org/2003/linkbase"
      xmlns:xlink="http://www.w3.org/1999/xlink"
      xmlns:in-bse-shp="http://www.bseindia.com/xbrl/shp/{taxonomy}/in-bse-shp">
      <link:schemaRef xlink:type="simple" xlink:href="in-bse-shp-{taxonomy}.xsd"/>
      <xbrli:context id="ShareholdingOfPromoterAndPromoterGroup_ContextI"><xbrli:entity><xbrli:identifier>ENTITY</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
      <in-bse-shp:NumberOfShares contextRef="ShareholdingOfPromoterAndPromoterGroup_ContextI" unitRef="shares">{promoter}</in-bse-shp:NumberOfShares>
      <in-bse-shp:NumberOfSharesEncumberedUnderPledged contextRef="ShareholdingOfPromoterAndPromoterGroup_ContextI" unitRef="shares">{pledged}</in-bse-shp:NumberOfSharesEncumberedUnderPledged>
      <in-bse-shp:EncumberedShareUnderPledgedAsPercentageOfTotalNumberOfShares contextRef="ShareholdingOfPromoterAndPromoterGroup_ContextI" unitRef="pure">{reported_fraction}</in-bse-shp:EncumberedShareUnderPledgedAsPercentageOfTotalNumberOfShares>
    </xbrli:xbrl>"""


class NseIntegratedXbrlTests(unittest.TestCase):
    period = datetime.date(2026, 9, 30)

    def test_prefers_instant_de_and_longest_current_duration_ratios(self) -> None:
        xml = integrated_xml(
            fact("DebtEquityRatio", "InstantCurrent", 0.02),
            fact("DebtEquityRatio", "YtdCurrent", 0.03),
            fact("InterestServiceCoverageRatio", "QuarterCurrent", 0.05),
            fact("InterestServiceCoverageRatio", "YtdCurrent", 0.03),
            fact("InterestServiceCoverageRatio", "PriorYear", 9.99),
            fact("DebtServiceCoverageRatio", "QuarterCurrent", 0.04),
            fact("DebtServiceCoverageRatio", "YtdCurrent", 0.025),
        )
        parsed, taxonomy, status = parse_integrated_xbrl(xml, reporting_period=self.period)

        self.assertEqual(taxonomy, "2026-01-31")
        self.assertEqual(status, "succeeded")
        self.assertEqual(parsed["debt_to_equity"], 2.0)
        self.assertEqual(parsed["contexts"]["debt_to_equity"], "instant")
        self.assertEqual(parsed["interest_service_coverage"], 3.0)
        self.assertEqual(parsed["debt_service_coverage"], 2.5)
        self.assertEqual(parsed["contexts"]["interest_service_coverage"]["start_date"], "2026-04-01")

    def test_duration_tagged_de_requires_agreement(self) -> None:
        agreeing = integrated_xml(
            fact("DebtEquityRatio", "QuarterCurrent", 0.02),
            fact("DebtEquityRatio", "YtdCurrent", 0.02005),
        )
        parsed, _, status = parse_integrated_xbrl(agreeing, reporting_period=self.period)
        self.assertEqual(status, "succeeded")
        self.assertEqual(parsed["debt_to_equity"], 2.0)

        conflicting = integrated_xml(
            fact("DebtEquityRatio", "QuarterCurrent", 0.02),
            fact("DebtEquityRatio", "YtdCurrent", 0.03),
        )
        parsed, _, status = parse_integrated_xbrl(conflicting, reporting_period=self.period)
        self.assertEqual(status, "ambiguous")
        self.assertIsNone(parsed["debt_to_equity"])

    def test_unknown_taxonomy_is_not_guessed(self) -> None:
        parsed, taxonomy, status = parse_integrated_xbrl(
            integrated_xml(fact("DebtEquityRatio", "InstantCurrent", 0.02), taxonomy="2099-01-01"),
            reporting_period=self.period,
        )
        self.assertEqual(taxonomy, "2099-01-01")
        self.assertEqual(status, "ambiguous")
        self.assertEqual(parsed["reason"], "unsupported_taxonomy")

    def test_filing_selection_prefers_current_consolidated_revision(self) -> None:
        records = [
            {"qe_Date": "30-SEP-2026", "consolidated": "Standalone", "type_Sub": "Revision", "revised_Date": "02-Nov-2026 10:00:00"},
            {"qe_Date": "30-SEP-2026", "consolidated": "Consolidated", "type_Sub": "Original", "creation_Date": "01-Nov-2026 10:00:00"},
            {"qe_Date": "30-JUN-2026", "consolidated": "Consolidated", "type_Sub": "Revision", "revised_Date": "03-Nov-2026 10:00:00"},
        ]
        selected = select_latest_filing(
            records,
            as_of_date=datetime.date(2026, 11, 5),
            period_key="qe_Date",
            prefer_consolidated=True,
        )
        self.assertEqual(selected["consolidated"], "Consolidated")
        self.assertEqual(selected["qe_Date"], "30-SEP-2026")


class NseRiskRuleTests(unittest.TestCase):
    def test_pledge_uses_promoter_holding_denominator(self) -> None:
        parsed, _, status = parse_shareholding_xbrl(
            pledge_xml(promoter=800, pledged=100, reported_fraction=0.125),
            reporting_period=datetime.date(2026, 9, 30),
        )
        self.assertEqual(status, "succeeded")
        self.assertEqual(parsed["pledged_pct_of_promoter_holding"], 12.5)
        self.assertEqual(parsed["denominator"], "total_promoter_group_shares")
        self.assertEqual(promoter_pledge_risk(parsed)["status"], "red")

    def test_zero_denominator_and_cross_check_mismatch_are_unknown(self) -> None:
        zero, _, _ = parse_shareholding_xbrl(
            pledge_xml(promoter=0, pledged=0, reported_fraction=0),
            reporting_period=datetime.date(2026, 9, 30),
        )
        mismatch, _, _ = parse_shareholding_xbrl(
            pledge_xml(promoter=800, pledged=100, reported_fraction=0.10),
            reporting_period=datetime.date(2026, 9, 30),
        )
        self.assertEqual(promoter_pledge_risk(zero)["status"], "unknown")
        self.assertEqual(promoter_pledge_risk(mismatch)["status"], "unknown")

    def test_pledge_score_impacts_follow_severity_thresholds(self) -> None:
        self.assertEqual(promoter_pledge_risk({"pledged_pct_of_promoter_holding": 0})["score_impact"], 0)
        self.assertEqual(promoter_pledge_risk({"pledged_pct_of_promoter_holding": 5})["score_impact"], -3)
        self.assertEqual(promoter_pledge_risk({"pledged_pct_of_promoter_holding": 10})["score_impact"], -8)
        self.assertEqual(promoter_pledge_risk({"pledged_pct_of_promoter_holding": 20})["score_impact"], -15)
        self.assertEqual(promoter_pledge_risk(None)["score_impact"], 0)

    def test_leverage_thresholds_adjust_fundamental_score_and_financials_are_na(self) -> None:
        severe = leverage_risk(
            {"debt_to_equity": 3.1, "interest_service_coverage": 4},
            industry="Capital Goods",
        )
        financial = leverage_risk(
            {"debt_to_equity": 8, "interest_service_coverage": 0.5},
            industry="Financial Services - NBFC",
        )
        unavailable = leverage_risk(
            {"debt_to_equity": 0, "interest_service_coverage": 0},
            industry="Industrials",
        )
        self.assertEqual(severe["status"], "severe")
        self.assertEqual(severe["score_impact"], -10)
        self.assertFalse(severe["automatic_rejection"])
        self.assertEqual(financial["status"], "not_applicable")
        self.assertEqual(financial["score_impact"], 0)
        self.assertEqual(unavailable["status"], "unknown")
        self.assertEqual(unavailable["score_impact"], 0)


if __name__ == "__main__":
    unittest.main()
