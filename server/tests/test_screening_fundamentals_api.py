import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import HTTPException

from app.routers.screening import get_fundamental_detail


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
            llm_flags={
                "summary": "Growth weakened materially.",
                "criteria": [
                    {
                        "name": "sales_growth",
                        "status": "negative",
                        "explanation": "Latest quarterly sales declined.",
                        "evidence_keys": ["growth.latest_quarter_revenue_yoy"],
                    }
                ],
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

        self.assertEqual(payload["annotation"]["verdict"], "fail")
        self.assertEqual(payload["snapshot"]["id"], str(snapshot_id))
        self.assertEqual(
            payload["annotation"]["criteria"][0]["evidence_keys"],
            ["growth.latest_quarter_revenue_yoy"],
        )
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
            llm_flags={
                "summary": "Fundamental annotation failed.",
                "error": {
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
        self.assertEqual(response.annotation.status, "failed")
        self.assertEqual(response.annotation.error.type, "FundamentalsAuthError")

    async def test_returns_model_failure_with_existing_snapshot(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        snapshot_id = uuid4()
        row = SimpleNamespace(
            id=uuid4(),
            scan_run_id=uuid4(),
            llm_status="failed",
            llm_verdict=None,
            llm_checked_at=now,
            llm_flags={
                "summary": "Model response validation failed.",
                "error": {
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

        self.assertEqual(response.annotation.status, "failed")
        self.assertEqual(response.annotation.error.type, "FundamentalLLMError")
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

    async def test_trigger_fundamental_pass_resets_and_enqueues(self) -> None:
        from app.routers.screening import trigger_fundamental_pass

        run_id = uuid4()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = run_id
        active_result = MagicMock()
        active_result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute.side_effect = [execute_result, active_result, MagicMock(), MagicMock()]

        redis_mock = AsyncMock()
        request = MagicMock()
        request.app.state.redis = redis_mock

        response = await trigger_fundamental_pass(run_id, request, db)

        self.assertEqual(response.status, "queued")
        self.assertEqual(response.scan_run_id, run_id)
        self.assertIn("enqueued successfully", response.message)

        # Verify db reset query was executed
        self.assertGreaterEqual(db.execute.call_count, 4)
        db.commit.assert_called_once()

        # Verify redis enqueue_job was called with run_fundamental_pass
        redis_mock.enqueue_job.assert_called_once()
        call_args = redis_mock.enqueue_job.call_args
        self.assertEqual(call_args[0][0], "run_fundamental_pass")
        self.assertEqual(call_args[0][1], str(run_id))

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


if __name__ == "__main__":
    unittest.main()
