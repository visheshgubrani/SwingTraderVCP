import datetime as dt
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import HTTPException

from app.routers.automation import (
    get_proposal_generation_chart,
    get_proposal_generation_results,
)


def mappings_one(value):
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = value
    return result


def mappings_all(values):
    result = MagicMock()
    result.mappings.return_value.all.return_value = values
    return result


class ProposalGenerationResultsApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.run_id = uuid4()
        self.scan_id = uuid4()
        self.attempt_id = uuid4()
        self.screening_id = uuid4()
        now = dt.datetime(2026, 8, 19, 4, 0, tzinfo=dt.timezone.utc)
        self.run = {
            "id": self.run_id,
            "scan_run_id": self.scan_id,
            "status": "completed",
            "candidates_total": 2,
            "candidates_processed": 2,
            "proposals_generated": 1,
            "proposals_rejected": 1,
            "proposals_uncertain": 0,
            "proposals_failed": 0,
            "error_message": None,
            "started_at": now,
            "completed_at": now,
        }
        self.attempt = {
            "id": self.attempt_id,
            "automation_run_id": self.run_id,
            "screening_result_id": self.screening_id,
            "instrument_id": uuid4(),
            "symbol": "NSE:EXAMPLE-EQ",
            "attempt_number": 1,
            "status": "invalid",
            "source_hash": "source",
            "renderer_version": "p10_mplfinance_v3",
            "prompt_version": "p10_vcp_proposal_v3",
            "schema_version": "gemini_vcp_proposal_output_v3",
            "geometry_version": "p10_geometry_rr_adjusted_chase_v4",
            "model": "google/gemini-3.7-flash",
            "risk_policy_version": 1,
            "context_image_hash": "context",
            "detail_image_hash": "detail",
            "provider_request_id": "request",
            "provider_usage": {},
            "provider_cost": 0,
            "structured_output": {"verdict": "valid", "confidence": 0.8},
            "error_type": "proposal_anchor_price_out_of_tolerance",
            "error_message": "contraction_low anchor on 2026-08-18 supplied 99; expected daily low 95; tolerance 1.0",
            "error_details": {
                "subreason": "outside_resistance_zone_tolerance",
            },
            "started_at": now,
            "completed_at": now,
        }

    async def test_returns_latest_attempt_per_candidate_and_counts(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [
            mappings_one(self.run),
            mappings_all([self.attempt]),
        ]

        response = await get_proposal_generation_results(self.run_id, db)

        self.assertEqual(response.status, "completed")
        self.assertEqual(response.proposals_rejected, 1)
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.results[0].error_type, "proposal_anchor_price_out_of_tolerance")
        self.assertEqual(
            response.results[0].error_details["subreason"],
            "outside_resistance_zone_tolerance",
        )
        attempts_sql = str(db.execute.await_args_list[1].args[0])
        self.assertIn("DISTINCT ON (screening_result_id)", attempts_sql)
        self.assertIn("attempt_number DESC", attempts_sql)
        self.assertIn("error_details", attempts_sql)

    async def test_missing_run_is_not_exposed(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_one(None)

        with self.assertRaises(HTTPException) as raised:
            await get_proposal_generation_results(self.run_id, db)

        self.assertEqual(raised.exception.status_code, 404)

    async def test_chart_is_scoped_to_run_and_attempt(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_one({"image": b"png-bytes"})

        response = await get_proposal_generation_chart(
            self.run_id, self.attempt_id, "detail", db
        )

        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(bytes(response.body), b"png-bytes")
        params = db.execute.await_args.args[1]
        self.assertEqual(params["automation_run_id"], self.run_id)
        self.assertEqual(params["attempt_id"], self.attempt_id)
        self.assertIn("detail_image", str(db.execute.await_args.args[0]))

    async def test_missing_chart_is_404(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_one({"image": None})

        with self.assertRaises(HTTPException) as raised:
            await get_proposal_generation_chart(
                self.run_id, self.attempt_id, "context", db
            )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_list_rejected_attempts(self) -> None:
        db = AsyncMock()
        attempt_row = dict(self.attempt)
        attempt_row["as_of_date"] = dt.date(2026, 8, 19)
        db.execute.return_value = mappings_all([attempt_row])

        from app.routers.automation import list_rejected_attempts

        results = await list_rejected_attempts(db, status_filter="invalid", symbol="EXAMPLE")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].symbol, "NSE:EXAMPLE-EQ")
        self.assertEqual(results[0].status, "invalid")
        self.assertEqual(results[0].error_type, "proposal_anchor_price_out_of_tolerance")

    async def test_get_proposal_attempt(self) -> None:
        db = AsyncMock()
        attempt_row = dict(self.attempt)
        attempt_row["as_of_date"] = dt.date(2026, 8, 19)
        db.execute.return_value = mappings_one(attempt_row)

        from app.routers.automation import get_proposal_attempt, get_attempt_chart_direct

        result = await get_proposal_attempt(self.attempt_id, db)
        self.assertEqual(result.id, self.attempt_id)
        self.assertEqual(result.symbol, "NSE:EXAMPLE-EQ")

        # Direct chart test
        db.execute.return_value = mappings_one({"image": b"direct-png-bytes"})
        chart_res = await get_attempt_chart_direct(self.attempt_id, "context", db)
        self.assertEqual(chart_res.media_type, "image/png")
        self.assertEqual(bytes(chart_res.body), b"direct-png-bytes")


if __name__ == "__main__":
    unittest.main()
