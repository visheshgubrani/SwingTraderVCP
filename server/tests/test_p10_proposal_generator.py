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
    build_proposal_vision_request,
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
            renderer_version="p10_mplfinance_v3",
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
                    "price": Decimal(str(self.low_candle.low)) + Decimal("0.01"),
                    "anchor_type": "contraction_low",
                },
                {
                    "date": self.resistance_candle.date,
                    "price": Decimal(str(self.resistance_candle.high)) + Decimal("0.01"),
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

        self.assertTrue(proposal.accepted)
        self.assertEqual(proposal.proposal["symbol"], "TESTSTOCK")
        self.assertEqual(proposal.proposal["status"], "pending_approval")
        self.assertEqual(proposal.proposal["entry_template"], "two_leg")
        self.assertEqual(proposal.proposal["leg_count"], 2)
        self.assertEqual(proposal.proposal["leg_risk_allocations"], [0.60, 0.40])
        self.assertEqual(proposal.proposal["relative_volume_threshold"], Decimal("1.75"))
        self.assertEqual(
            proposal.proposal["gemini_evidence"]["contraction_anchors"][0]["price"],
            str(self.low_candle.low),
        )
        pivot_grounding = proposal.proposal["geometry"]["pivot_grounding"]
        self.assertTrue(pivot_grounding["is_grounded"])
        self.assertEqual(
            pivot_grounding["selected_zone"]["low"],
            str(self.resistance_candle.high),
        )
        self.assertTrue(len(proposal.proposal["proposal_hash"]) == 64)

    def test_generate_trade_proposal_accepts_structural_t2_t3_below_hard_rr(self):
        atr14 = compute_atr14(self.candles)
        stop = calculate_structural_stop(Decimal(str(self.low_candle.low)), atr14)
        base_chase, _ = calculate_chase_ceiling(self.pivot, stop)
        t1 = (self.pivot * 2) - stop
        t2 = t1 + Decimal("0.50")
        t3 = t1 + Decimal("1.00")
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
            t1=t1,
            t2=t2,
            t3=t3,
            entry_template=EntryTemplate.TWO_LEG,
            base_tightness="solid",
            dry_up_quality="drying_up",
            resistance_room="clear",
            evidence_summary="Structural targets with T2/T3 below historic 2R/3R gates.",
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

        self.assertTrue(proposal.accepted, proposal.rejection_message)
        self.assertEqual(proposal.proposal["chase_ceiling"], self.pivot)
        geometry = proposal.proposal["geometry"]
        self.assertEqual(Decimal(geometry["base_chase_ceiling"]), base_chase)
        self.assertEqual(Decimal(geometry["rr_adjusted_chase_ceiling"]), self.pivot)
        self.assertEqual(Decimal(geometry["t1_r"]), Decimal("1"))
        self.assertTrue(geometry["t2_below_2r"])
        self.assertTrue(geometry["t3_below_3r"])
        self.assertEqual(
            proposal.proposal["geometry_version"],
            "p10_geometry_rr_adjusted_chase_v4",
        )

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
        self.assertFalse(proposal.accepted)
        self.assertEqual(proposal.rejection_code, "proposal_ai_contradicts_scanner")

    def test_anchor_outside_tolerance_has_stable_diagnostic(self):
        tolerance = compute_atr14(self.candles) * Decimal("0.50")
        ai_output = GeminiVcpProposalOutput(
            verdict="valid",
            contradicts_scanner=False,
            confidence=0.8,
            contraction_anchors=[
                {
                    "date": self.low_candle.date,
                    "price": Decimal(str(self.low_candle.low)) + tolerance + Decimal("0.01"),
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
            base_tightness="solid",
            dry_up_quality="supportive",
            resistance_room="clear",
            evidence_summary="Grounded test output.",
        )
        result = generate_trade_proposal_from_analysis(
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
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_code, "proposal_anchor_price_out_of_tolerance")
        self.assertIn("contraction_low", result.rejection_message)
        self.assertIn(str(self.low_candle.low), result.rejection_message)
        self.assertIn(str(tolerance), result.rejection_message)

    def test_pivot_outside_every_recent_zone_has_machine_subreason(self):
        ai_output = GeminiVcpProposalOutput(
            verdict="valid",
            contradicts_scanner=False,
            confidence=0.8,
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
            pivot_price=self.pivot + Decimal("10"),
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
            entry_template=EntryTemplate.SINGLE,
            base_tightness="solid",
            dry_up_quality="supportive",
            resistance_room="clear",
            evidence_summary="Grounded anchors with an unsupported pivot.",
        )

        result = generate_trade_proposal_from_analysis(
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

        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_code, "proposal_pivot_not_anchored")
        self.assertEqual(
            result.rejection_details["subreason"],
            "outside_resistance_zone_tolerance",
        )
        self.assertEqual(
            result.rejection_details["pivot_grounding"]["selected_zone"]["low"],
            str(self.resistance_candle.high),
        )

    def test_unsupported_old_resistance_has_no_eligible_evidence(self):
        old_resistance = self.candles[-100]
        ai_output = GeminiVcpProposalOutput(
            verdict="valid",
            contradicts_scanner=False,
            confidence=0.8,
            contraction_anchors=[
                {
                    "date": old_resistance.date,
                    "price": old_resistance.high,
                    "anchor_type": "resistance",
                },
                {
                    "date": self.low_candle.date,
                    "price": self.low_candle.low,
                    "anchor_type": "contraction_low",
                },
            ],
            pivot_price=Decimal(str(old_resistance.high)),
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
            entry_template=EntryTemplate.SINGLE,
            base_tightness="solid",
            dry_up_quality="supportive",
            resistance_room="clear",
            evidence_summary="An old boundary without structural retest support.",
        )

        result = generate_trade_proposal_from_analysis(
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

        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_code, "proposal_pivot_not_anchored")
        self.assertEqual(
            result.rejection_details["subreason"],
            "no_eligible_resistance_evidence",
        )

    def test_data_patterns_shape_grounds_upper_zone_before_wide_stop_rejection(self):
        start = dt.date(2025, 12, 11)
        candles = [
            CandleData(
                open=4500.0,
                high=4550.0,
                low=4450.0,
                close=4500.0,
                volume=100000,
                date=(start + dt.timedelta(days=index)).isoformat(),
            )
            for index in range(252)
        ]

        def replace_extreme(index: int, *, high: float | None = None, low: float | None = None):
            candle = candles[index]
            candles[index] = CandleData(
                open=candle.open,
                high=high if high is not None else candle.high,
                low=low if low is not None else candle.low,
                close=candle.close,
                volume=candle.volume,
                date=candle.date,
            )

        replace_extreme(190, high=4955.9)
        replace_extreme(205, low=3899.0)
        replace_extreme(220, high=4831.7)
        replace_extreme(230, low=4090.0)
        replace_extreme(240, high=4625.0)
        replace_extreme(245, low=4271.8)
        ai_output = GeminiVcpProposalOutput(
            verdict="valid",
            contradicts_scanner=False,
            confidence=0.86,
            contraction_anchors=[
                {"date": candles[190].date, "price": "4955.9", "anchor_type": "resistance"},
                {"date": candles[205].date, "price": "3899.0", "anchor_type": "contraction_low"},
                {"date": candles[220].date, "price": "4831.7", "anchor_type": "contraction_high"},
                {"date": candles[230].date, "price": "4090.0", "anchor_type": "contraction_low"},
                {"date": candles[240].date, "price": "4625.0", "anchor_type": "contraction_high"},
                {"date": candles[245].date, "price": "4271.8", "anchor_type": "contraction_low"},
            ],
            pivot_price=Decimal("4831.7"),
            t1=Decimal("5325.0"),
            t2=Decimal("5700.0"),
            t3=Decimal("6100.0"),
            entry_template=EntryTemplate.TWO_LEG,
            base_tightness="solid",
            dry_up_quality="drying_up",
            resistance_room="clear",
            evidence_summary="Upper base resistance remains the breakout pivot.",
        )

        result = generate_trade_proposal_from_analysis(
            symbol="NSE:DATAPATTNS-EQ",
            as_of_date=dt.date.fromisoformat(candles[-1].date),
            screening_result_id="data-patterns-result",
            instrument_id="data-patterns-instrument",
            candles=candles,
            ai_output=ai_output,
            rendered_charts=self.charts,
            model="google/gemini-3.7-flash",
            approved_risk_budget_amount=Decimal("1000"),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_code, "proposal_geometry_invalid")
        self.assertIn("Stop distance", result.rejection_message)
        grounding = result.rejection_details["pivot_grounding"]
        self.assertTrue(grounding["is_grounded"])
        self.assertEqual(grounding["selected_zone"]["low"], "4831.7")
        self.assertEqual(
            result.rejection_details["geometry_inputs"]["final_contraction_low"],
            "4271.8",
        )
        self.assertIsNone(
            result.rejection_details["geometry_inputs"]["calculated_chase_ceiling"]
        )

    def test_buried_grounded_pivot_is_audit_flagged_not_rejected(self):
        lower_high = self.candles[-30]
        upper_high_one = self.candles[-2]
        upper_high_two = self.candles[-1]
        pivot = Decimal(str(lower_high.high))
        ai_output = GeminiVcpProposalOutput(
            verdict="valid",
            contradicts_scanner=False,
            confidence=0.82,
            contraction_anchors=[
                {
                    "date": lower_high.date,
                    "price": lower_high.high,
                    "anchor_type": "resistance",
                },
                {
                    "date": self.low_candle.date,
                    "price": self.low_candle.low,
                    "anchor_type": "contraction_low",
                },
                {
                    "date": upper_high_one.date,
                    "price": upper_high_one.high,
                    "anchor_type": "contraction_high",
                },
                {
                    "date": upper_high_two.date,
                    "price": upper_high_two.high,
                    "anchor_type": "contraction_high",
                },
            ],
            pivot_price=pivot,
            t1=Decimal("124.50"),
            t2=Decimal("126.00"),
            t3=Decimal("128.00"),
            entry_template=EntryTemplate.TWO_LEG,
            base_tightness="solid",
            dry_up_quality="drying_up",
            resistance_room="moderate",
            evidence_summary="Grounded internal pivot with repeated overhead evidence.",
        )

        result = generate_trade_proposal_from_analysis(
            symbol="TESTSTOCK",
            as_of_date=dt.date(2026, 8, 17),
            screening_result_id="res-buried",
            instrument_id="inst-123",
            candles=self.candles,
            ai_output=ai_output,
            rendered_charts=self.charts,
            model="google/gemini-3.7-flash",
            approved_risk_budget_amount=Decimal("1000"),
            generated_at=dt.datetime(2026, 8, 18, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        )

        self.assertTrue(result.accepted)
        grounding = result.proposal["geometry"]["pivot_grounding"]
        self.assertIn(
            "pivot_below_material_overhead_zone",
            grounding["audit_flags"],
        )
        self.assertGreater(len(grounding["higher_zones"]), 0)


class TestProposalVisionRequest(unittest.TestCase):
    def test_request_contains_grounding_and_provider_controls(self):
        candles = [
            CandleData(
                open=100.0, high=105.0, low=95.0, close=102.0,
                volume=1000, date="2026-08-18",
            )
        ]
        request = build_proposal_vision_request(
            symbol="TESTSTOCK",
            context_png_b64="context",
            detail_png_b64="detail",
            candles=candles,
            model="google/gemini-3.7-flash",
            tick_size=Decimal("0.05"),
        )
        content = request["messages"][1]["content"]
        user_text = content[0]["text"]
        self.assertIn("2026-08-18,100.00,105.00,95.00,102.00,1000,1.0", user_text)
        system_prompt = request["messages"][0]["content"]
        self.assertIn("contraction_low.price", system_prompt)
        self.assertIn("measured-move / prior-swing / overhead-room", system_prompt)
        self.assertIn("Do not set t1 at the pivot, the breakout tick, or the first nearby resistance", system_prompt)
        self.assertIn("t1 < t2 < t3", system_prompt)
        self.assertEqual(request["provider"], {"require_parameters": True, "data_collection": "deny"})
        self.assertEqual(request["reasoning"], {"effort": "high", "exclude": True})
        self.assertFalse(request["stream"])


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
