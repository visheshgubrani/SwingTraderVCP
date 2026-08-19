import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.routers.automation import trigger_single_proposal
from app.routers.automation import settings as automation_settings


def mappings_one(value):
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = value
    return result


def scalar_one(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


class TriggerSingleProposalApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.result_id = uuid4()
        self.scan_id = uuid4()
        self.row = {
            "id": self.result_id,
            "scan_run_id": self.scan_id,
            "technical_passed": True,
            "result_rank": 3,
            "technical_metrics": {"fundamental_selected": True},
            "symbol": "NSE:EXAMPLE-EQ",
            "scan_status": "succeeded",
            "visibility": "personal",
            "triggered_by": "eod_scheduler",
            "as_of_date": dt.date(2026, 8, 18),
        }
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=object())))

    async def test_queues_a_shortlist_stock(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [scalar_one(False), mappings_one(self.row)]
        with patch(
            "app.routers.automation.enqueue_single_proposal",
            new=AsyncMock(return_value=True),
        ) as enqueue:
            response = await trigger_single_proposal(self.result_id, self.request, db)

        self.assertEqual(response.status, "queued")
        self.assertEqual(response.symbol, "NSE:EXAMPLE-EQ")
        self.assertEqual(response.scan_run_id, self.scan_id)
        enqueue.assert_awaited_once()

    async def test_rejects_when_processing_is_paused(self) -> None:
        db = AsyncMock()
        db.execute.return_value = scalar_one(True)
        with self.assertRaises(HTTPException) as raised:
            await trigger_single_proposal(self.result_id, self.request, db)
        self.assertEqual(raised.exception.status_code, 409)

    async def test_rejects_missing_result(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [scalar_one(False), mappings_one(None)]
        with self.assertRaises(HTTPException) as raised:
            await trigger_single_proposal(self.result_id, self.request, db)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_rejects_non_shortlist_when_p7_enabled(self) -> None:
        db = AsyncMock()
        row = dict(self.row)
        row["technical_metrics"] = {"fundamental_selected": False}
        db.execute.side_effect = [scalar_one(False), mappings_one(row)]
        with patch.object(automation_settings, "p7_fundamental_pass_enabled", True):
            with self.assertRaises(HTTPException) as raised:
                await trigger_single_proposal(self.result_id, self.request, db)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Top 20", raised.exception.detail)
