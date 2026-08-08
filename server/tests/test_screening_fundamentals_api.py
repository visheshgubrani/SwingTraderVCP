import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.routers.screening import get_fundamental_detail, get_fundamental_trace


class FundamentalDetailApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_normalized_snapshot_and_safe_ai_evidence(self) -> None:
        result_id = uuid4()
        scan_run_id = uuid4()
        snapshot_id = uuid4()
        now = datetime.datetime.now(datetime.timezone.utc)
        row = SimpleNamespace(
            id=result_id,
            scan_run_id=scan_run_id,
            llm_status="succeeded",
            llm_verdict="fail",
            llm_checked_at=now,
            fundamental_status="completed",
            fundamental_verdict=None,
            fundamental_scorecard={
                "rubric_version": "minervini_inspired_v2",
                "score": 42,
                "grade": "D",
                "coverage_pct": 70,
                "earned_points": 20,
                "available_points": 50,
                "max_points": 100,
                "components": [],
                "red_flags": ["sales_decline"],
                "provider_limitations": [],
                "insufficient_reason": None,
            },
            ai_status="succeeded",
            llm_flags={
                "summary": "Growth weakened materially.",
                "ai_opinion": {
                    "summary": "Growth weakened materially.",
                    "verdict_reference_ids": ["growth.latest_quarter_revenue_yoy"],
                },
                "red_flags": ["sales_decline"],
                "missing_data": ["quarterly_eps"],
                "model": {
                    "provider": "openrouter",
                    "name": "openai/gpt-5.6-luna-pro",
                    "reasoning_excluded": True,
                    "reasoning_details": "nested secret",
                },
                "reasoning_details": "must not be returned",
            },
            symbol="EXAMPLE",
            name="Example Limited",
            fyers_symbol="NSE:EXAMPLE-EQ",
            snapshot_id=snapshot_id,
            provider="upstox",
            statement_type="consolidated",
            fetched_at=now,
            latest_annual_period="Mar 2026",
            latest_quarterly_period="Jun 2026",
            normalized_facts={
                "company": {"symbol": "EXAMPLE"},
                "evidence": {
                    "growth.latest_quarter_revenue_yoy": {
                        "label": "Latest quarterly revenue YoY",
                        "value": {"value_pct": -12.5},
                        "unit": "percent",
                    }
                },
            },
        )
        execute_result = MagicMock()
        execute_result.one_or_none.return_value = row
        active_result = MagicMock()
        active_result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute.side_effect = [execute_result, active_result, MagicMock(), MagicMock()]

        response = await get_fundamental_detail(result_id, db)
        payload = response.model_dump(mode="json")

        self.assertEqual(payload["ai_opinion"]["verdict"], "fail")
        self.assertEqual(payload["snapshot"]["id"], str(snapshot_id))
        self.assertEqual(
            payload["ai_opinion"]["verdict_reference_ids"],
            ["growth.latest_quarter_revenue_yoy"],
        )
        self.assertEqual(payload["fundamental"]["assessment"]["grade"], "D")
        self.assertNotIn("raw_payload", str(payload))
        self.assertNotIn("reasoning_details", str(payload))

    async def test_returns_operational_failure_without_snapshot(self) -> None:
        result_id = uuid4()
        row = SimpleNamespace(
            id=result_id,
            scan_run_id=uuid4(),
            llm_status="failed",
            llm_verdict=None,
            llm_checked_at=None,
            fundamental_status="failed",
            fundamental_verdict=None,
            fundamental_scorecard={},
            ai_status="skipped",
            llm_flags={
                "summary": "Fundamental annotation failed.",
                "fundamental_error": {
                    "type": "FundamentalsAuthError",
                    "message": "Analytics token expired",
                },
            },
            symbol="FAILED",
            name=None,
            fyers_symbol="NSE:FAILED-EQ",
            snapshot_id=None,
            provider=None,
            statement_type=None,
            fetched_at=None,
            latest_annual_period=None,
            latest_quarterly_period=None,
            normalized_facts=None,
        )
        execute_result = MagicMock()
        execute_result.one_or_none.return_value = row
        db = AsyncMock()
        db.execute.return_value = execute_result

        response = await get_fundamental_detail(result_id, db)

        self.assertIsNone(response.snapshot)
        self.assertEqual(response.fundamental.status, "failed")
        self.assertIsNone(response.fundamental.error)
        self.assertEqual(response.ai_opinion.status, "skipped")

    async def test_returns_model_failure_with_existing_snapshot(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        snapshot_id = uuid4()
        row = SimpleNamespace(
            id=uuid4(),
            scan_run_id=uuid4(),
            llm_status="failed",
            llm_verdict=None,
            llm_checked_at=now,
            fundamental_status="completed",
            fundamental_verdict=None,
            fundamental_scorecard={},
            ai_status="failed",
            llm_flags={
                "summary": "Model response validation failed.",
                "ai_error": {
                    "type": "FundamentalLLMError",
                    "message": "Structured output was invalid",
                },
            },
            symbol="MODELFAIL",
            name="Model Failure Limited",
            fyers_symbol="NSE:MODELFAIL-EQ",
            snapshot_id=snapshot_id,
            provider="upstox",
            statement_type="consolidated",
            fetched_at=now,
            latest_annual_period="Mar 2026",
            latest_quarterly_period="Jun 2026",
            normalized_facts={"company": {"symbol": "MODELFAIL"}},
        )
        execute_result = MagicMock()
        execute_result.one_or_none.return_value = row
        db = AsyncMock()
        db.execute.return_value = execute_result

        response = await get_fundamental_detail(row.id, db)

        self.assertEqual(response.fundamental.status, "completed")
        self.assertEqual(response.ai_opinion.status, "failed")
        self.assertIsNone(response.ai_opinion.error)
        self.assertEqual(response.snapshot.id, snapshot_id)
        self.assertEqual(
            response.snapshot.normalized_facts["company"]["symbol"],
            "MODELFAIL",
        )

    async def test_unknown_result_returns_404(self) -> None:
        execute_result = MagicMock()
        execute_result.one_or_none.return_value = None
        db = AsyncMock()
        db.execute.return_value = execute_result

        with self.assertRaises(HTTPException) as context:
            await get_fundamental_detail(uuid4(), db)

        self.assertEqual(context.exception.status_code, 404)

    async def test_trace_returns_sanitized_attempt_timeline(self) -> None:
        result_id = uuid4()
        snapshot_id = uuid4()
        item_id = uuid4()
        attempt_id = uuid4()
        now = datetime.datetime.now(datetime.timezone.utc)
        row = SimpleNamespace(
            id=result_id,
            fundamental_scorecard={
                "rubric_version": "minervini_inspired_v2",
                "components": [],
            },
            llm_flags={"ai_error": {"type": "FundamentalLLMError"}},
            fundamental_status="completed",
            ai_status="failed",
            isin="INE257A01026",
            snapshot_id=snapshot_id,
            provider="upstox",
            statement_type="consolidated",
            fetched_at=now,
            content_hash="abc123",
            raw_payload={"company_profile": {"status": "success", "data": {}}},
            normalized_facts={"schema_version": "fundamental_facts_v3", "evidence": {}},
            analysis_item_id=item_id,
            analysis_key="analysis-key",
        )
        detail_result = MagicMock()
        detail_result.one_or_none.return_value = row
        attempt_result = MagicMock()
        attempt_result.mappings.return_value = [
            {
                "id": attempt_id,
                "analysis_item_id": item_id,
                "attempt_number": 1,
                "status": "invalid_response",
                "model": "model",
                "reasoning_effort": "low",
                "prompt_version": "fundamental_second_opinion_v1",
                "response_schema": "fundamental_second_opinion_v1",
                "input_hash": "hash",
                "request_payload": {"messages": []},
                "response_payload": {
                    "id": "request-id",
                    "reasoning_details": "secret",
                    "nested": {"reasoning_details": ["secret"]},
                },
                "http_status": 200,
                "request_id": "request-id",
                "usage": {"prompt_tokens": 10},
                "cost": 0.001,
                "error_code": "FundamentalLLMError",
                "error_message": "invalid",
                "started_at": now,
                "completed_at": now,
            }
        ]
        db = AsyncMock()
        db.execute.side_effect = [detail_result, attempt_result]

        response = await get_fundamental_trace(result_id, db)
        payload = response.model_dump(mode="json")

        self.assertTrue(payload["python_fit"]["contract_valid"])
        self.assertEqual(payload["ai_attempts"][0]["request_id"], "request-id")
        self.assertNotIn("reasoning_details", str(payload))
        self.assertEqual(payload["ai_request"], {"messages": []})
        self.assertEqual(
            payload["pipeline_errors"]["ai"]["type"],
            "FundamentalLLMError",
        )

    async def test_trigger_fundamental_pass_resets_and_enqueues(self) -> None:
        from app.routers.screening import trigger_fundamental_pass

        run_id = uuid4()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = run_id
        pause_result = MagicMock()
        pause_result.scalar_one_or_none.return_value = False
        active_result = MagicMock()
        active_result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute.side_effect = [
            execute_result,
            pause_result,
            active_result,
            MagicMock(),
            MagicMock(),
        ]

        redis_mock = AsyncMock()
        request = MagicMock()
        request.app.state.redis = redis_mock

        response = await trigger_fundamental_pass(run_id, request, db)

        self.assertEqual(response.status, "queued")
        self.assertEqual(response.scan_run_id, run_id)
        self.assertIn("enqueued successfully", response.message)

        # Verify db reset query was executed
        self.assertGreaterEqual(db.execute.call_count, 5)
        db.commit.assert_called_once()

        insert_params = db.execute.await_args_list[4].args[1]
        self.assertEqual(insert_params["run_id"], run_id)
        self.assertTrue(
            insert_params["queue_job_id"].startswith(
                f"fundamental-pass:{run_id}:"
            )
        )

        # The attempt-specific ID permits a later rerun of the same scan while
        # still correlating this database row with its exact Redis job.
        redis_mock.enqueue_job.assert_called_once()
        call_args = redis_mock.enqueue_job.call_args
        self.assertEqual(call_args[0][0], "run_fundamental_pass")
        self.assertEqual(call_args[0][1], str(run_id))
        self.assertEqual(
            call_args.kwargs["_job_id"], insert_params["queue_job_id"]
        )

    async def test_trigger_fundamental_pass_unknown_run_returns_404(self) -> None:
        from app.routers.screening import trigger_fundamental_pass

        run_id = uuid4()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute.return_value = execute_result

        request = MagicMock()

        with self.assertRaises(HTTPException) as context:
            await trigger_fundamental_pass(run_id, request, db)

        self.assertEqual(context.exception.status_code, 404)

    async def test_trigger_fundamental_pass_rejects_while_processing_is_paused(self) -> None:
        from app.routers.screening import trigger_fundamental_pass

        run_id = uuid4()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = run_id
        db = AsyncMock()
        db.execute.return_value = execute_result

        with (
            patch(
                "app.routers.screening.is_fundamental_control_paused",
                new=AsyncMock(return_value=True),
            ),
            self.assertRaises(HTTPException) as context,
        ):
            await trigger_fundamental_pass(run_id, MagicMock(), db)

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("paused", context.exception.detail)

    async def test_fundamental_progress_exposes_analysis_run_id(self) -> None:
        from app.routers.screening import get_fundamental_pass_progress

        analysis_run_id = uuid4()
        scan_run_id = uuid4()
        result = MagicMock()
        result.mappings.return_value.one_or_none.return_value = {
            "analysis_run_id": analysis_run_id,
            "scan_run_id": scan_run_id,
            "status": "partial",
            "counts": {"succeeded": 3, "failed": 1},
        }
        db = AsyncMock()
        db.execute.return_value = result

        response = await get_fundamental_pass_progress(scan_run_id, db)

        self.assertIsNotNone(response)
        self.assertEqual(response.analysis_run_id, analysis_run_id)
        self.assertEqual(response.counts, {"succeeded": 3, "failed": 1})
        query = str(db.execute.await_args.args[0])
        self.assertIn("r.id AS analysis_run_id", query)


if __name__ == "__main__":
    unittest.main()
