import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import HTTPException

from app.routers.screening import get_scan_results, list_scan_runs


def result_row(*, legacy: bool = False) -> SimpleNamespace:
    now = datetime.datetime.now(datetime.timezone.utc)
    technical_metrics = {
        "sma_150": 160.0,
        "sma_200_yesterday": 149.5,
        "sma_200_prev_22": 145.0,
        "sma_200_prev_110": 130.0,
        "high_52w": 220.0,
        "low_52w": 140.0,
        "rs_rating": 90,
        "adtv_crore": 22.0,
        "atr_10": 2.0,
        "atr_50": 2.1,
        "atr_ratio": 0.95,
        "atr_ratio_3m_low": 0.90,
        "atr_proximity_factor": 1.0556,
        "bb_width": 0.04,
        "bb_width_20th_pct": 0.05,
        "bb_width_percentile": 0.19,
        "avg_volume_10": 400_000,
        "avg_volume_50": 500_000,
        "volume_dry_up_ratio": 0.8,
        "criteria_matches": {"price_above_50_sma": True},
        "eligibility": {"minimum_history": True},
        "core_checks": {"price_above_50_sma": True},
        "fundamental_selected": True,
        "score": {
            "grade": "A",
            "components": {
                "relative_strength": {
                    "points": 20.0,
                    "max_points": 20.0,
                    "raw_value": 90,
                }
            },
        },
    }
    if legacy:
        technical_metrics = {
            "sma_150": 160.0,
            "rs_rating": 75,
            "criteria_matches": {},
        }
    return SimpleNamespace(
        id=uuid4(),
        result_rank=1,
        technical_score=None if legacy else 92.5,
        close_price=200.0,
        sma_50=180.0,
        sma_200=150.0,
        avg_volume_20=450_000,
        pct_from_52w_high=0.0909,
        technical_metrics=technical_metrics,
        llm_status="not_requested" if legacy else "queued",
        llm_verdict=None,
        llm_flags={},
        llm_checked_at=None,
        fundamental_snapshot_id=None,
        reviewer_status="pending",
        fundamentals_provider=None,
        fundamentals_statement_type=None,
        fundamentals_fetched_at=None,
        latest_annual_period=None,
        latest_quarterly_period=None,
        symbol="EXAMPLE",
        name="Example Limited",
        fyers_symbol="NSE:EXAMPLE-EQ",
        created_at=now,
    )


class ScreeningResultsApiTests(unittest.IsolatedAsyncioTestCase):
    async def load(self, row: SimpleNamespace):
        execute_result = MagicMock()
        execute_result.all.return_value = [row]
        db = AsyncMock()
        db.execute.return_value = execute_result
        return (await get_scan_results(str(uuid4()), db))[0]

    async def test_returns_v2_score_audit_fields(self) -> None:
        response = await self.load(result_row())

        self.assertEqual(response.technical_score, 92.5)
        self.assertEqual(response.score_grade, "A")
        self.assertEqual(
            response.score_components["relative_strength"]["points"],
            20.0,
        )
        self.assertEqual(response.bb_width_percentile, 0.19)
        self.assertTrue(response.fundamental_selected)

    async def test_legacy_result_keeps_nullable_score(self) -> None:
        response = await self.load(result_row(legacy=True))

        self.assertIsNone(response.technical_score)
        self.assertIsNone(response.score_grade)
        self.assertEqual(response.score_components, {})
        self.assertFalse(response.fundamental_selected)

    async def test_non_personal_run_is_not_exposed(self) -> None:
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute.return_value = execute_result

        with self.assertRaises(HTTPException) as raised:
            await get_scan_results(str(uuid4()), db)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(db.execute.await_count, 1)

    async def test_run_history_is_scoped_to_personal_visibility(self) -> None:
        execute_result = MagicMock()
        execute_result.all.return_value = []
        db = AsyncMock()
        db.execute.return_value = execute_result

        self.assertEqual(await list_scan_runs(db), [])

        statement = str(db.execute.await_args.args[0])
        self.assertIn("WHERE r.visibility = 'personal'", statement)


if __name__ == "__main__":
    unittest.main()
