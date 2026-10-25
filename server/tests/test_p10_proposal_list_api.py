import datetime as dt
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.routers.automation import (
    _proposal_row_to_dict,
    _symbol_pattern,
    get_trade_proposal,
    list_proposal_batches,
    list_rejected_attempts,
    list_trade_proposals,
)
from app.services.p10_rollout import PAPER_AUTO_ARM_DECISION_NOTE


def mappings_all(values):
    result = MagicMock()
    result.mappings.return_value.all.return_value = values
    return result


def proposal_row(**overrides):
    row = {
        "id": uuid4(),
        "symbol": "NSE:RELIANCE-EQ",
        "status": "approved",
        "as_of_date": dt.date(2026, 10, 23),
        "decision": None,
        "decided_at": None,
        "decision_notes": None,
        "position_id": None,
        "position_state": None,
        "position_realized_pnl": None,
        "position_execution_mode": None,
        "position_opened_at": None,
        "position_closed_at": None,
        "position_quantity": None,
        "position_open_quantity": None,
        "position_average_entry_price": None,
    }
    row.update(overrides)
    return row


class ProposalRowShapeTests(unittest.TestCase):
    def test_nests_position_and_labels_operator_decision(self) -> None:
        position_id = uuid4()
        item = _proposal_row_to_dict(
            proposal_row(
                decision="approved",
                decided_at=dt.datetime(2026, 10, 24, 3, 0, tzinfo=dt.timezone.utc),
                decision_notes="looks tight",
                position_id=position_id,
                position_state="closed",
                position_realized_pnl=Decimal("1250.50"),
                position_execution_mode="paper",
            )
        )
        self.assertEqual(item["decision_source"], "operator")
        self.assertEqual(item["position"]["id"], position_id)
        self.assertEqual(item["position"]["state"], "closed")
        self.assertEqual(item["position"]["realized_pnl"], Decimal("1250.50"))
        self.assertNotIn("position_state", item)

    def test_paper_auto_arm_is_not_an_operator_decision(self) -> None:
        item = _proposal_row_to_dict(
            proposal_row(decision="approved", decision_notes=PAPER_AUTO_ARM_DECISION_NOTE)
        )
        self.assertEqual(item["decision_source"], "paper_auto")
        self.assertIsNone(item["position"])

    def test_undecided_has_no_source(self) -> None:
        item = _proposal_row_to_dict(proposal_row(status="pending_approval"))
        self.assertIsNone(item["decision_source"])

    def test_symbol_pattern_is_a_case_insensitive_escaped_substring(self) -> None:
        self.assertEqual(_symbol_pattern(" reli "), "%RELI%")
        self.assertEqual(_symbol_pattern("M_M%"), "%M\\_M\\%%")


class ListTradeProposalsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_by_substring_and_date_range_and_pages_in_sql(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [mappings_all([]), mappings_all([])]
        await list_trade_proposals(
            db,
            status_filter="all",
            symbol="reli",
            automation_run_id=None,
            as_of_date=None,
            from_date=dt.date(2026, 10, 1),
            to_date=dt.date(2026, 10, 31),
            entry_state=None,
            limit=25,
            offset=50,
        )
        sql = str(db.execute.await_args_list[0].args[0])
        params = db.execute.await_args_list[0].args[1]
        self.assertIn("tp.symbol ILIKE :symbol_pattern", sql)
        self.assertIn("tp.as_of_date >= :from_date", sql)
        self.assertIn("tp.as_of_date <= :to_date", sql)
        self.assertIn("LEFT JOIN proposal_decisions d", sql)
        self.assertIn("FROM positions p", sql)
        self.assertIn("LIMIT :limit OFFSET :offset", sql)
        self.assertNotIn("tp.status = :status", sql)
        self.assertEqual(params["symbol_pattern"], "%RELI%")
        self.assertEqual((params["limit"], params["offset"]), (25, 50))

    async def test_entry_state_filter_is_applied_before_paging(self) -> None:
        rows = [proposal_row(id=uuid4()) for _ in range(4)]
        db = AsyncMock()
        db.execute.side_effect = [mappings_all(rows), mappings_all([])]
        states = iter(["filled", "armed", "filled", "filled"])
        with patch(
            "app.routers.automation.derive_proposal_entry_state",
            side_effect=lambda legs, now: next(states),
        ):
            results = await list_trade_proposals(
                db,
                status_filter="approved",
                symbol=None,
                automation_run_id=None,
                as_of_date=None,
                from_date=None,
                to_date=None,
                entry_state="filled",
                limit=1,
                offset=1,
            )
        sql = str(db.execute.await_args_list[0].args[0])
        self.assertNotIn("LIMIT :limit", sql)
        # filled rows are 0, 2, 3 → offset 1, limit 1 → row 2
        self.assertEqual([r["id"] for r in results], [rows[2]["id"]])


class ProposalDetailApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_detail_includes_decision_and_position(self) -> None:
        proposal_id = uuid4()
        position_id = uuid4()
        prop_result = MagicMock()
        prop_result.fetchone.return_value = MagicMock(
            _mapping=proposal_row(
                id=proposal_id,
                decision="approved",
                decision_notes="ok",
                position_id=position_id,
                position_state="open",
            )
        )
        legs_result = MagicMock()
        legs_result.fetchall.return_value = []
        events_result = MagicMock()
        events_result.fetchall.return_value = []
        db = AsyncMock()
        db.execute.side_effect = [prop_result, legs_result, events_result]

        result = await get_trade_proposal(proposal_id, db)

        self.assertEqual(result["decision_source"], "operator")
        self.assertEqual(result["position"]["id"], position_id)
        self.assertEqual(result["legs"], [])
        self.assertIn("WHERE tp.id = :proposal_id", str(db.execute.await_args_list[0].args[0]))


class RejectedAttemptsAndBatchesApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_attempts_hide_existing_noops_and_page(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_all([])
        await list_rejected_attempts(
            db,
            status_filter="all",
            symbol="tcs",
            automation_run_id=None,
            include_existing=False,
            limit=20,
            offset=40,
        )
        sql = str(db.execute.await_args.args[0])
        params = db.execute.await_args.args[1]
        self.assertIn("'proposal_already_exists'", sql)
        self.assertIn("pa.symbol ILIKE :symbol_pattern", sql)
        self.assertIn("LIMIT :limit OFFSET :offset", sql)
        self.assertEqual(params["offset"], 40)

    async def test_rejected_attempts_can_include_existing(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_all([])
        await list_rejected_attempts(
            db,
            status_filter="all",
            symbol=None,
            automation_run_id=None,
            include_existing=True,
            limit=50,
            offset=0,
        )
        self.assertNotIn("proposal_already_exists", str(db.execute.await_args.args[0]))

    async def test_batches_accept_offset(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_all([])
        await list_proposal_batches(db, limit=10, offset=30)
        self.assertIn("OFFSET :offset", str(db.execute.await_args.args[0]))
        self.assertEqual(db.execute.await_args.args[1], {"limit": 10, "offset": 30})


if __name__ == "__main__":
    unittest.main()
