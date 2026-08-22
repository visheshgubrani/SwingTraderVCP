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
    SCHEMA_VERSION,
    ProposalProviderError,
    build_proposal_vision_request,
    generate_trade_proposal_from_analysis,
    calculate_next_session_and_deadline,
    compute_proposal_hash,
    parse_proposal_openrouter_response,
)


def _ai_output(**overrides) -> GeminiVcpProposalOutput:
    payload = {
        "verdict": "valid",
        "prior_uptrend": "yes",
        "prior_uptrend_note": "Higher highs and higher lows above rising 50/150-day MAs.",
        "volume_dry_up": "yes",
        "volume_dry_up_note": "Volume dries up into the final contraction and pivot.",
        "contractions": [
            {
                "index": 1,
                "depth_pct": Decimal("18.0"),
                "high_price": Decimal("120.00"),
                "low_price": Decimal("98.40"),
            },
            {
                "index": 2,
                "depth_pct": Decimal("9.0"),
                "high_price": Decimal("118.00"),
                "low_price": Decimal("107.38"),
            },
        ],
        "entry_template": EntryTemplate.TWO_LEG,
        "red_flags": [],
        "evidence_summary": "Two nested contractions with volume dry-up into resistance.",
    }
    payload.update(overrides)
    return GeminiVcpProposalOutput.model_validate(payload)


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
        ai_output = _ai_output(
            pivot_price=self.pivot,
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
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
        self.assertEqual(proposal.proposal["confidence"], Decimal("0"))
        self.assertEqual(
            proposal.proposal["gemini_evidence"]["contractions"][0]["depth_pct"],
            "18.0",
        )
        self.assertEqual(proposal.proposal["gemini_evidence"]["prior_uptrend"], "yes")
        self.assertEqual(proposal.proposal["gemini_evidence"]["volume_dry_up"], "yes")
        pivot_grounding = proposal.proposal["geometry"]["pivot_grounding"]
        self.assertTrue(pivot_grounding["is_grounded"])
        self.assertEqual(
            pivot_grounding["selected_zone"]["low"],
            str(self.resistance_candle.high),
        )
        self.assertTrue(len(proposal.proposal["proposal_hash"]) == 64)
        self.assertEqual(proposal.proposal["schema_version"], SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, "gemini_vcp_proposal_output_v5")

    def test_generate_trade_proposal_accepts_structural_t2_t3_below_hard_rr(self):
        from app.domain.p10_geometry import derive_chart_geometry

        atr14 = compute_atr14(self.candles)
        chart = derive_chart_geometry(self.candles)
        stop = calculate_structural_stop(chart.final_contraction_low, atr14)
        base_chase, _ = calculate_chase_ceiling(self.pivot, stop)
        t1 = (self.pivot * 2) - stop
        t2 = t1 + Decimal("0.50")
        t3 = t1 + Decimal("1.00")
        ai_output = _ai_output(
            pivot_price=self.pivot,
            t1=t1,
            t2=t2,
            t3=t3,
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

    def test_generate_trade_proposal_rejects_invalid_verdict(self):
        ai_output = _ai_output(
            verdict="invalid",
            prior_uptrend="no",
            volume_dry_up="no",
            pivot_price=self.pivot,
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
            entry_template=EntryTemplate.SINGLE,
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
        self.assertEqual(proposal.rejection_code, "proposal_ai_invalid")

    def test_generate_trade_proposal_rejects_partial_verdict(self):
        ai_output = _ai_output(
            verdict="partial",
            pivot_price=self.pivot,
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
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
        self.assertEqual(result.rejection_code, "proposal_ai_partial")

    def test_generate_trade_proposal_rejects_missing_prior_uptrend(self):
        ai_output = _ai_output(
            prior_uptrend="no",
            prior_uptrend_note="No Stage 2 advance before the base.",
            pivot_price=self.pivot,
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
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
        self.assertEqual(result.rejection_code, "proposal_ai_no_prior_uptrend")

    def test_generate_trade_proposal_rejects_missing_volume_dry_up(self):
        ai_output = _ai_output(
            volume_dry_up="no",
            volume_dry_up_note="Volume expands into the lows.",
            pivot_price=self.pivot,
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
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
        self.assertEqual(result.rejection_code, "proposal_ai_no_volume_dry_up")

    def test_generate_trade_proposal_rejects_non_decreasing_contractions(self):
        ai_output = _ai_output(
            contractions=[
                {
                    "index": 1,
                    "depth_pct": Decimal("8.0"),
                    "high_price": Decimal("120.00"),
                    "low_price": Decimal("110.40"),
                },
                {
                    "index": 2,
                    "depth_pct": Decimal("12.0"),
                    "high_price": Decimal("118.00"),
                    "low_price": Decimal("103.84"),
                },
            ],
            pivot_price=self.pivot,
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
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
        self.assertEqual(result.rejection_code, "proposal_ai_contractions_not_tightening")

    def test_pivot_outside_every_recent_zone_has_machine_subreason(self):
        ai_output = _ai_output(
            pivot_price=self.pivot + Decimal("10"),
            t1=self.targets[0],
            t2=self.targets[1],
            t3=self.targets[2],
            entry_template=EntryTemplate.SINGLE,
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
        ai_output = _ai_output(
            pivot_price=Decimal("4831.7"),
            t1=Decimal("5325.0"),
            t2=Decimal("5700.0"),
            t3=Decimal("6100.0"),
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
        self.assertIsNone(
            result.rejection_details["geometry_inputs"]["calculated_chase_ceiling"]
        )


def _contains_key(node: object, key: str) -> bool:
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(value, key) for value in node)
    return False


class TestProposalVisionRequest(unittest.TestCase):
    def test_request_contains_grounding_and_provider_controls(self):
        request = build_proposal_vision_request(
            context_png_b64="context",
            detail_png_b64="detail",
            model="google/gemini-3.7-flash",
            tick_size=Decimal("0.05"),
        )
        content = request["messages"][1]["content"]
        user_text = content[0]["text"]
        self.assertNotIn("TESTSTOCK", user_text)
        self.assertNotIn("Canonical frozen OHLCV", user_text)
        self.assertNotIn("2026-08-18,100.00", user_text)
        self.assertIn("tick size is 0.05", user_text)
        system_prompt = request["messages"][0]["content"]
        self.assertIn("Do not invent session dates", system_prompt)
        self.assertIn("Do not set t1 at the pivot, the breakout tick, or the first nearby resistance", system_prompt)
        self.assertEqual(request["provider"], {"require_parameters": True, "data_collection": "deny"})
        self.assertEqual(request["reasoning"], {"effort": "high", "exclude": True})
        self.assertFalse(request["stream"])
        schema = request["response_format"]["json_schema"]["schema"]
        self.assertFalse(_contains_key(schema, "anyOf"))
        self.assertFalse(_contains_key(schema, "$ref"))
        self.assertNotIn("$defs", schema)
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("confidence", schema["properties"])
        self.assertNotIn("contradicts_scanner", schema["properties"])
        self.assertNotIn("contraction_anchors", schema["properties"])
        contraction_schema = schema["properties"]["contractions"]["items"]
        self.assertEqual(contraction_schema["type"], "object")
        self.assertEqual(contraction_schema["properties"]["high_price"]["type"], "number")
        self.assertEqual(contraction_schema["properties"]["high_price"]["exclusiveMinimum"], 0.0)
        self.assertFalse(contraction_schema["additionalProperties"])
        self.assertEqual(schema["properties"]["pivot_price"]["type"], "number")
        self.assertEqual(
            schema["properties"]["entry_template"]["enum"],
            ["single", "two_leg", "two_leg_staged", "three_leg_front", "three_leg_balanced"],
        )
        self.assertEqual(schema["properties"]["verdict"]["enum"], ["valid", "partial", "invalid"])
        self.assertEqual(
            set(schema["required"]),
            set(schema["properties"]),
        )


class TestParseProposalOpenRouterResponse(unittest.TestCase):
    def _payload(self, content: str | dict | list) -> dict:
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
            "prior_uptrend": "yes",
            "prior_uptrend_note": "Higher highs above rising 50/150-day MAs.",
            "volume_dry_up": "yes",
            "volume_dry_up_note": "Volume dries up into the final contraction.",
            "contractions": [
                {
                    "index": 1,
                    "depth_pct": "18.0",
                    "high_price": "120.0",
                    "low_price": "98.4",
                },
                {
                    "index": 2,
                    "depth_pct": "9.0",
                    "high_price": "118.0",
                    "low_price": "107.38",
                },
            ],
            "pivot_price": "112.0",
            "t1": "120.0",
            "t2": "128.0",
            "t3": "136.0",
            "entry_template": "two_leg",
            "red_flags": [],
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

    def test_parses_top_level_json_string_envelope(self) -> None:
        envelope = json.dumps(self._payload(json.dumps(self._valid_content())))
        output, usage, cost, request_id = parse_proposal_openrouter_response(envelope)
        self.assertEqual(output.verdict, "valid")
        self.assertEqual(request_id, "gen-proposal-1")
        self.assertEqual(cost, 0.012)
        self.assertEqual(usage["total_tokens"], 800)

    def test_parses_double_encoded_message_content(self) -> None:
        output, *_ = parse_proposal_openrouter_response(
            self._payload(json.dumps(json.dumps(self._valid_content()))),
        )
        self.assertEqual(output.verdict, "valid")
        self.assertEqual(output.entry_template, EntryTemplate.TWO_LEG)

    def test_parses_choice_that_is_proposal_json_string(self) -> None:
        output, *_ = parse_proposal_openrouter_response(
            {"choices": [json.dumps(self._valid_content())]},
        )
        self.assertEqual(output.verdict, "valid")
        self.assertEqual(output.entry_template, EntryTemplate.TWO_LEG)

    def test_parses_unwrapped_proposal_object(self) -> None:
        output, usage, cost, request_id = parse_proposal_openrouter_response(
            self._valid_content(),
        )
        self.assertEqual(output.verdict, "valid")
        self.assertEqual(usage, {})
        self.assertEqual(cost, 0.0)
        self.assertIsNone(request_id)

    def test_parses_message_parsed_object(self) -> None:
        payload = {
            "id": "gen-proposal-1",
            "usage": {"cost": 0.012, "total_tokens": 800},
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": None,
                        "parsed": self._valid_content(),
                    },
                }
            ],
        }
        output, usage, cost, request_id = parse_proposal_openrouter_response(payload)
        self.assertEqual(output.verdict, "valid")
        self.assertEqual(request_id, "gen-proposal-1")
        self.assertEqual(cost, 0.012)
        self.assertEqual(usage["total_tokens"], 800)

    def test_parses_multipart_json_part(self) -> None:
        payload = self._payload(
            [{"type": "output_json", "json": self._valid_content()}],
        )
        output, *_ = parse_proposal_openrouter_response(payload)
        self.assertEqual(output.verdict, "valid")

    def test_bad_payload_raises_typed_provider_error_not_attribute_error(self) -> None:
        with self.assertRaises(ProposalProviderError) as raised:
            parse_proposal_openrouter_response("not-json")
        self.assertEqual(
            raised.exception.error_type,
            "proposal_invalid_provider_json",
        )
        self.assertIsInstance(raised.exception.details, dict)

        with self.assertRaises(ProposalProviderError) as raised:
            parse_proposal_openrouter_response(["unexpected-list"])
        self.assertEqual(
            raised.exception.error_type,
            "proposal_invalid_provider_json",
        )
        self.assertIn("payload_type", raised.exception.details)

    def test_malformed_json_is_typed_provider_error(self) -> None:
        with self.assertRaises(ProposalProviderError) as raised:
            parse_proposal_openrouter_response(
                {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"summary":"cut off'},
                        }
                    ]
                }
            )
        self.assertEqual(
            raised.exception.error_type,
            "proposal_invalid_provider_json",
        )
        self.assertIn("finish_reason='length'", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
