import datetime as dt
import json
import unittest
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.domain.p10_geometry import (
    CandleData,
    calculate_chase_ceiling,
    calculate_structural_stop,
    compute_atr14,
)
from app.domain.p10_sizing import EntryTemplate
from app.schemas.proposals import GeminiVcpProposalOutput
from app.services.proposal_renderer import RenderedProposalCharts
from app.services.proposal_generator import (
    generate_trade_proposal_from_analysis,
    calculate_next_session_and_deadline,
    compute_proposal_hash,
    parse_proposal_openrouter_response,
)


class TestProposalGenerator(unittest.TestCase):
    def setUp(self):
        # A complete, frozen 252-session packet with tick-aligned prices.
        self.candles = []
        start = dt.date(2026, 8, 17) - dt.timedelta(days=251)
        for i in range(252):
            price = 100.0 + (i * 0.10)
            self.candles.append(CandleData(
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + 0.25,
                volume=100000,
                date=(start + dt.timedelta(days=i)).isoformat(),
            ))

        self.low_candle = self.candles[-10]
        self.resistance_candle = self.candles[-1]
        self.pivot = Decimal(str(self.resistance_candle.high))
        atr14 = compute_atr14(self.candles)
        stop = calculate_structural_stop(Decimal(str(self.low_candle.low)), atr14)
        chase, _ = calculate_chase_ceiling(self.pivot, stop)
        worst_r = chase - stop
        self.targets = (
            chase + worst_r,
            chase + Decimal("2") * worst_r,
            chase + Decimal("3") * worst_r,
        )

        self.charts = RenderedProposalCharts(
            renderer_version="p10_mplfinance_v2",
            context_png=b"context",
            context_hash="context_hash_123",
            detail_png=b"detail",
            detail_hash="detail_hash_123",
        )

    def test_calculate_next_session_and_deadline_weekday(self):
        # Monday -> Tuesday 09:00 IST
        monday = dt.date(2026, 8, 17)
        next_sess, deadline = calculate_next_session_and_deadline(monday)
        self.assertEqual(next_sess, dt.date(2026, 8, 18))
        self.assertEqual(deadline.hour, 9)
        self.assertEqual(deadline.minute, 0)

    def test_calculate_next_session_and_deadline_friday(self):
        # Friday -> Monday 09:00 IST
        friday = dt.date(2026, 8, 21)
        next_sess, deadline = calculate_next_session_and_deadline(friday)
        self.assertEqual(next_sess, dt.date(2026, 8, 24))
        self.assertEqual(deadline.hour, 9)
        self.assertEqual(deadline.minute, 0)

    def test_generate_trade_proposal_valid(self):
        ai_output = GeminiVcpProposalOutput(
            verdict="valid",
            contradicts_scanner=False,
            confidence=0.85,
            contraction_anchors=[
                {
                    "date": self.low_candle.date,
                    "price": self.low_candle.low,
                    "anchor_type": "contraction_low",
                },
                {
                    "date": self.resistance_candle.date,
                    "price": self.resistance_candle.high,
                    "anchor_type": "resistance",
                },
            ],
            pivot_price=self.pivot,
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
            entry_template=EntryTemplate.TWO_LEG,
            base_tightness="solid",
            dry_up_quality="drying_up",
            resistance_room="clear",
            evidence_summary="Coherent 2-contraction VCP pattern with volume dry-up.",
        )

        proposal = generate_trade_proposal_from_analysis(
            symbol="TESTSTOCK",
            as_of_date=dt.date(2026, 8, 17),
            screening_result_id="res-123",
            instrument_id="inst-123",
            candles=self.candles,
            ai_output=ai_output,
            rendered_charts=self.charts,
            model="google/gemini-3.7-flash",
            approved_risk_budget_amount=Decimal("1000"),
            generated_at=dt.datetime(2026, 8, 18, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        )

        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["symbol"], "TESTSTOCK")
        self.assertEqual(proposal["status"], "pending_approval")
        self.assertEqual(proposal["entry_template"], "two_leg")
        self.assertEqual(proposal["leg_count"], 2)
        self.assertEqual(proposal["leg_risk_allocations"], [0.60, 0.40])
        self.assertEqual(proposal["relative_volume_threshold"], Decimal("1.75"))
        self.assertTrue(len(proposal["proposal_hash"]) == 64)

    def test_generate_trade_proposal_rejects_contradicting_or_invalid(self):
        ai_output = GeminiVcpProposalOutput(
            verdict="invalid",
            contradicts_scanner=True,
            confidence=0.40,
            contraction_anchors=[
                {
                    "date": self.low_candle.date,
                    "price": self.low_candle.low,
                    "anchor_type": "contraction_low",
                },
                {
                    "date": self.resistance_candle.date,
                    "price": self.resistance_candle.high,
                    "anchor_type": "resistance",
                },
            ],
            pivot_price=self.pivot,
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
            entry_template=EntryTemplate.SINGLE,
            base_tightness="loose",
            dry_up_quality="weak",
            resistance_room="congested",
            evidence_summary="Erratic wide swings.",
        )

        proposal = generate_trade_proposal_from_analysis(
            symbol="TESTSTOCK",
            as_of_date=dt.date(2026, 8, 17),
            screening_result_id="res-123",
            instrument_id="inst-123",
            candles=self.candles,
            ai_output=ai_output,
            rendered_charts=self.charts,
            model="google/gemini-3.7-flash",
            approved_risk_budget_amount=Decimal("1000"),
        )
        self.assertIsNone(proposal)


class TestParseProposalOpenRouterResponse(unittest.TestCase):
    def _payload(self, content: str | dict) -> dict:
        return {
            "id": "gen-proposal-1",
            "usage": {"cost": 0.012, "total_tokens": 800},
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": content},
                }
            ],
        }

    def _valid_content(self) -> dict:
        return {
            "verdict": "valid",
            "contradicts_scanner": False,
            "confidence": 0.81,
            "contraction_anchors": [
                {
                    "date": "2026-08-10",
                    "price": "100.5",
                    "anchor_type": "contraction_low",
                },
                {
                    "date": "2026-08-17",
                    "price": "112.0",
                    "anchor_type": "resistance",
                },
            ],
            "pivot_price": "112.0",
            "t1": "120.0",
            "t2": "128.0",
            "t3": "136.0",
            "entry_template": "two_leg",
            "base_tightness": "solid",
            "dry_up_quality": "drying_up",
            "resistance_room": "clear",
            "evidence_summary": "Two nested contractions with volume dry-up into resistance.",
        }

    def test_parses_full_choice_object(self) -> None:
        output, usage, cost, request_id = parse_proposal_openrouter_response(
            self._payload(json.dumps(self._valid_content())),
        )
        self.assertEqual(output.verdict, "valid")
        self.assertEqual(output.entry_template, EntryTemplate.TWO_LEG)
        self.assertEqual(request_id, "gen-proposal-1")
        self.assertEqual(cost, 0.012)
        self.assertEqual(usage["total_tokens"], 800)

    def test_rejects_message_content_passed_as_choice(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no proposal choice"):
            parse_proposal_openrouter_response(
                {"choices": [json.dumps(self._valid_content())]},
            )


if __name__ == "__main__":
    unittest.main()
