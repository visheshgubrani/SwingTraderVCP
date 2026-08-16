import datetime as dt
import unittest

from app.services.vcp_vision import (
    MAX_DATE_DRIFT_DAYS,
    VcpContractionAnchor,
    VcpPivotZone,
    VcpPriorUptrend,
    VcpVolumeAssessment,
    VcpVisionResultV1,
    VisionSchemaError,
    derive_contraction_metrics,
    enrich_stored_result,
    snap_to_nearest_trading_date,
    validate_and_snap_result,
)
from app.services.openrouter_content import parse_openrouter_structured_content

TRADING_DATES = {
    dt.date(2025, 1, 6),
    dt.date(2025, 1, 7),
    dt.date(2025, 1, 8),
    dt.date(2025, 1, 9),
    dt.date(2025, 1, 10),
    dt.date(2025, 1, 13),
    dt.date(2025, 1, 14),
    dt.date(2025, 1, 15),
}


def frozen_candles():
    from app.services.vcp_vision import FrozenCandle

    return {
        date: FrozenCandle(
            date=date,
            open=100.0,
            high=105.0 + index * 2,
            low=95.0 - index,
            close=102.0,
            volume=100_000,
        )
        for index, date in enumerate(sorted(TRADING_DATES))
    }


def valid_result() -> VcpVisionResultV1:
    return VcpVisionResultV1(
        verdict="valid",
        confidence=80,
        summary="Coherent nested contractions on strong prior uptrend.",
        prior_uptrend=VcpPriorUptrend(
            assessment="clear",
            note="Higher highs and lows through December.",
        ),
        volume=VcpVolumeAssessment(
            assessment="drying_up",
            note="Volume contracts through the base.",
        ),
        bases=[
            {
                "start": "2025-01-06",
                "end": "2025-01-10",
                "quality": "solid",
                "notes": "Tight base.",
            }
        ],
        contraction_anchors=[
            VcpContractionAnchor(date="2025-01-07", evidence="Swing peak."),
            VcpContractionAnchor(date="2025-01-09", evidence="Nested peak."),
            VcpContractionAnchor(date="2025-01-13", evidence="Final peak."),
        ],
        pivot_zone=VcpPivotZone(
            start="2025-01-13",
            end="2025-01-15",
            rationale="Pivot above the final contraction.",
        ),
        supporting_evidence=["Volume dried up on the last pullback."],
        contrary_evidence=["News-driven gap."],
        human_review_focus=["Confirm pivot holds."],
    )


class SnapTests(unittest.TestCase):
    def test_exact_date_returns_unchanged(self) -> None:
        self.assertEqual(
            snap_to_nearest_trading_date(dt.date(2025, 1, 8), TRADING_DATES),
            dt.date(2025, 1, 8),
        )

    def test_weekend_snaps_within_drift(self) -> None:
        self.assertEqual(
            snap_to_nearest_trading_date(dt.date(2025, 1, 11), TRADING_DATES),
            dt.date(2025, 1, 10),
        )
        self.assertEqual(
            snap_to_nearest_trading_date(dt.date(2025, 1, 12), TRADING_DATES),
            dt.date(2025, 1, 13),
        )

    def test_outside_drift_returns_none(self) -> None:
        self.assertIsNone(
            snap_to_nearest_trading_date(dt.date(2025, 1, 20), TRADING_DATES)
        )
        self.assertIsNone(
            snap_to_nearest_trading_date(
                dt.date(2025, 1, 20),
                TRADING_DATES,
                max_drift_days=2,
            )
        )

    def test_drift_constant_used_by_parse(self) -> None:
        self.assertEqual(MAX_DATE_DRIFT_DAYS, 3)


class VisionTokenBudgetTests(unittest.TestCase):
    def test_vision_max_tokens_leaves_room_after_gemini_thinking(self) -> None:
        from annotated_types import Ge, Le

        from app.config import Settings

        field = Settings.model_fields["vcp_vision_max_tokens"]
        self.assertGreaterEqual(field.default, 8192)
        constraints = {type(item): item for item in field.metadata}
        self.assertEqual(constraints[Ge].ge, 512)
        self.assertGreaterEqual(constraints[Le].le, 16384)


class ValidateAndSnapTests(unittest.TestCase):
    def test_malformed_structured_json_reports_truncation_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "finish_reason='length'") as raised:
            parse_openrouter_structured_content(
                {
                    "finish_reason": "length",
                    "message": {"content": '{"summary":"cut off'},
                },
                usage={
                    "completion_tokens": 2386,
                    "completion_tokens_details": {"reasoning_tokens": 2305},
                },
            )
        self.assertIn("reasoning_tokens=2305", str(raised.exception))

    def test_provider_schema_requires_every_declared_field(self) -> None:
        from app.services.vcp_vision import OpenRouterVisionClient

        schema = OpenRouterVisionClient._response_schema()
        self.assertEqual(
            set(schema["required"]),
            set(schema["properties"]),
        )
        for definition in schema["$defs"].values():
            if definition.get("type") == "object":
                self.assertEqual(
                    set(definition["required"]),
                    set(definition["properties"]),
                )
                self.assertFalse(definition["additionalProperties"])
        self.assertNotIn("default", schema["properties"]["schema_version"])
        self.assertEqual(
            schema["properties"]["schema_version"]["enum"],
            ["vcp_visual_validator_result_v1"],
        )
        self.assertIn(
            "three peaks",
            schema["properties"]["contraction_anchors"]["description"],
        )
        self.assertEqual(schema["properties"]["contraction_anchors"]["minItems"], 2)

    def test_snaps_anchors_and_derives_after_enrichment(self) -> None:
        cleaned = validate_and_snap_result(valid_result(), TRADING_DATES)
        self.assertEqual(
            [anchor["date"] for anchor in cleaned["contraction_anchors"]],
            ["2025-01-07", "2025-01-09", "2025-01-13"],
        )
        self.assertEqual(cleaned["bases"][0]["start"], "2025-01-06")
        self.assertEqual(
            cleaned["pivot_zone"]["end"], "2025-01-15"
        )
        self.assertNotIn("derived", cleaned)

        stored = enrich_stored_result(cleaned, frozen_candles())
        contractions = stored["derived"]["contractions"]
        self.assertEqual(len(contractions), 2)
        self.assertEqual(contractions[0]["label"], "C1")
        self.assertGreater(contractions[0]["high"], contractions[0]["low"])
        self.assertGreater(contractions[0]["sessions"], 0)
        self.assertAlmostEqual(stored["derived"]["pivot_price"], 119.0)

    def test_two_start_peaks_plus_later_pivot_is_valid(self) -> None:
        result = valid_result()
        result.contraction_anchors = result.contraction_anchors[:2]
        cleaned = validate_and_snap_result(result, TRADING_DATES)
        self.assertEqual(cleaned["verdict"], "valid")
        stored = enrich_stored_result(cleaned, frozen_candles())
        self.assertEqual(
            [(item["start"], item["end"]) for item in stored["derived"]["contractions"]],
            [("2025-01-07", "2025-01-09"), ("2025-01-09", "2025-01-13")],
        )

    def test_underspecified_valid_is_stored_as_uncertain(self) -> None:
        result = valid_result()
        result.contraction_anchors = [
            VcpContractionAnchor(date="2025-01-13", evidence="Late peak."),
            VcpContractionAnchor(date="2025-01-15", evidence="Later peak."),
        ]
        result.pivot_zone = VcpPivotZone(
            start="2025-01-13",
            end="2025-01-15",
            rationale="Pivot does not extend past the last peak.",
        )
        cleaned = validate_and_snap_result(result, TRADING_DATES)
        self.assertEqual(cleaned["verdict"], "uncertain")
        self.assertTrue(
            any("two contraction windows" in note for note in cleaned["human_review_focus"])
        )

    def test_anchor_dates_must_be_strictly_ordered(self) -> None:
        result = valid_result()
        result.contraction_anchors = [
            VcpContractionAnchor(date="2025-01-09", evidence="a"),
            VcpContractionAnchor(date="2025-01-07", evidence="b"),
        ]
        with self.assertRaises(VisionSchemaError):
            validate_and_snap_result(result, TRADING_DATES)

    def test_valid_verdict_requires_pivot_zone(self) -> None:
        result = valid_result()
        result.pivot_zone = None
        with self.assertRaisesRegex(VisionSchemaError, "requires a pivot zone"):
            validate_and_snap_result(result, TRADING_DATES)

    def test_duplicate_anchors_rejected(self) -> None:
        result = valid_result()
        result.contraction_anchors = [
            VcpContractionAnchor(date="2025-01-07", evidence="a"),
            VcpContractionAnchor(date="2025-01-07", evidence="b"),
        ]
        with self.assertRaises(VisionSchemaError):
            validate_and_snap_result(result, TRADING_DATES)

    def test_invented_date_rejected(self) -> None:
        result = valid_result()
        result.contraction_anchors = [
            VcpContractionAnchor(date="2025-03-03", evidence="a"),
            VcpContractionAnchor(date="2025-03-04", evidence="b"),
        ]
        with self.assertRaises(VisionSchemaError):
            validate_and_snap_result(result, TRADING_DATES)

    def test_inverted_base_window_rejected(self) -> None:
        from app.services.vcp_vision import VcpBaseWindow

        result = valid_result()
        result.bases = [
            VcpBaseWindow(
                start="2025-01-10",
                end="2025-01-06",
                quality="solid",
                notes="",
            )
        ]
        with self.assertRaises(VisionSchemaError):
            validate_and_snap_result(result, TRADING_DATES)

    def test_inverted_pivot_zone_rejected(self) -> None:
        result = valid_result()
        result.pivot_zone = VcpPivotZone(
            start="2025-01-15",
            end="2025-01-13",
            rationale="Inverted.",
        )
        with self.assertRaises(VisionSchemaError):
            validate_and_snap_result(result, TRADING_DATES)

    def test_non_iso_date_rejected(self) -> None:
        result = valid_result()
        result.contraction_anchors = [
            VcpContractionAnchor(date="07-Jan-2025", evidence="a"),
            VcpContractionAnchor(date="09-Jan-2025", evidence="b"),
        ]
        with self.assertRaises(VisionSchemaError):
            validate_and_snap_result(result, TRADING_DATES)


class DeriveMetricsTests(unittest.TestCase):
    def test_pivot_price_from_zone_high(self) -> None:
        metrics = derive_contraction_metrics(
            candles_by_date=frozen_candles(),
            anchors=[dt.date(2025, 1, 7), dt.date(2025, 1, 9), dt.date(2025, 1, 13)],
            pivot_zone=(dt.date(2025, 1, 13), dt.date(2025, 1, 15)),
        )
        self.assertEqual(len(metrics["contractions"]), 2)
        self.assertIsNotNone(metrics["pivot_price"])

    def test_two_anchors_plus_later_pivot_derives_two_windows(self) -> None:
        metrics = derive_contraction_metrics(
            candles_by_date=frozen_candles(),
            anchors=[dt.date(2025, 1, 7), dt.date(2025, 1, 9)],
            pivot_zone=(dt.date(2025, 1, 13), dt.date(2025, 1, 15)),
        )
        self.assertEqual(len(metrics["contractions"]), 2)
        self.assertEqual(metrics["contractions"][1]["end"], "2025-01-13")

    def test_no_pivot_zone_returns_none(self) -> None:
        metrics = derive_contraction_metrics(
            candles_by_date=frozen_candles(),
            anchors=[dt.date(2025, 1, 7), dt.date(2025, 1, 9)],
            pivot_zone=None,
        )
        self.assertIsNone(metrics["pivot_price"])


if __name__ == "__main__":
    unittest.main()
