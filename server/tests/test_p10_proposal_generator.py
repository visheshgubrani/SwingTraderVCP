import datetime as dt
import json
import unittest
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.domain.p10_geometry import CandleData, VcpContractionWave
from app.schemas.proposals import GeminiBaseQuality, GeminiVcpProposalOutput
from app.services.proposal_renderer import RenderedProposalCharts
from app.services.proposal_generator import (
    GEOMETRY_VERSION,
    SCHEMA_VERSION,
    ProposalProviderError,
    build_proposal_vision_request,
    compute_proposal_hash,
    generate_trade_proposal_from_analysis,
    calculate_next_session_and_deadline,
    parse_proposal_openrouter_response,
)


def _ai_output(candidate_count: int = 2, **overrides) -> GeminiVcpProposalOutput:
    assessments = [
        {"index": index, "action": "confirm", "note": f"Confirms candidate {index}."}
        for index in range(1, candidate_count + 1)
    ]
    payload = {
        "classification": "valid",
        "progressive_tightening": "yes",
        "volume_dry_up": "clearly",
        "base_quality": {
            "price_action": "orderly",
            "climax_or_gap_violation": "no",
            "stage2_context": "yes",
        },
        "pattern_type": "vcp",
        "primary_reason": "mature_vcp",
        "candidate_assessments": assessments,
        "extra_windows": [],
        "confidence": 72,
        "red_flags": [],
        "evidence_summary": "Two nested contractions with volume dry-up into resistance.",
    }
    payload.update(overrides)
    classification = payload["classification"]
    if "pattern_type" not in overrides:
        payload["pattern_type"] = (
            "vcp" if classification in {"valid", "forming"} else "high_tight_shelf"
        )
    if "primary_reason" not in overrides:
        payload["primary_reason"] = {
            "valid": "mature_vcp",
            "forming": "immature_base",
            "not_vcp": "flat_or_high_tight_shelf",
        }[classification]
    return GeminiVcpProposalOutput.model_validate(payload)


class TestProposalGenerator(unittest.TestCase):
    def test_trigger_policy_version_is_part_of_proposal_hash(self):
        plan = {
            "symbol": "NSE:TEST-EQ",
            "entry_trigger_policy_version": "cumulative_two_bar_v1",
        }
        old_hash = compute_proposal_hash(plan)
        plan["entry_trigger_policy_version"] = "breakout_bar_signal_v2"
        self.assertNotEqual(old_hash, compute_proposal_hash(plan))

    def setUp(self):
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
        c1_high, c1_low = self.candles[-80], self.candles[-70]
        c2_high, c2_low = self.candles[-20], self.candles[-12]
        c3_high, c3_low = self.candles[-60], self.candles[-55]
        c4_high, c4_low = self.candles[-10], self.candles[-6]
        self.python_candidates = (
            VcpContractionWave(
                index=1,
                high_date=c1_high.date,
                high_price=Decimal("120.00"),
                low_date=c1_low.date,
                low_price=Decimal("100.00"),
                depth_pct=Decimal("16.67"),
                vol_adv20=Decimal("1.20"),
                vol_adv50=Decimal("1.10"),
            ),
            VcpContractionWave(
                index=2,
                high_date=c2_high.date,
                high_price=Decimal("124.00"),
                low_date=c2_low.date,
                low_price=Decimal("115.00"),
                depth_pct=Decimal("7.26"),
                vol_adv20=Decimal("0.70"),
                vol_adv50=Decimal("0.80"),
            ),
            VcpContractionWave(
                index=3,
                high_date=c3_high.date,
                high_price=Decimal("119.00"),
                low_date=c3_low.date,
                low_price=Decimal("114.00"),
                depth_pct=Decimal("4.20"),
                vol_adv20=Decimal("1.00"),
                vol_adv50=Decimal("1.00"),
            ),
            VcpContractionWave(
                index=4,
                high_date=c4_high.date,
                high_price=Decimal("125.00"),
                low_date=c4_low.date,
                low_price=Decimal("121.00"),
                depth_pct=Decimal("3.20"),
                vol_adv20=Decimal("0.50"),
                vol_adv50=Decimal("0.60"),
            ),
        )
        self.pair_candidates = self.python_candidates[:2]
        self.charts = RenderedProposalCharts(
            renderer_version="p10_mplfinance_v4",
            context_png=b"context",
            context_hash="context_hash_123",
            detail_png=b"detail",
            detail_hash="detail_hash_123",
        )

    def _generate(self, ai_output, **kwargs):
        return generate_trade_proposal_from_analysis(
            symbol="TESTSTOCK",
            as_of_date=dt.date(2026, 8, 17),
            screening_result_id="res-123",
            instrument_id="inst-123",
            candles=self.candles,
            ai_output=ai_output,
            rendered_charts=self.charts,
            model="google/gemini-3.7-flash",
            approved_risk_budget_amount=Decimal("1000"),
            python_candidates=kwargs.pop("python_candidates", self.pair_candidates),
            **kwargs,
        )

    def test_calculate_next_session_and_deadline_weekday(self):
        monday = dt.date(2026, 8, 17)
        next_sess, deadline = calculate_next_session_and_deadline(monday)
        self.assertEqual(next_sess, dt.date(2026, 8, 18))
        self.assertEqual(deadline.hour, 9)
        self.assertEqual(deadline.minute, 0)

    def test_calculate_next_session_and_deadline_friday(self):
        friday = dt.date(2026, 8, 21)
        next_sess, deadline = calculate_next_session_and_deadline(friday)
        self.assertEqual(next_sess, dt.date(2026, 8, 24))
        self.assertEqual(deadline.hour, 9)
        self.assertEqual(deadline.minute, 0)

    def test_generate_trade_proposal_valid(self):
        proposal = self._generate(
            _ai_output(),
            generated_at=dt.datetime(2026, 8, 18, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        )
        self.assertTrue(proposal.accepted, proposal.rejection_message)
        locked = proposal.proposal
        self.assertEqual(locked["symbol"], "TESTSTOCK")
        self.assertEqual(locked["status"], "pending_approval")
        self.assertIn(locked["entry_template"], {"single", "two_leg", "three_leg_front"})
        self.assertEqual(locked["confidence"], Decimal("72"))
        self.assertEqual(locked["gemini_evidence"]["classification"], "valid")
        self.assertEqual(locked["gemini_evidence"]["volume_dry_up"], "clearly")
        self.assertEqual(locked["gemini_evidence"]["llm_count"], 2)
        self.assertEqual(locked["gemini_evidence"]["python_count"], 2)
        self.assertEqual(locked["pivot_price"], Decimal("124.00"))
        self.assertEqual(locked["geometry"]["planned_entry"], str(locked["geometry"]["calculation_basis"]["entry_chase"]["planned_entry"]))
        self.assertEqual(len(locked["proposal_hash"]), 64)
        self.assertEqual(locked["schema_version"], SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, "gemini_vcp_proposal_output_v7")
        self.assertEqual(locked["geometry_version"], GEOMETRY_VERSION)
        self.assertEqual(
            locked["entry_trigger_policy_version"],
            "balanced_breakout_v3",
        )
        self.assertEqual(GEOMETRY_VERSION, "p10_python_owned_levels_v6")
        self.assertEqual(len(locked["geometry"]["target_slots"]), 3)

    def test_proposal_generated_after_d1_deadline_starts_expired(self):
        proposal = self._generate(
            _ai_output(),
            generated_at=dt.datetime(
                2026, 8, 18, 9, 1, tzinfo=ZoneInfo("Asia/Kolkata")
            ),
        )

        self.assertTrue(proposal.accepted, proposal.rejection_message)
        self.assertEqual(proposal.proposal["status"], "expired_unapproved")
        self.assertFalse(proposal.proposal["live_eligible"])

    def test_generate_trade_proposal_rejects_not_vcp(self):
        result = self._generate(_ai_output(classification="not_vcp"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_code, "proposal_ai_invalid")

    def test_generate_trade_proposal_forming_does_not_compute_prices(self):
        result = self._generate(
            _ai_output(classification="forming", forming_state="developing"),
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_code, "proposal_ai_forming")
        self.assertNotIn("pivot_price", result.rejection_details)

    def test_generate_trade_proposal_rejects_stage2_no(self):
        # Schema v7 rejects contradictory `valid` payloads outright, so the
        # generator defence is exercised by mutating a validated output the
        # way no compliant provider packet can.
        ai = _ai_output()
        ai.base_quality = GeminiBaseQuality(
            price_action="orderly",
            climax_or_gap_violation="no",
            stage2_context="no",
        )
        result = self._generate(ai)
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_code, "proposal_numeric_gate_failed")
        self.assertIn("stage2_context_no", result.rejection_details["failures"])

    def test_generate_trade_proposal_rejects_volume_not_really(self):
        ai = _ai_output()
        ai.volume_dry_up = "not_really"
        result = self._generate(ai)
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_code, "proposal_numeric_gate_failed")
        self.assertIn("volume_dry_up_not_really", result.rejection_details["failures"])

    def test_count_disagreement_forces_single_and_banner(self):
        result = self._generate(
            _ai_output(
                candidate_count=4,
                candidate_assessments=[
                    {"index": 1, "action": "confirm", "note": "Keep first contraction."},
                    {"index": 2, "action": "confirm", "note": "Keep final contraction."},
                    {"index": 3, "action": "reject", "note": "Noise swing in the middle."},
                    {"index": 4, "action": "reject", "note": "Late noise, not a contraction."},
                ],
            ),
            python_candidates=self.python_candidates,
        )
        self.assertTrue(result.accepted, result.rejection_message)
        self.assertEqual(result.proposal["entry_template"], "single")
        self.assertTrue(result.proposal["gemini_evidence"]["mismatch_banner"])
        self.assertEqual(result.proposal["gemini_evidence"]["python_count"], 4)
        self.assertEqual(result.proposal["gemini_evidence"]["llm_count"], 2)


def _contains_key(node: object, key: str) -> bool:
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(value, key) for value in node)
    return False


class TestProposalVisionRequest(unittest.TestCase):
    def test_request_contains_one_image_and_candidate_summary(self):
        request = build_proposal_vision_request(
            detail_png_b64="detail",
            model="google/gemini-3.7-flash",
            tick_size=Decimal("0.05"),
            candidate_summary="1. 2026-06-01 high / 2026-06-10 low, 18.0% depth",
        )
        content = request["messages"][1]["content"]
        user_text = content[0]["text"]
        self.assertNotIn("TESTSTOCK", user_text)
        self.assertNotIn("Canonical frozen OHLCV", user_text)
        image_parts = [part for part in content if part.get("type") == "image_url"]
        self.assertEqual(len(image_parts), 1)
        system_prompt = request["messages"][0]["content"]
        self.assertIn("Do NOT output a pivot, stop, target, entry", system_prompt)
        self.assertEqual(request["plugins"], [{"id": "response-healing"}])
        self.assertEqual(request["provider"], {"require_parameters": True, "data_collection": "deny"})
        self.assertFalse(request["stream"])
        schema = request["response_format"]["json_schema"]["schema"]
        self.assertFalse(_contains_key(schema, "anyOf"))
        self.assertFalse(_contains_key(schema, "$ref"))
        self.assertNotIn("$defs", schema)
        self.assertFalse(schema["additionalProperties"])
        for forbidden in (
            "pivot_price",
            "t1",
            "t2",
            "t3",
            "entry_template",
            "contraction_count",
            "verdict",
            "stop",
            "quantity",
        ):
            self.assertNotIn(forbidden, schema["properties"])
        self.assertIn("confidence", schema["properties"])
        self.assertIn("candidate_assessments", schema["properties"])
        self.assertIn("extra_windows", schema["properties"])
        self.assertEqual(
            schema["properties"]["classification"]["enum"],
            ["valid", "forming", "not_vcp"],
        )
        self.assertEqual(
            set(schema["required"]),
            set(schema["properties"]),
        )

    def test_schema_rejects_prices_and_free_contraction_count(self):
        with self.assertRaises(ValidationError):
            GeminiVcpProposalOutput.model_validate({
                **_ai_output().model_dump(mode="json"),
                "pivot_price": 112.0,
            })
        with self.assertRaises(ValidationError):
            GeminiVcpProposalOutput.model_validate({
                **_ai_output().model_dump(mode="json"),
                "contraction_count": 2,
            })
        with self.assertRaises(ValidationError):
            GeminiVcpProposalOutput.model_validate({
                **_ai_output().model_dump(mode="json"),
                "entry_template": "two_leg",
            })

    def test_merge_requires_merge_with_index(self):
        with self.assertRaises(ValidationError):
            GeminiVcpProposalOutput.model_validate({
                **_ai_output().model_dump(mode="json"),
                "candidate_assessments": [
                    {"index": 1, "action": "merge", "note": "Same swing as 2."},
                    {"index": 2, "action": "confirm", "note": "Keep this window."},
                ],
            })


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
            "classification": "valid",
            "progressive_tightening": "yes",
            "volume_dry_up": "clearly",
            "base_quality": {
                "price_action": "orderly",
                "climax_or_gap_violation": "no",
                "stage2_context": "yes",
            },
            "pattern_type": "vcp",
            "primary_reason": "mature_vcp",
            "candidate_assessments": [
                {"index": 1, "action": "confirm", "note": "First pullback matches."},
                {"index": 2, "action": "confirm", "note": "Final contraction matches."},
            ],
            "extra_windows": [],
            "confidence": 72,
            "red_flags": [],
            "evidence_summary": "Two nested contractions with volume dry-up into resistance.",
        }

    def test_parses_full_choice_object(self) -> None:
        output, usage, cost, request_id = parse_proposal_openrouter_response(
            self._payload(json.dumps(self._valid_content())),
        )
        self.assertEqual(output.classification, "valid")
        self.assertEqual(output.confidence, 72)
        self.assertEqual(request_id, "gen-proposal-1")
        self.assertEqual(cost, 0.012)
        self.assertEqual(usage["total_tokens"], 800)

    def test_parses_top_level_json_string_envelope(self) -> None:
        envelope = json.dumps(self._payload(json.dumps(self._valid_content())))
        output, usage, cost, request_id = parse_proposal_openrouter_response(envelope)
        self.assertEqual(output.classification, "valid")
        self.assertEqual(request_id, "gen-proposal-1")
        self.assertEqual(cost, 0.012)
        self.assertEqual(usage["total_tokens"], 800)

    def test_parses_double_encoded_message_content(self) -> None:
        output, *_ = parse_proposal_openrouter_response(
            self._payload(json.dumps(json.dumps(self._valid_content()))),
        )
        self.assertEqual(output.classification, "valid")

    def test_parses_choice_that_is_proposal_json_string(self) -> None:
        output, *_ = parse_proposal_openrouter_response(
            {"choices": [json.dumps(self._valid_content())]},
        )
        self.assertEqual(output.classification, "valid")

    def test_parses_unwrapped_proposal_object(self) -> None:
        output, usage, cost, request_id = parse_proposal_openrouter_response(
            self._valid_content(),
        )
        self.assertEqual(output.classification, "valid")
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
        self.assertEqual(output.classification, "valid")
        self.assertEqual(request_id, "gen-proposal-1")
        self.assertEqual(cost, 0.012)
        self.assertEqual(usage["total_tokens"], 800)

    def test_parses_multipart_json_part(self) -> None:
        payload = self._payload(
            [{"type": "output_json", "json": self._valid_content()}],
        )
        output, *_ = parse_proposal_openrouter_response(payload)
        self.assertEqual(output.classification, "valid")

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

    def test_openrouter_top_level_error_payload_raises_clear_provider_error(self) -> None:
        error_payload = {
            "error": {
                "code": 504,
                "message": "The operation was aborted",
            }
        }
        with self.assertRaises(ProposalProviderError) as raised:
            parse_proposal_openrouter_response(error_payload)
        self.assertIn("The operation was aborted", str(raised.exception))
        self.assertIn("code=504", str(raised.exception))
        self.assertEqual(raised.exception.details.get("error_code"), 504)

    def test_openrouter_choice_level_error_payload_raises_clear_provider_error(self) -> None:
        error_payload = {
            "choices": [
                {
                    "error": {
                        "code": 502,
                        "message": "Bad gateway upstream",
                    }
                }
            ]
        }
        with self.assertRaises(ProposalProviderError) as raised:
            parse_proposal_openrouter_response(error_payload)
        self.assertIn("Bad gateway upstream", str(raised.exception))
        self.assertIn("code=502", str(raised.exception))
        self.assertEqual(raised.exception.details.get("error_code"), 502)


if __name__ == "__main__":
    unittest.main()
