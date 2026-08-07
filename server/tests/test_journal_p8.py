import datetime
import unittest
from decimal import Decimal

from app.domain.journal_aggregation import (
    aggregate_exit_outcome,
    closure_date_ist,
    compute_gross_pnl,
    compute_r_multiple,
    compute_summary_metrics,
    hold_duration_hours,
    period_key,
    weighted_average_price,
)
from app.domain.journal_charges import FillLeg, estimate_cnc_charges
from app.domain.market_regime import classify_regime, is_stale_reference
from app.services.journal_ai_coach import _build_deterministic_metrics, _cohort_breakdown
from app.services.journal_llm import JournalCoachReport
from app.worker import WorkerSettings


class JournalChargesTests(unittest.TestCase):
    def test_cnc_charges_version_and_total(self):
        breakdown = estimate_cnc_charges(
            [
                FillLeg(side="buy", quantity=10, price=Decimal("100")),
                FillLeg(side="sell", quantity=10, price=Decimal("110")),
            ]
        )
        self.assertEqual(breakdown.version, "fyers_cnc_v1")
        self.assertGreater(breakdown.total, Decimal("0"))
        self.assertEqual(breakdown.label, "estimated")

    def test_dp_charged_once_per_sell_scrip_day(self):
        breakdown = estimate_cnc_charges(
            [
                FillLeg(side="sell", quantity=5, price=Decimal("100")),
                FillLeg(side="sell", quantity=5, price=Decimal("100")),
            ]
        )
        self.assertEqual(breakdown.dp_charges, Decimal("15.93"))


class MarketRegimeTests(unittest.TestCase):
    def test_bullish_regime(self):
        regime, evidence = classify_regime(
            benchmark_price=Decimal("22000"),
            sma_50=Decimal("21000"),
            sma_200=Decimal("20000"),
            sma_50_slope_20d=Decimal("100"),
            constituents_above_sma_50=300,
            constituents_total=500,
        )
        self.assertEqual(regime, "bullish")
        self.assertTrue(evidence.price_above_sma_50)

    def test_bearish_regime(self):
        regime, _ = classify_regime(
            benchmark_price=Decimal("18000"),
            sma_50=Decimal("19000"),
            sma_200=Decimal("20000"),
            sma_50_slope_20d=Decimal("-50"),
            constituents_above_sma_50=200,
            constituents_total=500,
        )
        self.assertEqual(regime, "bearish")

    def test_unavailable_when_stale(self):
        regime, _ = classify_regime(
            benchmark_price=Decimal("22000"),
            sma_50=Decimal("21000"),
            sma_200=Decimal("20000"),
            sma_50_slope_20d=Decimal("100"),
            constituents_above_sma_50=300,
            constituents_total=500,
            stale=True,
        )
        self.assertEqual(regime, "unavailable")

    def test_staleness_threshold(self):
        ref = datetime.date(2026, 7, 20)
        as_of = datetime.date(2026, 7, 24)
        self.assertTrue(is_stale_reference(ref, as_of, max_age_days=3))


class JournalAggregationTests(unittest.TestCase):
    def test_weighted_average_price(self):
        avg = weighted_average_price([(10, Decimal("100")), (10, Decimal("120"))])
        self.assertEqual(avg, Decimal("110"))

    def test_partial_entry_exit_gross_pnl(self):
        gross = compute_gross_pnl(
            side="long",
            entry_fills=[(10, Decimal("100"))],
            exit_fills=[(5, Decimal("110")), (5, Decimal("120"))],
        )
        self.assertEqual(gross, Decimal("150"))

    def test_mixed_exit_outcome(self):
        self.assertEqual(
            aggregate_exit_outcome(["stop_loss", "manual"]),
            "mixed",
        )

    def test_r_multiple(self):
        r = compute_r_multiple(
            gross_or_net_pnl=Decimal("500"),
            risk_amount=Decimal("250"),
        )
        self.assertEqual(r, Decimal("2"))

    def test_period_key_ist_month(self):
        closed = datetime.datetime(2026, 7, 20, 15, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(period_key(closed, "month"), "2026-07")
        self.assertEqual(closure_date_ist(closed).year, 2026)

    def test_hold_duration_hours(self):
        opened = datetime.datetime(2026, 7, 20, 9, 0, tzinfo=datetime.timezone.utc)
        closed = datetime.datetime(2026, 7, 21, 9, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(hold_duration_hours(opened, closed), Decimal("24"))

    def test_summary_metrics(self):
        trades = [
            {
                "id": "a",
                "closed_at": datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
                "gross_pnl": "100",
                "net_pnl": "90",
                "total_charges": "10",
                "net_r_multiple": "2",
                "hold_duration_hours": "24",
            },
            {
                "id": "b",
                "closed_at": datetime.datetime(2026, 7, 2, tzinfo=datetime.timezone.utc),
                "gross_pnl": "-50",
                "net_pnl": "-55",
                "total_charges": "5",
                "net_r_multiple": "-1",
                "hold_duration_hours": "12",
            },
        ]
        summary = compute_summary_metrics(trades)
        self.assertEqual(summary["trade_count"], 2)
        self.assertEqual(summary["net_pnl"], Decimal("35"))


class JournalAiBatchingTests(unittest.TestCase):
    def test_batches_cover_all_trades(self):
        trades = [
            {
                "id": str(i),
                "symbol": "NSE:X",
                "net_pnl": i,
                "closed_at": datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
            }
            for i in range(60)
        ]
        metrics = _build_deterministic_metrics(trades)
        represented = sum(batch["trade_count"] for batch in metrics["batches"])
        self.assertEqual(represented, 60)

    def test_cohort_breakdown(self):
        trades = [
            {"id": "1", "setup_tags": ["VCP"], "net_pnl": 100, "regime": "bullish"},
            {"id": "2", "setup_tags": ["VCP"], "net_pnl": -50, "regime": "neutral"},
        ]
        setups = _cohort_breakdown(trades, field="setup_tags")
        self.assertEqual(setups[0]["cohort"], "VCP")
        self.assertEqual(setups[0]["trade_count"], 2)


class JournalCoachSchemaTests(unittest.TestCase):
    def test_strict_schema_accepts_minimal_valid_report(self):
        report = JournalCoachReport.model_validate(
            {
                "strengths": ["Consistent risk sizing"],
                "weaknesses": [],
                "setup_cohorts": [],
                "regime_cohorts": [],
                "recurring_mistakes": [],
                "data_quality_warnings": [],
                "review_questions": [],
            }
        )
        self.assertEqual(report.strengths[0], "Consistent risk sizing")


class WorkerRegistrationTests(unittest.TestCase):
    def test_journal_jobs_registered(self):
        names = {fn.__name__ for fn in WorkerSettings.functions}
        self.assertIn("run_journal_dispatcher", names)
        self.assertIn("run_journal_ai_coach", names)


if __name__ == "__main__":
    unittest.main()
